"""Leitura e gravacao do historico de analises.

Este modulo apenas persiste o que o motor de satelite ja decidiu. Nenhuma regra
de recomendacao e recalculada, reinterpretada ou sobrescrita aqui.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from .alert_repository import MonitoredSectionRepository
from .identity import AnalysisIdentity, geometry_identity
from .models import Analysis, AnalysisObservation, KmMarker


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _haversine_km(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    radius = 6371.0088
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    inner = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(inner))


def nearest_km(session: Session, longitude: float | None, latitude: float | None) -> int | None:
    """Marco quilometrico mais proximo do centroide informado."""

    if longitude is None or latitude is None:
        return None
    markers = session.execute(select(KmMarker)).scalars().all()
    if not markers:
        return None
    closest = min(
        markers,
        key=lambda marker: _haversine_km(
            longitude, latitude, marker.longitude, marker.latitude
        ),
    )
    return closest.km


def save_analysis(
    session: Session,
    payload: Mapping[str, Any],
    *,
    geometry: Mapping[str, Any] | None = None,
    run_directory: str | None = None,
    artifacts: Mapping[str, str] | None = None,
    created_at: datetime | None = None,
    identity: AnalysisIdentity | None = None,
    monitored_section_cadence_days: int | None = None,
    alert_now: datetime | None = None,
) -> Analysis:
    """Insert or update an analysis without replacing its database row."""

    analysis_id = str(payload.get("analysis_id") or "").strip()
    if not analysis_id:
        raise ValueError("payload sem analysis_id")

    recommendation: Mapping[str, Any] = payload.get("recommendation") or {}
    metrics: Mapping[str, Any] = recommendation.get("metrics") or {}
    period: Mapping[str, Any] = payload.get("analysis_period") or {}
    summary: Mapping[str, Any] = payload.get("summary") or {}
    quality = summary.get("analysis_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    aoi: Mapping[str, Any] = payload.get("aoi") or {}
    centroid = aoi.get("centroid")
    centroid = centroid if isinstance(centroid, Mapping) else {}

    longitude = _as_float(centroid.get("longitude") or aoi.get("centroid_longitude"))
    latitude = _as_float(centroid.get("latitude") or aoi.get("centroid_latitude"))

    if identity is None and geometry is not None:
        identity = geometry_identity(geometry)
    closest_km = nearest_km(session, longitude, latitude)
    effective_created_at = created_at or datetime.now()
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        analysis = Analysis(id=analysis_id, created_at=effective_created_at)
        session.add(analysis)

    values = {
        "status": str(payload.get("status") or "unknown"),
        "decision": recommendation.get("decision"),
        "confidence": recommendation.get("confidence"),
        "summary": recommendation.get("summary"),
        "experimental": bool(recommendation.get("experimental", True)),
        "period_start": _as_date(period.get("start_date")),
        "period_end": _as_date(period.get("end_date")),
        "period_timezone": period.get("timezone"),
        "period_strategy": period.get("strategy"),
        "selected_area_m2": _as_float(payload.get("selected_area_m2")),
        "effective_area_m2": _as_float(payload.get("effective_analysis_area_m2")),
        "effective_area_pct": _as_float(payload.get("effective_analysis_pct")),
        "analysis_quality_status": quality.get("status"),
        "analysis_quality_score": _as_float(quality.get("score")),
        "observation_count": _as_int(metrics.get("observation_count")),
        "current_ndvi_mean": _as_float(metrics.get("current_ndvi_mean")),
        "current_percentile": _as_float(metrics.get("current_percentile")),
        "recent_trend": _as_float(metrics.get("recent_trend")),
        "recent_trend_status": metrics.get("recent_trend_status"),
        "geometry": dict(geometry) if geometry else None,
        "centroid_longitude": longitude,
        "centroid_latitude": latitude,
        "nearest_km": closest_km,
        "run_directory": run_directory,
        "artifacts": dict(artifacts) if artifacts else None,
        "payload": dict(payload),
    }
    if identity is not None:
        values.update(
            subject_kind=identity.subject_kind,
            subject_key=identity.subject_key,
            spatial_key=identity.spatial_key,
            road_id=identity.road_id,
            road_ref=identity.road_ref,
            road_name=identity.road_name,
            axis_id=identity.axis_id,
            section_id=identity.section_id,
            section_index=identity.section_index,
        )
    for field, value in values.items():
        setattr(analysis, field, value)

    observations_by_date: dict[date, Mapping[str, Any]] = {}
    for record in payload.get("timeseries") or []:
        if not isinstance(record, Mapping):
            continue
        observed_on = _as_date(record.get("datetime") or record.get("date"))
        if observed_on is None or observed_on in observations_by_date:
            continue
        observations_by_date[observed_on] = record

    existing_by_date = {item.observed_on: item for item in analysis.observations}
    for observed_on, record in observations_by_date.items():
        observation = existing_by_date.pop(observed_on, None)
        if observation is None:
            observation = AnalysisObservation(observed_on=observed_on)
            analysis.observations.append(observation)
        observation.ndvi_mean = _as_float(record.get("ndvi_mean"))
        observation.ndvi_median = _as_float(record.get("ndvi_median"))
        observation.scene_quality_score = _as_float(record.get("scene_quality_score"))
        observation.valid_pixel_percentage = _as_float(
            record.get("valid_pixel_percentage")
        )
    for obsolete in existing_by_date.values():
        analysis.observations.remove(obsolete)

    latest_valid = max(observations_by_date, default=None)
    analysis.latest_valid_observation_on = latest_valid
    session.flush()
    if identity is not None:
        source = (
            "automatic"
            if payload.get("analysis_trigger") == "automatic_viewport"
            else "manual"
        )
        if monitored_section_cadence_days is None:
            from .monitoring import MonitoringConfig

            monitored_section_cadence_days = MonitoringConfig.from_env().default_cadence_days
        MonitoredSectionRepository(session).upsert(
            identity,
            geometry=geometry,
            source=source,
            analysis_at=analysis.created_at,
            latest_valid_observation_on=latest_valid,
            cadence_days=monitored_section_cadence_days,
        )
        # Local import avoids coupling the persistence modules at import time.
        # Evaluation shares this transaction with the analysis and section upsert.
        from .alert_engine import AlertEngine

        AlertEngine(session).evaluate_analysis(
            analysis, now=alert_now or analysis.created_at
        )
    return analysis


def get_analysis(session: Session, analysis_id: str) -> Analysis | None:
    statement = (
        select(Analysis)
        .options(selectinload(Analysis.observations))
        .where(Analysis.id == analysis_id)
    )
    return session.execute(statement).scalars().first()


def list_analyses(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    decision: str | None = None,
) -> Sequence[Analysis]:
    statement = select(Analysis).order_by(Analysis.created_at.desc())
    if decision:
        statement = statement.where(Analysis.decision == decision)
    statement = statement.offset(max(0, offset)).limit(max(1, limit))
    return session.execute(statement).scalars().all()


def count_analyses(session: Session, *, decision: str | None = None) -> int:
    statement = select(func.count()).select_from(Analysis)
    if decision:
        statement = statement.where(Analysis.decision == decision)
    return int(session.execute(statement).scalar_one())


def list_history_analyses(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    decision: str | None = None,
) -> Sequence[Analysis]:
    """List only analyses visible in the operator-facing History UI."""

    statement = (
        select(Analysis)
        .where(Analysis.hidden_from_history_at.is_(None))
        .order_by(Analysis.created_at.desc())
    )
    if decision:
        statement = statement.where(Analysis.decision == decision)
    statement = statement.offset(max(0, offset)).limit(max(1, limit))
    return session.execute(statement).scalars().all()


def count_history_analyses(session: Session, *, decision: str | None = None) -> int:
    """Count visible UI history without changing scientific queries."""

    statement = (
        select(func.count())
        .select_from(Analysis)
        .where(Analysis.hidden_from_history_at.is_(None))
    )
    if decision:
        statement = statement.where(Analysis.decision == decision)
    return int(session.execute(statement).scalar_one())


def hide_analysis_from_history(
    session: Session, analysis_id: str, *, hidden_at: datetime
) -> bool:
    """Soft-hide one analysis; return false only when the row does not exist."""

    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        return False
    if analysis.hidden_from_history_at is None:
        analysis.hidden_from_history_at = hidden_at
        session.flush()
    return True


def hide_all_history_analyses(session: Session, *, hidden_at: datetime) -> int:
    """Soft-hide every currently visible analysis and return the affected count."""

    result = session.execute(
        update(Analysis)
        .where(Analysis.hidden_from_history_at.is_(None))
        .values(hidden_from_history_at=hidden_at)
    )
    return int(result.rowcount or 0)


def delete_analysis(session: Session, analysis_id: str) -> bool:
    result = session.execute(delete(Analysis).where(Analysis.id == analysis_id))
    return bool(result.rowcount)
