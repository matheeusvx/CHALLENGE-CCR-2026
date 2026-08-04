"""Recomendacao experimental e explicavel baseada na serie temporal local."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

import numpy as np

AREA_TYPE = "roadside_grass"
LIMITATIONS = (
    "O Sentinel-2 nao mede diretamente a altura da grama.",
    "NDVI representa resposta espectral da vegetacao e pode sofrer mistura de pixels.",
    "Os limites sao experimentais e ainda nao foram validados pela Motiva.",
    "A recomendacao exige validacao com inspecoes de campo e registros de manutencao.",
)


@dataclass(frozen=True)
class RecommendationThresholds:
    decision_min_observations: int = 4
    high_vegetation_percentile: float = 75.0
    significant_drop_absolute: float = 0.06
    significant_drop_relative_percentage: float = 15.0
    trend_window: int = 3
    max_gap_days: int = 20
    recent_intervention_days: int = 20

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values.update({"experimental": True, "validated_by_motiva": False})
        return values


@dataclass(frozen=True)
class RecommendationInput:
    observations: Sequence[dict[str, Any]]
    thresholds: RecommendationThresholds
    reference_date: date
    min_valid_pixel_percentage: float = 70.0
    area_type: str = AREA_TYPE


@dataclass(frozen=True)
class RecommendationResult:
    recommendation: str
    confidence: str
    area_type: str
    summary: str
    reasons: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    metrics: dict[str, Any]
    thresholds: dict[str, Any]
    quality: dict[str, Any]
    date_range: dict[str, str | None]
    limitations: tuple[str, ...] = LIMITATIONS
    experimental: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "area_type": self.area_type,
            "experimental": self.experimental,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "blocking_reasons": list(self.blocking_reasons),
            "metrics": self.metrics,
            "thresholds": self.thresholds,
            "quality": self.quality,
            "date_range": self.date_range,
            "limitations": list(self.limitations),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "experimental": self.experimental,
            "reasons": list(self.reasons),
            "blocking_reasons": list(self.blocking_reasons),
            "metrics": self.metrics,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DailyAggregationResult:
    observations: list[dict[str, Any]]
    audit: dict[str, Any]


@dataclass(frozen=True)
class DropDetection:
    significant_drop_detected: bool
    confirmed: bool
    quality_acceptable: bool
    absolute_drop_detected: bool
    relative_drop_detected: bool
    absolute_drop: float
    relative_drop_percentage: float | None
    largest_recent_drop: float
    drop_date: str | None
    days_since_significant_drop: int | None
    significant_drop_dates: tuple[str, ...]
    unconfirmed_recent_drop: bool


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        observed_at = value
    elif isinstance(value, date):
        observed_at = datetime.combine(value, datetime.min.time())
    else:
        observed_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_observations(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for record in records:
        if record.get("accepted_for_timeseries", True) is False:
            continue
        if _finite_number(record.get("ndvi_mean")) is None:
            continue
        try:
            _parse_datetime(record.get("datetime"))
        except (TypeError, ValueError):
            continue
        valid.append(dict(record))
    return sorted(valid, key=lambda record: _parse_datetime(record["datetime"]))


def _coverage(record: dict[str, Any]) -> float:
    value = _finite_number(record.get("aoi_coverage_percentage"))
    if value is not None:
        return value
    return 0.0 if record.get("partial_raster_coverage") else 100.0


def _best_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(record: dict[str, Any]) -> tuple[float, float, float, str]:
        valid = _finite_number(record.get("valid_pixel_percentage")) or 0.0
        cloud = _finite_number(record.get("cloud_cover"))
        return (-valid, cloud if cloud is not None else math.inf, -_coverage(record), str(record.get("item_id", "")))

    return min(records, key=rank)


def _joined_values(records: list[dict[str, Any]], key: str) -> str | None:
    values = sorted({str(record[key]) for record in records if record.get(key) not in (None, "")})
    return ";".join(values) if values else None


def _median_record(day: date, records: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _best_record(records)
    result = dict(selected)
    median_fields = (
        "cloud_cover",
        "ndvi_mean",
        "ndvi_median",
        "ndvi_std",
        "ndvi_min",
        "ndvi_max",
        "valid_pixel_count",
        "total_pixel_count",
        "valid_pixel_percentage",
        "aoi_coverage_percentage",
    )
    for field in median_fields:
        values = [_finite_number(record.get(field)) for record in records]
        finite_values = [value for value in values if value is not None]
        result[field] = float(np.median(finite_values)) if finite_values else None

    result.update(
        {
            "item_id": f"daily-median-{day.isoformat()}",
            "datetime": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            "platform": _joined_values(records, "platform"),
            "tile": _joined_values(records, "tile"),
            "quality_status": min(
                (str(record.get("quality_status", "high")) for record in records),
                key={"low": 0, "medium": 1, "high": 2}.get,
            ),
            "quality_reasons": sorted(
                {reason for record in records for reason in (record.get("quality_reasons") or [])}
            ),
            "partial_raster_coverage": any(
                bool(record.get("partial_raster_coverage")) for record in records
            ),
            "accepted_for_timeseries": True,
        }
    )
    return result


def aggregate_daily_observations(
    records: Sequence[dict[str, Any]],
    strategy: str = "best",
) -> DailyAggregationResult:
    """Consolida observacoes aceitas e registra a proveniencia de cada dia."""
    if strategy not in {"best", "median", "none"}:
        raise ValueError("A agregacao diaria deve ser 'best', 'median' ou 'none'.")

    valid = _valid_observations(records)
    if strategy == "none":
        observations = []
        days = []
        for record in valid:
            row = dict(record)
            item_id = str(row.get("item_id", ""))
            row.update(
                {
                    "daily_aggregation": "none",
                    "aggregation_scene_count": 1,
                    "aggregation_source_item_ids": [item_id],
                    "aggregation_selected_item_id": item_id,
                }
            )
            observations.append(row)
            days.append(
                {
                    "date": _parse_datetime(row["datetime"]).date().isoformat(),
                    "considered_item_ids": [item_id],
                    "selected_item_id": item_id,
                    "aggregated_scene_count": 1,
                }
            )
    else:
        groups: dict[date, list[dict[str, Any]]] = {}
        for record in valid:
            groups.setdefault(_parse_datetime(record["datetime"]).date(), []).append(record)

        observations = []
        days = []
        for day in sorted(groups):
            candidates = groups[day]
            selected = _best_record(candidates)
            row = dict(selected) if strategy == "best" else _median_record(day, candidates)
            source_ids = [str(record.get("item_id", "")) for record in candidates]
            row.update(
                {
                    "daily_aggregation": strategy,
                    "aggregation_scene_count": len(candidates),
                    "aggregation_source_item_ids": source_ids,
                    "aggregation_selected_item_id": (
                        str(selected.get("item_id", "")) if strategy == "best" else None
                    ),
                }
            )
            observations.append(row)
            days.append(
                {
                    "date": day.isoformat(),
                    "considered_item_ids": source_ids,
                    "selected_item_id": (
                        str(selected.get("item_id", "")) if strategy == "best" else None
                    ),
                    "aggregated_scene_count": len(candidates),
                }
            )

    return DailyAggregationResult(
        observations=observations,
        audit={
            "strategy": strategy,
            "input_scene_count": len(valid),
            "output_observation_count": len(observations),
            "days": days,
        },
    )


def calculate_current_percentile(values: Sequence[float], current_value: float | None = None) -> float | None:
    """Calcula o percentil por posto medio, incluindo empates de forma deterministica."""
    finite = [float(value) for value in values if _finite_number(value) is not None]
    if not finite:
        return None
    current = float(finite[-1] if current_value is None else current_value)
    below = sum(value < current for value in finite)
    equal = sum(value == current for value in finite)
    return float((below + 0.5 * equal) / len(finite) * 100.0)


def calculate_recent_trend(
    observations: Sequence[dict[str, Any]],
    window: int = 3,
) -> float | None:
    """Retorna a inclinacao linear recente em unidades de NDVI por dia."""
    valid = _valid_observations(observations)
    if window < 2 or len(valid) < window:
        return None
    recent = valid[-window:]
    dates = [_parse_datetime(record["datetime"]) for record in recent]
    x = np.array([(value - dates[0]).total_seconds() / 86400.0 for value in dates])
    if np.allclose(x, x[0]):
        x = np.arange(len(recent), dtype=float)
    y = np.array([float(record["ndvi_mean"]) for record in recent], dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def _quality_acceptable(record: dict[str, Any], minimum: float) -> bool:
    percentage = _finite_number(record.get("valid_pixel_percentage"))
    return (
        (percentage is None or percentage >= minimum)
        and not bool(record.get("partial_raster_coverage"))
        and record.get("quality_status") != "low"
    )


def detect_recent_drop(
    observations: Sequence[dict[str, Any]],
    thresholds: RecommendationThresholds,
    reference_date: date | None = None,
    min_valid_pixel_percentage: float = 70.0,
) -> DropDetection:
    """Detecta quedas significativas, recencia e confirmacao posterior."""
    valid = _valid_observations(observations)
    if len(valid) < 2:
        return DropDetection(False, False, False, False, False, 0.0, None, 0.0, None, None, (), False)

    reference = reference_date or _parse_datetime(valid[-1]["datetime"]).date()
    candidates: list[dict[str, Any]] = []
    significant_dates: list[str] = []
    for index in range(1, len(valid)):
        before = float(valid[index - 1]["ndvi_mean"])
        after = float(valid[index]["ndvi_mean"])
        absolute = max(0.0, before - after)
        relative = absolute / abs(before) * 100.0 if before != 0 else None
        absolute_detected = absolute >= thresholds.significant_drop_absolute
        relative_detected = (
            relative is not None
            and relative >= thresholds.significant_drop_relative_percentage
        )
        significant = absolute_detected and relative_detected
        drop_day = _parse_datetime(valid[index]["datetime"]).date()
        days_since = (reference - drop_day).days
        recent = 0 <= days_since <= thresholds.recent_intervention_days
        confirmed = index < len(valid) - 1
        confirmation_ok = not confirmed or _quality_acceptable(
            valid[index + 1], min_valid_pixel_percentage
        )
        quality_ok = (
            _quality_acceptable(valid[index - 1], min_valid_pixel_percentage)
            and _quality_acceptable(valid[index], min_valid_pixel_percentage)
            and confirmation_ok
        )
        if significant:
            significant_dates.append(drop_day.isoformat())
        candidates.append(
            {
                "absolute": absolute,
                "relative": relative,
                "absolute_detected": absolute_detected,
                "relative_detected": relative_detected,
                "significant": significant,
                "recent": recent,
                "confirmed": confirmed,
                "quality_ok": quality_ok,
                "date": drop_day,
                "days_since": days_since,
            }
        )

    recent_candidates = [candidate for candidate in candidates if candidate["recent"]]
    largest_recent = max((candidate["absolute"] for candidate in recent_candidates), default=0.0)
    significant = [candidate for candidate in candidates if candidate["significant"]]
    latest = significant[-1] if significant else None
    recent_significant = [candidate for candidate in significant if candidate["recent"]]
    selected = max(recent_significant, key=lambda item: item["absolute"], default=latest)
    return DropDetection(
        significant_drop_detected=bool(recent_significant),
        confirmed=bool(selected and selected["confirmed"]),
        quality_acceptable=bool(selected and selected["quality_ok"]),
        absolute_drop_detected=bool(selected and selected["absolute_detected"]),
        relative_drop_detected=bool(selected and selected["relative_detected"]),
        absolute_drop=float(selected["absolute"]) if selected else 0.0,
        relative_drop_percentage=(float(selected["relative"]) if selected and selected["relative"] is not None else None),
        largest_recent_drop=float(largest_recent),
        drop_date=selected["date"].isoformat() if selected else None,
        days_since_significant_drop=int(latest["days_since"]) if latest else None,
        significant_drop_dates=tuple(significant_dates),
        unconfirmed_recent_drop=any(
            candidate["recent"] and not candidate["confirmed"] for candidate in significant
        ),
    )


def calculate_temporal_features(input_data: RecommendationInput) -> dict[str, Any]:
    """Calcula as caracteristicas temporais usadas pela recomendacao."""
    observations = _valid_observations(input_data.observations)
    if not observations:
        return {
            "observation_count": 0,
            "current_ndvi_mean": None,
            "current_ndvi_median": None,
            "historical_median": None,
            "historical_mean": None,
            "historical_standard_deviation": None,
            "current_percentile": None,
            "recent_trend": None,
            "last_absolute_change": None,
            "last_relative_change_percentage": None,
            "largest_recent_drop": 0.0,
            "days_since_significant_drop": None,
            "total_amplitude": None,
            "max_observation_gap_days": None,
            "first_date": None,
            "last_date": None,
            "significant_drop_detected": False,
            "significant_drop_confirmed": False,
            "significant_drop_quality_acceptable": False,
            "significant_drop_dates": [],
            "unconfirmed_recent_drop": False,
        }

    values = [float(record["ndvi_mean"]) for record in observations]
    dates = [_parse_datetime(record["datetime"]) for record in observations]
    gaps = [(dates[index] - dates[index - 1]).total_seconds() / 86400.0 for index in range(1, len(dates))]
    last_change = values[-1] - values[-2] if len(values) >= 2 else None
    last_relative = (
        last_change / abs(values[-2]) * 100.0
        if last_change is not None and values[-2] != 0
        else None
    )
    drop = detect_recent_drop(
        observations,
        input_data.thresholds,
        reference_date=input_data.reference_date,
        min_valid_pixel_percentage=input_data.min_valid_pixel_percentage,
    )
    current_median = _finite_number(observations[-1].get("ndvi_median"))
    return {
        "observation_count": len(observations),
        "current_ndvi_mean": values[-1],
        "current_ndvi_median": current_median,
        "historical_median": float(np.median(values)),
        "historical_mean": float(np.mean(values)),
        "historical_standard_deviation": float(np.std(values)),
        "current_percentile": calculate_current_percentile(values),
        "recent_trend": calculate_recent_trend(observations, input_data.thresholds.trend_window),
        "last_absolute_change": last_change,
        "last_relative_change_percentage": last_relative,
        "largest_recent_drop": drop.largest_recent_drop,
        "days_since_significant_drop": drop.days_since_significant_drop,
        "total_amplitude": float(max(values) - min(values)),
        "max_observation_gap_days": float(max(gaps)) if gaps else 0.0,
        "first_date": dates[0].date().isoformat(),
        "last_date": dates[-1].date().isoformat(),
        "significant_drop_detected": drop.significant_drop_detected,
        "significant_drop_confirmed": drop.confirmed,
        "significant_drop_quality_acceptable": drop.quality_acceptable,
        "significant_drop_absolute": drop.absolute_drop,
        "significant_drop_relative_percentage": drop.relative_drop_percentage,
        "significant_drop_dates": list(drop.significant_drop_dates),
        "unconfirmed_recent_drop": drop.unconfirmed_recent_drop,
    }


def _trend_status(metrics: dict[str, Any], thresholds: RecommendationThresholds) -> str | None:
    trend = metrics.get("recent_trend")
    count = metrics.get("observation_count", 0)
    if trend is None or count < thresholds.trend_window:
        return None
    observations_span = max(1, thresholds.trend_window - 1)
    stable_limit = thresholds.significant_drop_absolute / observations_span
    if abs(trend) < stable_limit:
        return "stable"
    return "positive" if trend > 0 else "negative"


def _series_is_contradictory(observations: Sequence[dict[str, Any]], thresholds: RecommendationThresholds) -> bool:
    valid = _valid_observations(observations)
    recent = valid[-thresholds.trend_window :]
    changes = [
        float(recent[index]["ndvi_mean"]) - float(recent[index - 1]["ndvi_mean"])
        for index in range(1, len(recent))
    ]
    return (
        any(change >= thresholds.significant_drop_absolute for change in changes)
        and any(change <= -thresholds.significant_drop_absolute for change in changes)
    )


def _quality_summary(observations: Sequence[dict[str, Any]], minimum: float) -> dict[str, Any]:
    valid = _valid_observations(observations)
    percentages = [
        value
        for record in valid
        if (value := _finite_number(record.get("valid_pixel_percentage"))) is not None
    ]
    partial_count = sum(bool(record.get("partial_raster_coverage")) for record in valid)
    unacceptable_count = sum(not _quality_acceptable(record, minimum) for record in valid)
    return {
        "min_valid_pixel_percentage_required": minimum,
        "minimum_valid_pixel_percentage": min(percentages) if percentages else None,
        "mean_valid_pixel_percentage": float(np.mean(percentages)) if percentages else None,
        "partial_coverage_observation_count": partial_count,
        "unacceptable_observation_count": unacceptable_count,
        "all_observations_acceptable": unacceptable_count == 0,
    }


def _confidence(
    recommendation: str,
    metrics: dict[str, Any],
    quality: dict[str, Any],
    thresholds: RecommendationThresholds,
) -> tuple[str, int]:
    if recommendation == "inconclusivo":
        return "low", 0
    score = 0
    count = int(metrics["observation_count"])
    score += 2 if count >= thresholds.decision_min_observations * 2 else 1
    mean_valid = quality.get("mean_valid_pixel_percentage")
    if mean_valid is not None:
        score += 2 if mean_valid >= 85 else 1
    gap = metrics.get("max_observation_gap_days")
    if gap is not None:
        score += 2 if gap <= thresholds.max_gap_days / 2 else 1
    percentile = metrics.get("current_percentile")
    if percentile is not None:
        boundary_distance = min(
            abs(percentile - 50.0),
            abs(percentile - thresholds.high_vegetation_percentile),
        )
        score += 2 if boundary_distance >= 10 else 1
    if metrics.get("significant_drop_confirmed"):
        score += 1
    amplitude = metrics.get("total_amplitude")
    if amplitude is not None and amplitude <= thresholds.significant_drop_absolute * 2:
        score += 1
    if score >= 8:
        return "high", score
    if score >= 5:
        return "medium", score
    return "low", score


def recommend_cut(input_data: RecommendationInput) -> RecommendationResult:
    """Aplica regras conservadoras e retorna uma recomendacao explicavel."""
    observations = _valid_observations(input_data.observations)
    thresholds = input_data.thresholds
    metrics = calculate_temporal_features(input_data)
    quality = _quality_summary(observations, input_data.min_valid_pixel_percentage)
    trend_status = _trend_status(metrics, thresholds)
    metrics["recent_trend_status"] = trend_status
    metrics["series_contradictory"] = _series_is_contradictory(observations, thresholds)

    blockers: list[str] = []
    if metrics["observation_count"] < thresholds.decision_min_observations:
        blockers.append("insufficient_observations")
    if (
        metrics["max_observation_gap_days"] is not None
        and metrics["max_observation_gap_days"] > thresholds.max_gap_days
    ):
        blockers.append("excessive_observation_gap")
    if metrics["last_date"] is None or (
        input_data.reference_date - date.fromisoformat(metrics["last_date"])
    ).days > thresholds.max_gap_days:
        blockers.append("no_recent_observation")
    if quality["unacceptable_observation_count"]:
        if quality["minimum_valid_pixel_percentage"] is not None and quality[
            "minimum_valid_pixel_percentage"
        ] < input_data.min_valid_pixel_percentage:
            blockers.append("insufficient_valid_pixel_percentage")
        if quality["partial_coverage_observation_count"]:
            blockers.append("partial_aoi_coverage")
        if not blockers or blockers[-1] not in {
            "insufficient_valid_pixel_percentage",
            "partial_aoi_coverage",
        }:
            blockers.append("unacceptable_observation_quality")
    if metrics["series_contradictory"]:
        blockers.append("contradictory_series")
    if trend_status is None:
        blockers.append("insufficient_trend_data")
    if metrics["unconfirmed_recent_drop"]:
        blockers.append("unconfirmed_possible_drop")

    reasons: list[str] = []
    if blockers:
        recommendation = "inconclusivo"
        summary = "Evidencias insuficientes para uma recomendacao de corte."
    elif (
        metrics["significant_drop_detected"]
        and metrics["significant_drop_confirmed"]
        and metrics["significant_drop_quality_acceptable"]
    ):
        recommendation = "nao_cortar"
        reasons.extend(
            [
                "recent_significant_drop_confirmed",
                "quality_acceptable_during_drop",
            ]
        )
        summary = "Possivel intervencao recente identificada na serie local."
    else:
        last_change = metrics.get("last_absolute_change")
        last_relative = metrics.get("last_relative_change_percentage")
        accelerated_growth = (
            last_change is not None
            and last_relative is not None
            and last_change >= thresholds.significant_drop_absolute
            and last_relative >= thresholds.significant_drop_relative_percentage
        )
        current_percentile = metrics.get("current_percentile")
        if (
            current_percentile is not None
            and current_percentile <= 50.0
            and trend_status in {"stable", "negative"}
            and not accelerated_growth
        ):
            recommendation = "nao_cortar"
            reasons.extend(
                [
                    "current_percentile_below_or_equal_50",
                    "stable_or_decreasing_recent_trend",
                ]
            )
            summary = "Vegetacao abaixo do nivel historico alto sem crescimento acelerado."
        elif (
            current_percentile is not None
            and current_percentile >= thresholds.high_vegetation_percentile
            and trend_status in {"positive", "stable"}
            and not metrics["significant_drop_detected"]
        ):
            recommendation = "cortar"
            reasons.extend(
                [
                    "current_percentile_at_or_above_high_threshold",
                    "positive_or_stable_high_recent_trend",
                    "no_recent_confirmed_significant_drop",
                ]
            )
            summary = "Recomendacao experimental de corte baseada no historico local."
        else:
            recommendation = "inconclusivo"
            blockers.append("criteria_between_cut_and_no_cut")
            summary = "Indicadores intermediarios nao sustentam uma decisao segura."

    confidence, confidence_score = _confidence(recommendation, metrics, quality, thresholds)
    quality["confidence_score"] = confidence_score
    return RecommendationResult(
        recommendation=recommendation,
        confidence=confidence,
        area_type=input_data.area_type,
        summary=summary,
        reasons=tuple(reasons),
        blocking_reasons=tuple(dict.fromkeys(blockers)),
        metrics=metrics,
        thresholds=thresholds.to_dict(),
        quality=quality,
        date_range={"start": metrics["first_date"], "end": metrics["last_date"]},
    )
