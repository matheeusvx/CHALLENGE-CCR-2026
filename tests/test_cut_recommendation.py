"""Testes deterministas da consolidacao e recomendacao experimental."""

from datetime import date, datetime, timedelta, timezone

import pytest

from src.satellite_monitoring.cut_recommendation import (
    RecommendationInput,
    RecommendationThresholds,
    aggregate_daily_observations,
    calculate_current_percentile,
    calculate_recent_trend,
    calculate_temporal_features,
    detect_recent_drop,
    recommend_cut,
)

REFERENCE_DATE = date(2026, 8, 4)
THRESHOLDS = RecommendationThresholds()


def _observation(
    day: int,
    ndvi: float,
    *,
    item_id: str | None = None,
    hour: int = 10,
    valid_percentage: float = 90.0,
    cloud_cover: float = 5.0,
    coverage: float = 100.0,
    partial: bool = False,
) -> dict:
    observed_at = datetime(2026, 7, 1, hour, tzinfo=timezone.utc) + timedelta(days=day - 1)
    return {
        "item_id": item_id or f"scene-{day}-{hour}",
        "datetime": observed_at.isoformat(),
        "cloud_cover": cloud_cover,
        "platform": "sentinel-2b",
        "tile": "23KLP",
        "red_asset": "B04",
        "nir_asset": "B08",
        "scl_asset": "SCL",
        "ndvi_mean": ndvi,
        "ndvi_median": ndvi + 0.01,
        "ndvi_std": 0.02,
        "ndvi_min": ndvi - 0.1,
        "ndvi_max": ndvi + 0.1,
        "valid_pixel_count": int(valid_percentage),
        "total_pixel_count": 100,
        "valid_pixel_percentage": valid_percentage,
        "aoi_coverage_percentage": coverage,
        "partial_raster_coverage": partial,
        "quality_status": "high" if valid_percentage >= 85 else "medium",
        "quality_reasons": [],
        "accepted_for_timeseries": True,
    }


def _input(values: list[float], days: list[int] | None = None, **kwargs) -> RecommendationInput:
    observation_days = days or [10 + index * 5 for index in range(len(values))]
    return RecommendationInput(
        observations=[
            _observation(day, value) for day, value in zip(observation_days, values)
        ],
        thresholds=kwargs.get("thresholds", THRESHOLDS),
        reference_date=kwargs.get("reference_date", REFERENCE_DATE),
        min_valid_pixel_percentage=70.0,
    )


def test_daily_aggregation_best_uses_all_tiebreakers() -> None:
    records = [
        _observation(10, 0.50, item_id="less-valid", valid_percentage=88, cloud_cover=1),
        _observation(10, 0.60, item_id="more-cloud", hour=11, cloud_cover=8, coverage=100),
        _observation(10, 0.65, item_id="less-coverage", hour=12, cloud_cover=5, coverage=90),
        _observation(10, 0.70, item_id="chosen", hour=13, cloud_cover=5, coverage=100),
    ]

    result = aggregate_daily_observations(records, "best")

    assert len(result.observations) == 1
    assert result.observations[0]["item_id"] == "chosen"
    assert result.observations[0]["aggregation_scene_count"] == 4
    assert result.audit["days"][0]["considered_item_ids"] == [
        "less-valid",
        "more-cloud",
        "less-coverage",
        "chosen",
    ]
    assert result.audit["days"][0]["selected_item_id"] == "chosen"
    assert "scene_quality_score" in result.audit["days"][0]["selection_reason"]


def test_daily_best_prioritizes_scene_quality_score() -> None:
    higher_valid = _observation(10, 0.50, item_id="higher-valid", valid_percentage=99)
    higher_score = _observation(10, 0.60, item_id="higher-score", hour=11, valid_percentage=90)
    higher_valid["scene_quality_score"] = 80.0
    higher_score["scene_quality_score"] = 95.0
    result = aggregate_daily_observations([higher_valid, higher_score], "best")
    assert result.observations[0]["item_id"] == "higher-score"


def test_daily_aggregation_median_calculates_one_observation() -> None:
    records = [
        _observation(10, 0.2, item_id="a", hour=10),
        _observation(10, 0.8, item_id="b", hour=11),
        _observation(10, 0.5, item_id="c", hour=12),
    ]

    result = aggregate_daily_observations(records, "median")

    assert len(result.observations) == 1
    assert result.observations[0]["ndvi_mean"] == pytest.approx(0.5)
    assert result.observations[0]["aggregation_scene_count"] == 3
    assert result.observations[0]["aggregation_selected_item_id"] is None


def test_daily_aggregation_none_preserves_observations() -> None:
    records = [_observation(10, 0.2), _observation(10, 0.3, hour=11)]
    result = aggregate_daily_observations(records, "none")
    assert len(result.observations) == 2


def test_current_percentile_uses_midrank_for_ties() -> None:
    assert calculate_current_percentile([0.2, 0.4, 0.6, 0.8]) == pytest.approx(87.5)
    assert calculate_current_percentile([0.5, 0.5, 0.5]) == pytest.approx(50.0)


def test_recent_trend_positive_and_negative() -> None:
    assert calculate_recent_trend(_input([0.2, 0.3, 0.5]).observations, 3) > 0
    assert calculate_recent_trend(_input([0.7, 0.6, 0.4]).observations, 3) < 0


def test_detects_absolute_and_relative_drop() -> None:
    observations = _input([0.70, 0.50, 0.51]).observations
    drop = detect_recent_drop(observations, THRESHOLDS, REFERENCE_DATE)
    assert drop.absolute_drop_detected is True
    assert drop.relative_drop_detected is True
    assert drop.absolute_drop == pytest.approx(0.20)
    assert drop.relative_drop_percentage == pytest.approx(28.5714, rel=1e-4)


def test_drop_confirmation_requires_later_observation() -> None:
    confirmed = detect_recent_drop(
        _input([0.70, 0.50, 0.51]).observations,
        THRESHOLDS,
        REFERENCE_DATE,
    )
    unconfirmed = detect_recent_drop(
        _input([0.70, 0.50]).observations,
        THRESHOLDS,
        REFERENCE_DATE,
    )
    assert confirmed.confirmed is True
    assert confirmed.unconfirmed_recent_drop is False
    assert unconfirmed.confirmed is False
    assert unconfirmed.unconfirmed_recent_drop is True


def test_temporal_features_include_requested_metrics() -> None:
    metrics = calculate_temporal_features(_input([0.2, 0.4, 0.3, 0.5]))
    assert metrics["observation_count"] == 4
    assert metrics["historical_median"] == pytest.approx(0.35)
    assert metrics["historical_mean"] == pytest.approx(0.35)
    assert metrics["total_amplitude"] == pytest.approx(0.3)
    assert metrics["max_observation_gap_days"] == pytest.approx(5)
    assert metrics["first_date"] == "2026-07-10"
    assert metrics["last_date"] == "2026-07-25"


def test_few_observations_are_inconclusive() -> None:
    result = recommend_cut(_input([0.2, 0.4, 0.6]))
    assert result.recommendation == "inconclusivo"
    assert "insufficient_observations" in result.blocking_reasons


def test_excessive_gap_is_inconclusive() -> None:
    result = recommend_cut(_input([0.2, 0.3, 0.4, 0.5], days=[1, 6, 10, 31]))
    assert result.recommendation == "inconclusivo"
    assert "excessive_observation_gap" in result.blocking_reasons


def test_recommends_cut_for_high_local_percentile_and_growth() -> None:
    result = recommend_cut(_input([0.20, 0.30, 0.40, 0.55]))
    assert result.recommendation == "cortar"
    assert "current_percentile_at_or_above_high_threshold" in result.reasons


def test_recommends_no_cut_for_confirmed_recent_drop() -> None:
    result = recommend_cut(_input([0.65, 0.70, 0.50, 0.51]))
    assert result.recommendation == "nao_cortar"
    assert "recent_significant_drop_confirmed" in result.reasons


def test_recommends_no_cut_for_low_percentile() -> None:
    result = recommend_cut(_input([0.70, 0.66, 0.62, 0.58]))
    assert result.recommendation == "nao_cortar"
    assert "current_percentile_below_or_equal_50" in result.reasons


def test_intermediate_indicators_are_inconclusive() -> None:
    result = recommend_cut(_input([0.40, 0.45, 0.43, 0.44]))
    assert result.recommendation == "inconclusivo"
    assert "criteria_between_cut_and_no_cut" in result.blocking_reasons


def test_contradictory_series_is_inconclusive() -> None:
    result = recommend_cut(_input([0.40, 0.50, 0.40, 0.50]))
    assert result.recommendation == "inconclusivo"
    assert "contradictory_series" in result.blocking_reasons


def test_low_quality_or_partial_coverage_blocks_decision() -> None:
    observations = _input([0.2, 0.3, 0.4, 0.5]).observations
    observations[-1]["valid_pixel_percentage"] = 60.0
    observations[-1]["quality_status"] = "low"
    observations[-1]["partial_raster_coverage"] = True
    result = recommend_cut(
        RecommendationInput(observations, THRESHOLDS, REFERENCE_DATE, 70.0)
    )
    assert result.recommendation == "inconclusivo"
    assert "insufficient_valid_pixel_percentage" in result.blocking_reasons
    assert "partial_aoi_coverage" in result.blocking_reasons


def test_confidence_is_deterministic_and_can_be_high() -> None:
    result = recommend_cut(
        _input(
            [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55],
            days=[1, 5, 9, 13, 17, 21, 25, 29],
        )
    )
    assert result.recommendation == "cortar"
    assert result.confidence == "high"
    assert result.quality["confidence_score"] >= 8


def test_old_latest_observation_blocks_decision() -> None:
    input_data = _input(
        [0.2, 0.3, 0.4, 0.5],
        reference_date=REFERENCE_DATE + timedelta(days=30),
    )
    result = recommend_cut(input_data)
    assert result.recommendation == "inconclusivo"
    assert "no_recent_observation" in result.blocking_reasons
