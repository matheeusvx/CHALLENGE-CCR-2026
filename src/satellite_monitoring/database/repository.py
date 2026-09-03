"""Leitura e gravacao do historico de analises.

Este modulo apenas persiste o que o motor de satelite ja decidiu. Nenhuma regra
de recomendacao e recalculada, reinterpretada ou sobrescrita aqui.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

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
) -> Analysis:
    """Grava (ou substitui) uma analise a partir da resposta da API."""

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

    session.execute(delete(Analysis).where(Analysis.id == analysis_id))

    analysis = Analysis(
        id=analysis_id,
        created_at=created_at or datetime.now(),
        status=str(payload.get("status") or "unknown"),
        decision=recommendation.get("decision"),
        confidence=recommendation.get("confidence"),
        summary=recommendation.get("summary"),
        experimental=bool(recommendation.get("experimental", True)),
        period_start=_as_date(period.get("start_date")),
        period_end=_as_date(period.get("end_date")),
        period_timezone=period.get("timezone"),
        period_strategy=period.get("strategy"),
        selected_area_m2=_as_float(payload.get("selected_area_m2")),
        effective_area_m2=_as_float(payload.get("effective_analysis_area_m2")),
        effective_area_pct=_as_float(payload.get("effective_analysis_pct")),
        analysis_quality_status=quality.get("status"),
        analysis_quality_score=_as_float(quality.get("score")),
        observation_count=_as_int(metrics.get("observation_count")),
        current_ndvi_mean=_as_float(metrics.get("current_ndvi_mean")),
        current_percentile=_as_float(metrics.get("current_percentile")),
        recent_trend=_as_float(metrics.get("recent_trend")),
        recent_trend_status=metrics.get("recent_trend_status"),
        geometry=dict(geometry) if geometry else None,
        centroid_longitude=longitude,
        centroid_latitude=latitude,
        nearest_km=nearest_km(session, longitude, latitude),
        run_directory=run_directory,
        artifacts=dict(artifacts) if artifacts else None,
        payload=dict(payload),
    )

    seen: set[date] = set()
    for record in payload.get("timeseries") or []:
        if not isinstance(record, Mapping):
            continue
        observed_on = _as_date(record.get("datetime") or record.get("date"))
        if observed_on is None or observed_on in seen:
            continue
        seen.add(observed_on)
        analysis.observations.append(
            AnalysisObservation(
                observed_on=observed_on,
                ndvi_mean=_as_float(record.get("ndvi_mean")),
                ndvi_median=_as_float(record.get("ndvi_median")),
                scene_quality_score=_as_float(record.get("scene_quality_score")),
                valid_pixel_percentage=_as_float(record.get("valid_pixel_percentage")),
            )
        )

    session.add(analysis)
    session.flush()
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


def delete_analysis(session: Session, analysis_id: str) -> bool:
    result = session.execute(delete(Analysis).where(Analysis.id == analysis_id))
    return bool(result.rowcount)
