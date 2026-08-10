"""Testes deterministas da serie robusta e da qualidade global."""

from datetime import date

from src.satellite_monitoring.cut_recommendation import (
    RecommendationInput,
    RecommendationThresholds,
    recommend_cut,
)
from src.satellite_monitoring.temporal_quality import (
    calculate_analysis_quality,
    diagnose_temporal_consistency,
)


def _record(day: int, ndvi: float, score: float = 90.0) -> dict:
    return {
        "item_id": f"scene-{day}",
        "datetime": f"2026-07-{day:02d}T10:00:00+00:00",
        "ndvi_mean": ndvi,
        "ndvi_median": ndvi,
        "scene_quality_score": score,
        "valid_pixel_percentage": score,
        "aoi_coverage_percentage": 100.0,
        "partial_raster_coverage": False,
        "quality_status": "high" if score >= 85 else "medium",
        "accepted_for_timeseries": True,
    }


def _diagnose(records: list[dict]) -> object:
    return diagnose_temporal_consistency(
        records,
        min_deviation=0.06,
        mad_multiplier=3.0,
        return_ratio=0.5,
    )


def test_isolated_low_quality_spike_is_excluded_but_audited() -> None:
    result = _diagnose([_record(1, 0.50), _record(5, 0.80, 70), _record(9, 0.51)])
    assert len(result.raw_daily_timeseries) == 3
    assert len(result.analysis_timeseries) == 2
    assert result.outliers[0]["reasons"] == [
        "isolated_temporal_spike",
        "low_quality_temporal_outlier",
    ]


def test_isolated_low_quality_drop_is_excluded() -> None:
    result = _diagnose([_record(1, 0.60), _record(5, 0.30, 65), _record(9, 0.59)])
    assert result.raw_daily_timeseries[1]["temporal_outlier_suspected"] is True
    assert "isolated_temporal_drop" in result.raw_daily_timeseries[1]["exclusion_reasons"]


def test_persistent_high_quality_drop_is_not_removed() -> None:
    result = _diagnose([_record(1, 0.65), _record(5, 0.40), _record(9, 0.39)])
    assert len(result.analysis_timeseries) == 3
    assert result.outliers == []


def test_stable_series_is_preserved() -> None:
    result = _diagnose([_record(1, 0.50), _record(5, 0.51), _record(9, 0.50)])
    assert len(result.raw_daily_timeseries) == len(result.analysis_timeseries) == 3


def _analysis_quality(score: float, count: int, rejected: int = 0) -> dict:
    records = [_record(1 + index * 4, 0.4 + index * 0.01, score) for index in range(count)]
    return calculate_analysis_quality(
        records,
        raw_records=records,
        processed_scene_count=count + rejected,
        rejected_scene_count=rejected,
        max_gap_days=20,
        min_observations=4,
    )


def test_analysis_quality_high_medium_and_low() -> None:
    assert _analysis_quality(100.0, 4)["status"] == "high"
    assert _analysis_quality(75.0, 4, rejected=1)["status"] == "medium"
    low = _analysis_quality(50.0, 2, rejected=3)
    assert low["status"] == "low"
    assert "insufficient_analysis_observations" in low["reasons"]


def test_recommendation_is_inconclusive_when_temporal_filter_leaves_few_data() -> None:
    result = _diagnose(
        [
            _record(1, 0.50),
            _record(5, 0.80, 65),
            _record(9, 0.51),
            _record(13, 0.52),
        ]
    )
    recommendation = recommend_cut(
        RecommendationInput(
            observations=result.analysis_timeseries,
            thresholds=RecommendationThresholds(decision_min_observations=4),
            reference_date=date(2026, 7, 13),
        )
    )
    assert recommendation.recommendation == "inconclusivo"
    assert "insufficient_observations" in recommendation.blocking_reasons


def test_recommendation_is_inconclusive_when_analysis_quality_is_low() -> None:
    observations = [_record(day, 0.40 + day / 100) for day in (1, 5, 9, 13)]
    for observation in observations:
        observation["analysis_quality_status"] = "low"
    recommendation = recommend_cut(
        RecommendationInput(
            observations=observations,
            thresholds=RecommendationThresholds(decision_min_observations=4),
            reference_date=date(2026, 7, 13),
        )
    )
    assert recommendation.recommendation == "inconclusivo"
    assert "unacceptable_observation_quality" in recommendation.blocking_reasons
