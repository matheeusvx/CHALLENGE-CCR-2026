"""Testes das regras provisorias de qualidade."""

import pytest

from src.satellite_monitoring.quality import (
    assess_scene_quality,
    classify_quality,
    determine_overall_status,
    summarize_scene_quality,
)


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [(100.0, "high"), (85.0, "high"), (84.99, "medium"), (70.0, "medium"), (69.99, "low")],
)
def test_classifies_quality_levels(percentage: float, expected: str) -> None:
    assert classify_quality(percentage) == expected


def test_quality_thresholds_are_configurable() -> None:
    assert classify_quality(80.0, medium_threshold=60.0, high_threshold=75.0) == "high"


def _assess(percentage: float, include_low: bool = False):
    return assess_scene_quality(
        valid_pixel_percentage=percentage,
        min_valid_pixel_percentage=70.0,
        medium_threshold=70.0,
        high_threshold=85.0,
        has_scl=True,
        cloud_cover=10.0,
        max_cloud_cover=30.0,
        partial_raster_coverage=False,
        has_valid_ndvi_pixels=True,
        include_low_quality_scenes=include_low,
    )


def test_rejects_scene_below_minimum_valid_percentage() -> None:
    assessment = _assess(69.0)
    assert assessment.quality_status == "low"
    assert assessment.accepted_for_timeseries is False
    assert "insufficient_valid_pixels" in assessment.quality_reasons


def test_can_include_low_quality_scene_explicitly() -> None:
    assessment = _assess(69.0, include_low=True)
    assert assessment.quality_status == "low"
    assert assessment.accepted_for_timeseries is True
    assert "insufficient_valid_pixels" in assessment.quality_reasons


def test_records_additional_quality_reasons() -> None:
    assessment = assess_scene_quality(
        valid_pixel_percentage=0.0,
        min_valid_pixel_percentage=70.0,
        medium_threshold=70.0,
        high_threshold=85.0,
        has_scl=False,
        cloud_cover=40.0,
        max_cloud_cover=30.0,
        partial_raster_coverage=True,
        has_valid_ndvi_pixels=False,
        include_low_quality_scenes=True,
    )
    assert assessment.accepted_for_timeseries is False
    assert assessment.quality_reasons == (
        "no_valid_ndvi_pixels",
        "insufficient_valid_pixels",
        "missing_scl",
        "excessive_global_cloud_cover",
        "partial_raster_coverage",
    )


def test_minimum_observations_sets_overall_status() -> None:
    assert determine_overall_status(
        fatal_error=None,
        processed_scene_count=5,
        accepted_scene_count=3,
        min_observations=4,
    ) == "insufficient_observations"
    assert determine_overall_status(
        fatal_error=None,
        processed_scene_count=5,
        accepted_scene_count=4,
        min_observations=4,
    ) == "success"


def test_quality_summary_contains_accepted_and_rejected_scenes() -> None:
    summary = summarize_scene_quality(
        [
            {
                "item_id": "accepted",
                "datetime": "2026-08-01T00:00:00+00:00",
                "quality_status": "high",
                "quality_reasons": [],
                "accepted_for_timeseries": True,
                "processing_status": "processed",
            },
            {
                "item_id": "rejected",
                "datetime": "2026-08-02T00:00:00+00:00",
                "quality_status": "low",
                "quality_reasons": ["insufficient_valid_pixels", "missing_scl"],
                "accepted_for_timeseries": False,
                "processing_status": "processed",
            },
        ]
    )
    assert summary["accepted_scene_count"] == 1
    assert summary["rejected_scene_count"] == 1
    assert summary["rejected_scenes"][0]["item_id"] == "rejected"
    assert summary["rejection_reasons"] == {
        "insufficient_valid_pixels": 1,
        "missing_scl": 1,
    }
