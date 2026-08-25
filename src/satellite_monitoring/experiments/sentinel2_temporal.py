"""Features temporais Sentinel-2 isoladas para experimentos offline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Mapping, Sequence

import numpy as np

TEMPORAL_LOOKBACK_DAYS = 60
TREND_WINDOWS_DAYS = (30, 60)
MIN_TREND_OBSERVATIONS = 2
RECENT_DROP_WINDOW_DAYS = 30
RECENT_DROP_ABSOLUTE = 0.06
NO_RECENT_DROP_DAYS = TEMPORAL_LOOKBACK_DAYS + 1
TRAINING_TEMPORAL_CONTEXT = "historical_scenes_before_selected_anchor_scene"

DELTA_FEATURES = (
    "delta_ndvi",
    "delta_ndre",
    "delta_ndii",
    "delta_swir1",
    "delta_swir2",
)
TREND_FEATURES = tuple(
    f"{index}_slope_{window}d"
    for index in ("ndvi", "ndre", "ndii")
    for window in TREND_WINDOWS_DAYS
)
EVENT_FEATURES = (
    "recent_ndvi_drop",
    "days_since_recent_ndvi_drop",
    "ndvi_recovery_since_drop",
    "recent_ndre_drop",
)


@dataclass(frozen=True)
class TemporalObservation:
    scene_date: date
    item_id: str
    values: Mapping[str, float]


@dataclass(frozen=True)
class TemporalFeatureStages:
    t1: dict[str, float] | None
    t2: dict[str, float] | None
    t3: dict[str, float] | None
    previous_scene_date: date | None
    temporal_gap_days: int | None
    observation_count_30d: int
    observation_count_60d: int
    invalid_reason_t1: str | None
    invalid_reason_t2: str | None
    invalid_reason_t3: str | None


def parse_scene_date(record: Mapping[str, Any]) -> date | None:
    raw = record.get("datetime") or record.get("sentinel_scene_date")
    if hasattr(raw, "date"):
        try:
            return raw.date()
        except (TypeError, ValueError):
            pass
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def strictly_historical_records(
    records: Sequence[Mapping[str, Any]],
    anchor_scene_date: date,
    *,
    lookback_days: int = TEMPORAL_LOOKBACK_DAYS,
) -> list[Mapping[str, Any]]:
    """Seleciona somente cenas aceitas em (anchor-lookback, anchor)."""
    eligible: list[tuple[Mapping[str, Any], date]] = []
    for record in records:
        scene_date = parse_scene_date(record)
        if (
            record.get("accepted_for_timeseries") is True
            and scene_date is not None
            and 0 < (anchor_scene_date - scene_date).days <= lookback_days
        ):
            eligible.append((record, scene_date))
    return [
        record
        for record, _ in sorted(
            eligible,
            key=lambda entry: (
                -entry[1].toordinal(),
                str(entry[0].get("item_id") or ""),
            ),
        )
    ]


def linear_slope_per_day(
    dates: Sequence[date], values: Sequence[float], *, anchor_scene_date: date
) -> float | None:
    if len(dates) < MIN_TREND_OBSERVATIONS or len(dates) != len(values):
        return None
    x = np.asarray([(value - anchor_scene_date).days for value in dates], dtype=float)
    y = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(y)) or len(np.unique(x)) < 2:
        return None
    return float(np.polyfit(x, y, 1)[0])


def recent_drop_features(
    observations: Sequence[TemporalObservation],
    *,
    anchor_scene_date: date,
    feature: str,
    threshold: float = RECENT_DROP_ABSOLUTE,
) -> tuple[float, int, float]:
    """Retorna magnitude, dias e recuperacao do evento mais recente em 30 dias."""
    ordered = sorted(observations, key=lambda item: (item.scene_date, item.item_id))
    events: list[tuple[date, float, float]] = []
    for before, after in zip(ordered, ordered[1:]):
        days_since = (anchor_scene_date - after.scene_date).days
        if not 0 <= days_since <= RECENT_DROP_WINDOW_DAYS:
            continue
        drop = float(before.values[feature]) - float(after.values[feature])
        if math.isfinite(drop) and drop >= threshold:
            events.append((after.scene_date, drop, float(after.values[feature])))
    if not events:
        return 0.0, NO_RECENT_DROP_DAYS, 0.0
    drop_date, magnitude, post_drop = max(events, key=lambda item: item[0])
    current = float(ordered[-1].values[feature])
    return magnitude, (anchor_scene_date - drop_date).days, current - post_drop


def _deduplicate_observations(
    observations: Sequence[TemporalObservation],
) -> list[TemporalObservation]:
    by_date: dict[date, TemporalObservation] = {}
    for observation in sorted(observations, key=lambda item: item.item_id):
        by_date.setdefault(observation.scene_date, observation)
    return sorted(by_date.values(), key=lambda item: item.scene_date)


def build_temporal_feature_stages(
    anchor: TemporalObservation,
    historical: Sequence[TemporalObservation],
) -> TemporalFeatureStages:
    """Constrói T1/T2/T3 sem usar cenas futuras nem o label de altura."""
    if any(item.scene_date >= anchor.scene_date for item in historical):
        raise ValueError("Historical observations must be strictly before anchor scene.")
    history = _deduplicate_observations(
        [
            item
            for item in historical
            if 0 < (anchor.scene_date - item.scene_date).days <= TEMPORAL_LOOKBACK_DAYS
        ]
    )
    if not history:
        return TemporalFeatureStages(
            t1=None,
            t2=None,
            t3=None,
            previous_scene_date=None,
            temporal_gap_days=None,
            observation_count_30d=1,
            observation_count_60d=1,
            invalid_reason_t1="no_previous_scene",
            invalid_reason_t2="no_previous_scene",
            invalid_reason_t3="no_previous_scene",
        )

    previous = history[-1]
    gap = (anchor.scene_date - previous.scene_date).days
    t1 = {
        "delta_ndvi": float(anchor.values["ndvi"]) - float(previous.values["ndvi"]),
        "delta_ndre": float(anchor.values["ndre"]) - float(previous.values["ndre"]),
        "delta_ndii": float(anchor.values["ndii"]) - float(previous.values["ndii"]),
        "delta_swir1": float(anchor.values["swir1_reflectance"])
        - float(previous.values["swir1_reflectance"]),
        "delta_swir2": float(anchor.values["swir2_reflectance"])
        - float(previous.values["swir2_reflectance"]),
    }
    series = _deduplicate_observations([*history, anchor])
    counts = {
        window: sum((anchor.scene_date - item.scene_date).days <= window for item in series)
        for window in TREND_WINDOWS_DAYS
    }
    if counts[30] < MIN_TREND_OBSERVATIONS:
        reason = "insufficient_30d_observations"
        return TemporalFeatureStages(
            t1=t1,
            t2=None,
            t3=None,
            previous_scene_date=previous.scene_date,
            temporal_gap_days=gap,
            observation_count_30d=counts[30],
            observation_count_60d=counts[60],
            invalid_reason_t1=None,
            invalid_reason_t2=reason,
            invalid_reason_t3=reason,
        )
    if counts[60] < MIN_TREND_OBSERVATIONS:
        reason = "insufficient_60d_observations"
        return TemporalFeatureStages(
            t1=t1,
            t2=None,
            t3=None,
            previous_scene_date=previous.scene_date,
            temporal_gap_days=gap,
            observation_count_30d=counts[30],
            observation_count_60d=counts[60],
            invalid_reason_t1=None,
            invalid_reason_t2=reason,
            invalid_reason_t3=reason,
        )

    t2 = dict(t1)
    for feature in ("ndvi", "ndre", "ndii"):
        for window in TREND_WINDOWS_DAYS:
            selected = [
                item
                for item in series
                if (anchor.scene_date - item.scene_date).days <= window
            ]
            slope = linear_slope_per_day(
                [item.scene_date for item in selected],
                [float(item.values[feature]) for item in selected],
                anchor_scene_date=anchor.scene_date,
            )
            if slope is None or not math.isfinite(slope):
                return TemporalFeatureStages(
                    t1=t1,
                    t2=None,
                    t3=None,
                    previous_scene_date=previous.scene_date,
                    temporal_gap_days=gap,
                    observation_count_30d=counts[30],
                    observation_count_60d=counts[60],
                    invalid_reason_t1=None,
                    invalid_reason_t2="invalid_feature",
                    invalid_reason_t3="invalid_feature",
                )
            t2[f"{feature}_slope_{window}d"] = slope

    ndvi_drop, ndvi_days, ndvi_recovery = recent_drop_features(
        series, anchor_scene_date=anchor.scene_date, feature="ndvi"
    )
    ndre_drop, _, _ = recent_drop_features(
        series, anchor_scene_date=anchor.scene_date, feature="ndre"
    )
    t3 = {
        **t2,
        "recent_ndvi_drop": ndvi_drop,
        "days_since_recent_ndvi_drop": float(ndvi_days),
        "ndvi_recovery_since_drop": ndvi_recovery,
        "recent_ndre_drop": ndre_drop,
    }
    if not all(math.isfinite(value) for value in (*t1.values(), *t2.values(), *t3.values())):
        return TemporalFeatureStages(
            t1=None,
            t2=None,
            t3=None,
            previous_scene_date=previous.scene_date,
            temporal_gap_days=gap,
            observation_count_30d=counts[30],
            observation_count_60d=counts[60],
            invalid_reason_t1="invalid_feature",
            invalid_reason_t2="invalid_feature",
            invalid_reason_t3="invalid_feature",
        )
    return TemporalFeatureStages(
        t1=t1,
        t2=t2,
        t3=t3,
        previous_scene_date=previous.scene_date,
        temporal_gap_days=gap,
        observation_count_30d=counts[30],
        observation_count_60d=counts[60],
        invalid_reason_t1=None,
        invalid_reason_t2=None,
        invalid_reason_t3=None,
    )
