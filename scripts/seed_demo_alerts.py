"""Create deterministic, manually-triggered demonstration data for Alertas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from shapely.geometry import box, mapping

from scripts.clear_demo_alerts import clear_demo_data
from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    AlertEventRepository,
    AlertRepository,
    Analysis,
    AnalysisIdentity,
    MonitoredSectionRepository,
    init_database,
    session_scope,
)
from src.satellite_monitoring.road_geometry import LocalGeoJsonRoadGeometryProvider

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROAD_DATASET = PROJECT_ROOT / "data" / "roads" / "processed" / "motiva-sp-roads-state.geojson"
DEMO_NAMESPACE = "https://motiva.example/demo-alerts/v1/"


@dataclass(frozen=True)
class DemoLocation:
    subject_key: str
    spatial_key: str
    road_id: str
    road_ref: str
    road_name: str
    axis_id: str
    section_id: str
    section_index: int
    geometry: dict[str, Any]
    longitude: float
    latitude: float


@dataclass(frozen=True)
class AnalysisSpec:
    name: str
    location: DemoLocation
    created_at: datetime
    decision: str
    observed_on: date | None
    support_agreement: str | None = None


def _uuid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, DEMO_NAMESPACE + name))


def _locations() -> list[DemoLocation]:
    provider = LocalGeoJsonRoadGeometryProvider(ROAD_DATASET)
    axes_by_ref = {
        road_ref: [
            axis
            for axis in provider.axes
            if axis.eligible and axis.road_ref == road_ref
        ]
        for road_ref in ("SP-330", "SP-348")
    }
    if any(not axes for axes in axes_by_ref.values()):
        raise RuntimeError("SP-330/SP-348 road geometry is unavailable for demo data")

    names = {"SP-330": "Rodovia Anhanguera", "SP-348": "Rodovia dos Bandeirantes"}
    locations: list[DemoLocation] = []
    for index in range(6):
        road_ref = "SP-330" if index % 2 == 0 else "SP-348"
        axes = axes_by_ref[road_ref]
        axis = axes[(index // 2) % len(axes)]
        point = axis.geometry.interpolate((0.25 + 0.2 * (index % 3)), normalized=True)
        # A small valid presentation polygon centered on the real persisted axis.
        # It is demo-only and is never passed to the scientific pipeline.
        geometry = mapping(box(point.x - 0.0012, point.y - 0.0008,
                               point.x + 0.0012, point.y + 0.0008))
        section_id = f"demo_section_{index + 1:03d}"
        spatial_key = f"roadside:v1:demo:{road_ref}:{section_id}"
        locations.append(DemoLocation(
            subject_key=spatial_key,
            spatial_key=spatial_key,
            road_id=axis.road_id or road_ref,
            road_ref=road_ref,
            road_name=axis.road_name or names[road_ref],
            axis_id=axis.axis_id,
            section_id=section_id,
            section_index=index,
            geometry=geometry,
            longitude=float(point.x),
            latitude=float(point.y),
        ))
    return locations


def _analysis(session, spec: AnalysisSpec) -> Analysis:
    analysis = Analysis(
        id=_uuid("analysis/" + spec.name),
        created_at=spec.created_at,
        status="completed",
        decision=spec.decision,
        confidence="medium",
        summary="Registro sintético criado exclusivamente para demonstração.",
        experimental=True,
        geometry=spec.location.geometry,
        centroid_longitude=spec.location.longitude,
        centroid_latitude=spec.location.latitude,
        subject_kind="road_section",
        subject_key=spec.location.subject_key,
        spatial_key=spec.location.spatial_key,
        road_id=spec.location.road_id,
        road_ref=spec.location.road_ref,
        road_name=spec.location.road_name,
        axis_id=spec.location.axis_id,
        section_id=spec.location.section_id,
        section_index=spec.location.section_index,
        latest_valid_observation_on=spec.observed_on,
        payload={
            "analysis_id": _uuid("analysis/" + spec.name),
            "status": "completed",
            "analysis_period": {
                "start_date": (spec.created_at - timedelta(days=30)).date().isoformat(),
                "end_date": spec.created_at.date().isoformat(),
                "timezone": "America/Sao_Paulo",
                "strategy": "previous_calendar_month",
            },
            "recommendation": {
                "decision": spec.decision,
                "confidence": "medium",
                "experimental": True,
                "summary": "Cenário operacional preparado para demonstração.",
                "reasons": [],
                "blocking_reasons": [],
                "limitations": ["Registro exclusivo para demonstração."],
                "metrics": {"observation_count": 1 if spec.observed_on else 0},
            },
            "selected_area_m2": 35000.0,
            "aoi": {
                "source": "demo_seed",
                "area_square_meters": 35000.0,
                "centroid": {
                    "longitude": spec.location.longitude,
                    "latitude": spec.location.latitude,
                },
            },
            "summary": {"analysis_quality": {"status": "medium", "score": 75}},
            "timeseries": [],
            "scenes": [],
            "artifacts": {},
            "warnings": [],
            "errors": [],
            "analysis_trigger": "automatic_viewport",
            "metadata": {"demo_data": True, "demo_scenario": spec.name},
            "decision_support": (
                {"status": "available", "agreement": spec.support_agreement}
                if spec.support_agreement else None
            ),
        },
    )
    session.add(analysis)
    session.flush()
    return analysis


def _register_section(session, location: DemoLocation, analysis: Analysis) -> None:
    identity = AnalysisIdentity(
        subject_kind="road_section",
        subject_key=location.subject_key,
        spatial_key=location.spatial_key,
        road_id=location.road_id,
        road_ref=location.road_ref,
        road_name=location.road_name,
        axis_id=location.axis_id,
        section_id=location.section_id,
        section_index=location.section_index,
    )
    MonitoredSectionRepository(session).upsert(
        identity,
        geometry=location.geometry,
        source="demo_seed",
        analysis_at=analysis.created_at,
        latest_valid_observation_on=analysis.latest_valid_observation_on,
        cadence_days=7,
        metadata={"demo_data": True, "demo_scenario": location.section_id},
    )


def _event(
    session,
    alert: Alert,
    event_type: str,
    at: datetime,
    *,
    analysis_id: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    severity: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    AlertEventRepository(session).add(AlertEvent(
        alert_id=alert.id,
        event_type=event_type,
        occurred_at=at,
        analysis_id=analysis_id or alert.last_analysis_id,
        previous_status=previous_status,
        new_status=new_status,
        severity=severity or alert.severity,
        metadata_json={"demo_data": True, **(extra or {})},
    ))


def _alert(
    session,
    *,
    name: str,
    alert_type: str,
    severity: str,
    status: str,
    origin: Analysis,
    previous: Analysis | None,
    latest: Analysis,
    first_detected_at: datetime,
    metadata: dict[str, Any],
) -> Alert:
    location = DemoLocation(
        origin.subject_key, origin.spatial_key, origin.road_id, origin.road_ref,
        origin.road_name, origin.axis_id, origin.section_id,
        origin.section_index, origin.geometry, origin.centroid_longitude,
        origin.centroid_latitude,
    )
    alert = Alert(
        id=_uuid("alert/" + name),
        type=alert_type,
        severity=severity,
        status=status,
        subject_kind="road_section",
        subject_key=location.subject_key,
        spatial_key=location.spatial_key,
        road_id=location.road_id,
        road_ref=location.road_ref,
        road_name=location.road_name,
        axis_id=location.axis_id,
        section_id=location.section_id,
        section_index=location.section_index,
        analysis_id=origin.id,
        previous_analysis_id=previous.id if previous else None,
        last_analysis_id=latest.id,
        current_recommendation=latest.decision,
        previous_recommendation=previous.decision if previous else None,
        first_detected_at=first_detected_at,
        last_seen_at=latest.created_at,
        acknowledged_at=latest.created_at if status in {"seen", "monitoring", "resolved"} else None,
        resolved_at=latest.created_at if status == "resolved" else None,
        updated_at=latest.created_at,
        open_key=None if status == "resolved" else f"demo:{name}:open",
        version=1,
        metadata_json={"demo_data": True, "demo_scenario": name, **metadata},
    )
    AlertRepository(session).add(alert)
    return alert


def seed_demo_alerts(*, now: datetime | None = None) -> dict[str, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    locations = _locations()
    with session_scope() as session:
        clear_demo_data(session)

        # 1. Recommendation changed: NAO_CORTAR -> CORTAR.
        rec_previous = _analysis(session, AnalysisSpec(
            "recommendation/previous", locations[0], current - timedelta(days=4),
            "nao_cortar", (current - timedelta(days=4)).date(),
        ))
        rec_current = _analysis(session, AnalysisSpec(
            "recommendation/current", locations[0], current - timedelta(hours=3),
            "cortar", current.date(),
        ))
        _register_section(session, locations[0], rec_current)
        changed = _alert(
            session, name="recommendation_changed", alert_type="RECOMMENDATION_CHANGED",
            severity="high", status="new", origin=rec_current,
            previous=rec_previous, latest=rec_current,
            first_detected_at=rec_current.created_at,
            metadata={"transition": "nao_cortar_to_cortar"},
        )
        _event(session, changed, "created", changed.first_detected_at,
               analysis_id=rec_current.id, new_status="new")

        # 2. Consecutive CORTAR sequence; explicitly not a mowing-date estimate.
        cut_start = _analysis(session, AnalysisSpec(
            "cut_pending/start", locations[1], current - timedelta(days=9),
            "cortar", (current - timedelta(days=9)).date(),
        ))
        cut_latest = _analysis(session, AnalysisSpec(
            "cut_pending/latest", locations[1], current - timedelta(hours=2),
            "cortar", current.date(),
        ))
        _register_section(session, locations[1], cut_latest)
        pending = _alert(
            session, name="cut_pending", alert_type="CUT_PENDING", severity="high",
            status="monitoring", origin=cut_start, previous=None, latest=cut_latest,
            first_detected_at=current - timedelta(days=2),
            metadata={
                "cut_pending_days": 9,
                "threshold_days": 7,
                "consecutive_cortar_since": (current - timedelta(days=9)).date().isoformat(),
                "not_a_mowing_date_estimate": True,
            },
        )
        pending.acknowledged_at = current - timedelta(days=1, hours=18)
        pending.version = 2
        _event(session, pending, "created", current - timedelta(days=2),
               analysis_id=cut_start.id, new_status="new", severity="medium")
        _event(session, pending, "status_changed", current - timedelta(days=1, hours=18),
               previous_status="new", new_status="monitoring")
        _event(session, pending, "condition_persisted", cut_latest.created_at,
               analysis_id=cut_latest.id)
        _event(session, pending, "severity_changed", current - timedelta(hours=2),
               analysis_id=cut_latest.id,
               extra={"previous_severity": "medium", "reason": "demo_priority"})

        # 3. Inconclusive after a valid prior decision.
        reobs_previous = _analysis(session, AnalysisSpec(
            "reobservation/previous", locations[2], current - timedelta(days=6),
            "nao_cortar", (current - timedelta(days=6)).date(),
        ))
        reobs_current = _analysis(session, AnalysisSpec(
            "reobservation/current", locations[2], current - timedelta(hours=8),
            "inconclusivo", None,
        ))
        _register_section(session, locations[2], reobs_current)
        reobservation = _alert(
            session, name="reobservation_required", alert_type="REOBSERVATION_REQUIRED",
            severity="medium", status="new", origin=reobs_current,
            previous=reobs_previous, latest=reobs_current,
            first_detected_at=reobs_current.created_at,
            metadata={"last_valid_recommendation": "nao_cortar"},
        )
        _event(session, reobservation, "created", reobs_current.created_at,
               analysis_id=reobs_current.id, new_status="new")

        # 4. 36 days without a valid observation, already seen by an operator.
        stale_analysis = _analysis(session, AnalysisSpec(
            "stale/latest", locations[3], current - timedelta(days=36),
            "nao_cortar", (current - timedelta(days=36)).date(),
        ))
        _register_section(session, locations[3], stale_analysis)
        stale = _alert(
            session, name="stale_monitoring", alert_type="STALE_MONITORING",
            severity="medium", status="seen", origin=stale_analysis,
            previous=None, latest=stale_analysis,
            first_detected_at=current - timedelta(days=6),
            metadata={
                "stale_days": 36,
                "threshold_days": 30,
                "last_valid_observation_on": (current - timedelta(days=36)).date().isoformat(),
            },
        )
        stale.last_seen_at = current - timedelta(hours=5)
        stale.updated_at = stale.last_seen_at
        stale.acknowledged_at = stale.last_seen_at
        stale.version = 2
        _event(session, stale, "created", stale.first_detected_at,
               analysis_id=stale_analysis.id, new_status="new")
        _event(session, stale, "status_changed", stale.last_seen_at,
               previous_status="new", new_status="seen")

        # 5. Auxiliary support divergence; satellite recommendation remains primary.
        divergence_analysis = _analysis(session, AnalysisSpec(
            "support_divergence/latest", locations[4], current - timedelta(hours=5),
            "cortar", current.date(), support_agreement="diverge",
        ))
        _register_section(session, locations[4], divergence_analysis)
        divergence = _alert(
            session, name="support_divergence", alert_type="SUPPORT_DIVERGENCE",
            severity="low", status="new", origin=divergence_analysis,
            previous=None, latest=divergence_analysis,
            first_detected_at=divergence_analysis.created_at,
            metadata={"support_status": "available", "agreement": "diverge"},
        )
        _event(session, divergence, "created", divergence.first_detected_at,
               analysis_id=divergence_analysis.id, new_status="new")

        # 6. Fully resolved lifecycle.
        resolved_origin = _analysis(session, AnalysisSpec(
            "resolved/origin", locations[5], current - timedelta(days=18),
            "cortar", (current - timedelta(days=18)).date(),
        ))
        resolved_latest = _analysis(session, AnalysisSpec(
            "resolved/latest", locations[5], current - timedelta(days=1),
            "nao_cortar", (current - timedelta(days=1)).date(),
        ))
        _register_section(session, locations[5], resolved_latest)
        resolved = _alert(
            session, name="resolved_lifecycle", alert_type="CUT_PENDING",
            severity="medium", status="resolved", origin=resolved_origin,
            previous=resolved_origin, latest=resolved_latest,
            first_detected_at=current - timedelta(days=11),
            metadata={"resolution_reason": "recommendation_no_longer_cortar",
                      "not_a_mowing_date_estimate": True},
        )
        resolved.acknowledged_at = current - timedelta(days=10)
        resolved.version = 4
        _event(session, resolved, "created", current - timedelta(days=11),
               analysis_id=resolved_origin.id, new_status="new")
        _event(session, resolved, "status_changed", current - timedelta(days=10),
               previous_status="new", new_status="seen")
        _event(session, resolved, "condition_persisted", current - timedelta(days=8),
               analysis_id=resolved_origin.id)
        _event(session, resolved, "severity_changed", current - timedelta(days=7),
               analysis_id=resolved_origin.id,
               extra={"previous_severity": "medium"})
        _event(session, resolved, "status_changed", current - timedelta(days=6),
               previous_status="seen", new_status="monitoring")
        _event(session, resolved, "auto_resolved", resolved_latest.created_at,
               analysis_id=resolved_latest.id,
               previous_status="monitoring", new_status="resolved",
               extra={"reason": "recommendation_no_longer_cortar"})

        alerts = session.query(Alert).filter(Alert.id.in_([
            changed.id, pending.id, reobservation.id, stale.id,
            divergence.id, resolved.id,
        ])).all()
        summary = {
            "active": sum(item.status != "resolved" for item in alerts),
            "new": sum(item.status == "new" for item in alerts),
            "monitoring": sum(item.status == "monitoring" for item in alerts),
            "resolved": sum(item.status == "resolved" for item in alerts),
            "total": len(alerts),
        }
    return summary


def main() -> int:
    init_database()
    summary = seed_demo_alerts()
    print("Demo alerts created:")
    for name in ("active", "new", "monitoring", "resolved", "total"):
        print(f"- {name}: {summary[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
