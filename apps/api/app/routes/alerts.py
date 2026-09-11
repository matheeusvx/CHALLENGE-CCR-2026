"""Read/update-only operational alert API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from shapely.geometry import shape

from src.satellite_monitoring.database import (
    Alert,
    AlertEventRepository,
    AlertRepository,
    Analysis,
    session_scope,
)
from src.satellite_monitoring.database.alert_repository import (
    AlertVersionConflict,
    InvalidAlertTransition,
)

from ..dependencies import get_alert_now
from ..exceptions import ApiError
from ..schemas import (
    AlertAnalysisReference,
    AlertDetail,
    AlertEventResponse,
    AlertListItem,
    AlertMapTarget,
    AlertPage,
    AlertPatchRequest,
    AlertPatchResponse,
    AlertRoadMetadata,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

AlertStatusFilter = Literal["new", "seen", "monitoring", "resolved"]
AlertSeverityFilter = Literal["low", "medium", "high", "critical"]
AlertTypeFilter = Literal[
    "RECOMMENDATION_CHANGED",
    "CUT_PENDING",
    "REOBSERVATION_REQUIRED",
    "STALE_MONITORING",
    "SUPPORT_DIVERGENCE",
]


def _item(alert: Alert) -> AlertListItem:
    return AlertListItem(
        id=alert.id,
        type=alert.type,
        severity=alert.severity,
        status=alert.status,
        subject_kind=alert.subject_kind,
        subject_key=alert.subject_key,
        spatial_key=alert.spatial_key,
        road_id=alert.road_id,
        road_ref=alert.road_ref,
        road_name=alert.road_name,
        axis_id=alert.axis_id,
        section_id=alert.section_id,
        section_index=alert.section_index,
        analysis_id=alert.analysis_id,
        previous_analysis_id=alert.previous_analysis_id,
        last_analysis_id=alert.last_analysis_id,
        current_recommendation=alert.current_recommendation,
        previous_recommendation=alert.previous_recommendation,
        first_detected_at=alert.first_detected_at,
        last_seen_at=alert.last_seen_at,
        acknowledged_at=alert.acknowledged_at,
        resolved_at=alert.resolved_at,
        updated_at=alert.updated_at,
        version=alert.version,
        metadata=dict(alert.metadata_json or {}),
    )


def _analysis_reference(analysis: Analysis | None) -> AlertAnalysisReference | None:
    if analysis is None:
        return None
    return AlertAnalysisReference(
        analysis_id=analysis.id,
        created_at=analysis.created_at,
        status=analysis.status,
        decision=analysis.decision,
        confidence=analysis.confidence,
        latest_valid_observation_on=analysis.latest_valid_observation_on,
    )


def _map_target(alert: Alert, *analyses: Analysis | None) -> AlertMapTarget:
    persisted = next((item for item in analyses if item and item.geometry), None)
    geometry: dict[str, Any] | None = (
        dict(persisted.geometry) if persisted and persisted.geometry else None
    )
    bounds = None
    centroid = None
    if geometry is not None:
        try:
            geometry_shape = shape(geometry)
            west, south, east, north = geometry_shape.bounds
            bounds = {"west": west, "south": south, "east": east, "north": north}
            centroid = {
                "longitude": geometry_shape.centroid.x,
                "latitude": geometry_shape.centroid.y,
            }
        except (TypeError, ValueError):
            # Invalid legacy geometry remains visible, but does not leak an exception.
            bounds = None
    if persisted and persisted.centroid_longitude is not None and persisted.centroid_latitude is not None:
        centroid = {
            "longitude": persisted.centroid_longitude,
            "latitude": persisted.centroid_latitude,
        }
    return AlertMapTarget(
        geometry=geometry,
        bounds=bounds,
        centroid=centroid,
        road_ref=alert.road_ref,
        road_name=alert.road_name,
        section_id=alert.section_id,
    )


@router.get("", response_model=AlertPage)
def list_alerts(
    status: AlertStatusFilter | None = None,
    severity: AlertSeverityFilter | None = None,
    type: AlertTypeFilter | None = None,
    road: str | None = Query(None, min_length=1),
    section_id: str | None = Query(None, min_length=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AlertPage:
    with session_scope() as session:
        total, active_count, records = AlertRepository(session).list_filtered(
            status=status,
            severity=severity,
            alert_type=type,
            road=road,
            section_id=section_id,
            limit=limit,
            offset=offset,
        )
        return AlertPage(
            total=total,
            active_count=active_count,
            limit=limit,
            offset=offset,
            items=[_item(record) for record in records],
        )


@router.get("/{alert_id}", response_model=AlertDetail)
def get_alert_detail(alert_id: str) -> AlertDetail:
    with session_scope() as session:
        repository = AlertRepository(session)
        alert = repository.get(alert_id)
        if alert is None:
            raise ApiError("ALERT_NOT_FOUND", "Alerta nao encontrado.", status_code=404)
        origin = repository.get_analysis(alert.analysis_id)
        previous = repository.get_analysis(alert.previous_analysis_id)
        latest = repository.get_analysis(alert.last_analysis_id)
        origin_reference = _analysis_reference(origin)
        if origin_reference is None:  # protected by the database FK
            raise ApiError("ALERT_NOT_FOUND", "Alerta nao encontrado.", status_code=404)
        events = AlertEventRepository(session).list_for_alert(alert.id)
        item = _item(alert).model_dump()
        return AlertDetail(
            **item,
            timeline=[
                AlertEventResponse(
                    id=event.id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    analysis_id=event.analysis_id,
                    previous_status=event.previous_status,
                    new_status=event.new_status,
                    severity=event.severity,
                    metadata=dict(event.metadata_json or {}),
                )
                for event in events
            ],
            origin_analysis=origin_reference,
            previous_analysis=_analysis_reference(previous),
            latest_analysis=_analysis_reference(latest),
            road_metadata=AlertRoadMetadata(
                road_id=alert.road_id,
                road_ref=alert.road_ref,
                road_name=alert.road_name,
                axis_id=alert.axis_id,
                section_id=alert.section_id,
                section_index=alert.section_index,
                spatial_key=alert.spatial_key,
            ),
            map_target=_map_target(alert, latest, origin, previous),
        )


@router.patch("/{alert_id}", response_model=AlertPatchResponse)
def patch_alert(
    alert_id: str,
    payload: AlertPatchRequest,
    now: datetime = Depends(get_alert_now),
) -> AlertPatchResponse:
    with session_scope() as session:
        repository = AlertRepository(session)
        try:
            alert = repository.update_status(
                alert_id,
                expected_version=payload.version,
                new_status=payload.status,
                now=now.replace(tzinfo=None),
            )
        except AlertVersionConflict as exc:
            raise ApiError(
                "ALERT_VERSION_CONFLICT",
                "O alerta foi atualizado por outro operador.",
                status_code=409,
            ) from exc
        except InvalidAlertTransition as exc:
            raise ApiError(
                "INVALID_ALERT_TRANSITION",
                "A transicao de status solicitada nao e permitida.",
                status_code=422,
            ) from exc
        if alert is None:
            raise ApiError("ALERT_NOT_FOUND", "Alerta nao encontrado.", status_code=404)
        return AlertPatchResponse(**_item(alert).model_dump())
