"""Testes offline das features temporais do challenger Sentinel-2."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from src.satellite_monitoring.experiments.sentinel2_ablation import (
    common_sample_ids,
    fixed_fold_oof_scores,
    shared_group_fold_assignment,
)
from src.satellite_monitoring.experiments.sentinel2_temporal import (
    NO_RECENT_DROP_DAYS,
    TemporalObservation,
    build_temporal_feature_stages,
    linear_slope_per_day,
    recent_drop_features,
    strictly_historical_records,
)


def _values(ndvi: float, *, ndre: float | None = None, ndii: float = 0.2):
    return {
        "ndvi": ndvi,
        "ndre": ndvi if ndre is None else ndre,
        "ndii": ndii,
        "swir1_reflectance": 0.3,
        "swir2_reflectance": 0.2,
    }


def _observation(day: date, ndvi: float, item: str) -> TemporalObservation:
    return TemporalObservation(day, item, _values(ndvi))


def test_historical_selection_is_strictly_before_anchor_and_within_60_days() -> None:
    anchor = date(2026, 3, 20)
    records = [
        {"datetime": "2026-03-21T10:00:00Z", "accepted_for_timeseries": True, "item_id": "future"},
        {"datetime": "2026-03-20T10:00:00Z", "accepted_for_timeseries": True, "item_id": "anchor"},
        {"datetime": "2026-03-15T10:00:00Z", "accepted_for_timeseries": True, "item_id": "valid"},
        {"datetime": "2026-03-10T10:00:00Z", "accepted_for_timeseries": False, "item_id": "bad"},
        {"datetime": "2026-01-01T10:00:00Z", "accepted_for_timeseries": True, "item_id": "old"},
    ]
    selected = strictly_historical_records(records, anchor)
    assert [row["item_id"] for row in selected] == ["valid"]


def test_delta_and_temporal_gap_use_immediately_previous_observation() -> None:
    anchor_day = date(2026, 3, 20)
    anchor = TemporalObservation(anchor_day, "anchor", _values(0.60, ndre=0.50, ndii=0.25))
    previous = TemporalObservation(
        anchor_day - timedelta(days=5), "previous", _values(0.45, ndre=0.40, ndii=0.20)
    )
    stages = build_temporal_feature_stages(anchor, [previous])
    assert stages.previous_scene_date == previous.scene_date
    assert stages.temporal_gap_days == 5
    assert stages.t1 == pytest.approx(
        {
            "delta_ndvi": 0.15,
            "delta_ndre": 0.10,
            "delta_ndii": 0.05,
            "delta_swir1": 0.0,
            "delta_swir2": 0.0,
        }
    )


def test_slope_is_per_day_and_requires_two_distinct_observations() -> None:
    anchor = date(2026, 3, 20)
    dates = [anchor - timedelta(days=10), anchor]
    assert linear_slope_per_day(dates, [0.2, 0.4], anchor_scene_date=anchor) == pytest.approx(0.02)
    assert linear_slope_per_day([anchor], [0.4], anchor_scene_date=anchor) is None


def test_t2_is_absent_when_30d_history_is_insufficient() -> None:
    anchor_day = date(2026, 3, 20)
    stages = build_temporal_feature_stages(
        _observation(anchor_day, 0.5, "anchor"),
        [_observation(anchor_day - timedelta(days=40), 0.3, "previous")],
    )
    assert stages.t1 is not None
    assert stages.t2 is None
    assert stages.invalid_reason_t2 == "insufficient_30d_observations"
    assert stages.observation_count_30d == 1
    assert stages.observation_count_60d == 2


def test_recent_drop_and_recovery_are_deterministic() -> None:
    anchor_day = date(2026, 3, 20)
    observations = [
        _observation(anchor_day - timedelta(days=20), 0.70, "old"),
        _observation(anchor_day - timedelta(days=10), 0.50, "drop"),
        _observation(anchor_day, 0.60, "anchor"),
    ]
    first = recent_drop_features(observations, anchor_scene_date=anchor_day, feature="ndvi")
    second = recent_drop_features(list(reversed(observations)), anchor_scene_date=anchor_day, feature="ndvi")
    assert first == second
    assert first == pytest.approx((0.20, 10, 0.10))
    no_drop = recent_drop_features(
        [_observation(anchor_day - timedelta(days=5), 0.5, "old"), _observation(anchor_day, 0.55, "anchor")],
        anchor_scene_date=anchor_day,
        feature="ndvi",
    )
    assert no_drop == pytest.approx((0.0, NO_RECENT_DROP_DAYS, 0.0))


def test_common_cohort_and_folds_remain_deterministic_without_leakage() -> None:
    rows = [
        {
            "sample_id": f"sample-{index:02d}",
            "group_id": f"km-{index:02d}",
            "target": index % 2,
            "ndvi": 0.2 + 0.1 * (index % 2),
        }
        for index in range(20)
    ]
    common = common_sample_ids(rows, rows[2:], rows[4:])
    assert common == [row["sample_id"] for row in rows[4:]]
    assignment, folds = shared_group_fold_assignment(rows)
    repeated, repeated_folds = shared_group_fold_assignment(list(reversed(rows)))
    assert assignment == repeated
    assert folds == repeated_folds
    y1, scores1, fold_rows = fixed_fold_oof_scores(rows, ("ndvi",), assignment)
    y2, scores2, _ = fixed_fold_oof_scores(rows, ("ndvi",), assignment)
    assert np.array_equal(y1, y2)
    assert np.allclose(scores1, scores2)
    for fold in fold_rows:
        assert set(fold["train_groups"]).isdisjoint(fold["test_groups"])


def test_future_observation_is_rejected_by_feature_builder() -> None:
    anchor_day = date(2026, 3, 20)
    with pytest.raises(ValueError, match="strictly before"):
        build_temporal_feature_stages(
            _observation(anchor_day, 0.5, "anchor"),
            [_observation(anchor_day + timedelta(days=1), 0.6, "future")],
        )
