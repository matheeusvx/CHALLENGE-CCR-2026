"""Diagnostico deterministico de consistencia e qualidade da serie temporal."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, median
from typing import Any, Sequence


@dataclass(frozen=True)
class TemporalSeriesResult:
    raw_daily_timeseries: list[dict[str, Any]]
    analysis_timeseries: list[dict[str, Any]]
    outliers: list[dict[str, Any]]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _quality_score(record: dict[str, Any]) -> float:
    return _number(
        record.get("scene_quality_score"),
        _number(record.get("valid_pixel_percentage")),
    )


def diagnose_temporal_consistency(
    records: Sequence[dict[str, Any]],
    *,
    min_deviation: float,
    mad_multiplier: float,
    return_ratio: float,
) -> TemporalSeriesResult:
    """Marca reversoes isoladas de menor qualidade sem remover quedas persistentes."""
    rows = sorted((dict(record) for record in records), key=lambda row: _datetime(row["datetime"]))
    values = [_number(row.get("ndvi_mean")) for row in rows]
    outliers: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        previous_difference = values[index] - values[index - 1] if index > 0 else None
        next_difference = values[index + 1] - values[index] if index + 1 < len(rows) else None
        local_values = values[max(0, index - 1) : min(len(rows), index + 2)]
        local_median = float(median(local_values))
        local_mad = float(median(abs(value - local_median) for value in local_values))
        deviation = values[index] - local_median
        reasons: list[str] = []

        if 0 < index < len(rows) - 1 and previous_difference is not None:
            neighbors_returned = abs(values[index + 1] - values[index - 1]) <= (
                abs(previous_difference) * return_ratio
            )
            deviation_threshold = max(min_deviation, mad_multiplier * local_mad)
            lower_quality = _quality_score(row) < min(
                _quality_score(rows[index - 1]),
                _quality_score(rows[index + 1]),
            )
            if abs(deviation) >= deviation_threshold and neighbors_returned and lower_quality:
                reasons.append(
                    "isolated_temporal_drop"
                    if previous_difference < 0
                    else "isolated_temporal_spike"
                )
                reasons.append("low_quality_temporal_outlier")

        suspected = bool(reasons)
        row.update(
            {
                "difference_from_previous": previous_difference,
                "difference_to_next": next_difference,
                "rolling_median_3": local_median,
                "deviation_from_rolling_median": deviation,
                "local_mad": local_mad,
                "temporal_outlier_suspected": suspected,
                "included_in_analysis": not suspected,
                "exclusion_reasons": reasons,
            }
        )
        if suspected:
            outliers.append(
                {
                    "item_id": row.get("item_id"),
                    "datetime": row.get("datetime"),
                    "reasons": reasons,
                    "scene_quality_score": row.get("scene_quality_score"),
                    "deviation_from_rolling_median": deviation,
                    "local_mad": local_mad,
                }
            )

    return TemporalSeriesResult(
        raw_daily_timeseries=rows,
        analysis_timeseries=[row for row in rows if row["included_in_analysis"]],
        outliers=outliers,
    )


def calculate_analysis_quality(
    analysis_records: Sequence[dict[str, Any]],
    *,
    raw_records: Sequence[dict[str, Any]] | None = None,
    processed_scene_count: int,
    rejected_scene_count: int,
    max_gap_days: int,
    min_observations: int,
) -> dict[str, Any]:
    """Calcula qualidade global por media ponderada de seis dimensoes auditaveis."""
    analysis = list(analysis_records)
    raw = list(raw_records) if raw_records is not None else analysis
    scene_scores = [_quality_score(record) for record in analysis]
    valid_percentages = [_number(record.get("valid_pixel_percentage")) for record in analysis]
    coverages = [_number(record.get("aoi_coverage_percentage")) for record in analysis]
    dates = sorted(_datetime(record["datetime"]) for record in analysis)
    gaps = [(current - previous).days for previous, current in zip(dates, dates[1:])]
    max_gap = max(gaps, default=0)
    outlier_count = sum(bool(record.get("temporal_outlier_suspected")) for record in raw)

    mean_scene = mean(scene_scores) if scene_scores else 0.0
    minimum_scene = min(scene_scores, default=0.0)
    mean_valid = mean(valid_percentages) if valid_percentages else 0.0
    mean_coverage = mean(coverages) if coverages else 0.0
    gap_score = 100.0 if max_gap <= max_gap_days else max(
        0.0, 100.0 - (max_gap - max_gap_days) * 5.0
    )
    retention_score = 100.0 * (1.0 - outlier_count / len(raw)) if raw else 0.0
    acceptance_score = (
        100.0 * max(0, processed_scene_count - rejected_scene_count) / processed_scene_count
        if processed_scene_count
        else 0.0
    )
    score = round(
        mean_scene * 0.30
        + minimum_scene * 0.15
        + mean_valid * 0.20
        + mean_coverage * 0.15
        + gap_score * 0.10
        + retention_score * 0.05
        + acceptance_score * 0.05,
        2,
    )
    status = "high" if score >= 85.0 else "medium" if score >= 70.0 else "low"

    reasons: list[str] = []
    if len(analysis) < min_observations:
        reasons.append("insufficient_analysis_observations")
    if mean_scene < 70.0:
        reasons.append("low_mean_scene_quality")
    if max_gap > max_gap_days:
        reasons.append("excessive_temporal_gap")
    if outlier_count:
        reasons.append("temporal_outliers_excluded")
    if rejected_scene_count:
        reasons.append("scenes_rejected_by_quality")

    return {
        "score": min(100.0, max(0.0, score)),
        "status": status,
        "observation_count": len(analysis),
        "mean_scene_quality_score": round(mean_scene, 2),
        "minimum_scene_quality_score": round(minimum_scene, 2),
        "mean_valid_pixel_percentage": round(mean_valid, 2),
        "mean_aoi_coverage_percentage": round(mean_coverage, 2),
        "max_temporal_gap_days": max_gap,
        "temporal_outlier_count": outlier_count,
        "rejected_scene_count": rejected_scene_count,
        "reasons": reasons,
        "limitations": [
            "Pontuacao experimental sem validacao de campo.",
            "Outliers temporais sao diagnosticados por vizinhanca local e qualidade relativa.",
        ],
        "formula": {
            "mean_scene_quality_score_weight": 0.30,
            "minimum_scene_quality_score_weight": 0.15,
            "mean_valid_pixel_percentage_weight": 0.20,
            "mean_aoi_coverage_percentage_weight": 0.15,
            "temporal_gap_weight": 0.10,
            "temporal_retention_weight": 0.05,
            "scene_acceptance_weight": 0.05,
        },
    }
