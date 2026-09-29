"""Testes offline da validação externa de campo de 2026-08-21."""

from __future__ import annotations

from datetime import date

import pytest
from shapely.geometry import shape

from src.satellite_monitoring.experiments.field_external_validation import (
    EXPECTED_HOLDOUTS,
    assert_external_holdout_excluded,
    classification_result,
    field_scene_eligibility,
    load_external_holdouts,
    metric_buffer_geojson,
    projected_area_m2,
    select_strict_causal_scene,
    spatial_stability,
)
from src.satellite_monitoring.experiments.sentinel2_temporal import (
    TemporalObservation,
    build_temporal_feature_stages,
)


def test_metric_buffers_preserve_center_and_requested_area() -> None:
    latitude, longitude = -23.292111, -46.844444
    areas = []
    for radius in (10, 20, 30):
        geometry = metric_buffer_geojson(latitude, longitude, radius)
        center = shape(geometry).centroid
        assert center.x == pytest.approx(longitude, abs=1e-6)
        assert center.y == pytest.approx(latitude, abs=1e-6)
        area = projected_area_m2(geometry)
        assert area == pytest.approx(3.13654849 * radius**2, rel=1e-4)
        areas.append(area)
    assert areas[0] < areas[1] < areas[2]


def test_external_holdouts_are_forbidden_in_any_fit_rows() -> None:
    assert_external_holdout_excluded([{"sample_id": "old-sample"}])
    with pytest.raises(ValueError, match="External holdout"):
        assert_external_holdout_excluded([{"sample_id": "FIELD_VIDEO_01"}])


def test_missing_updated_dataset_keeps_explicit_holdout_separate(tmp_path) -> None:
    samples, diagnostics = load_external_holdouts(tmp_path / "missing.csv")
    assert [sample.sample_id for sample in samples] == ["FIELD_VIDEO_01", "FIELD_VIDEO_02"]
    assert diagnostics["dataset_available"] is False
    assert diagnostics["matched_sample_ids"] == []


def test_strict_causal_scene_never_selects_after_field_date() -> None:
    records = [
        {"item_id": "future", "datetime": "2026-08-22T10:00:00Z", "accepted_for_timeseries": True},
        {"item_id": "old", "datetime": "2026-08-19T10:00:00Z", "accepted_for_timeseries": True,
         "scene_quality_score": 70, "valid_pixel_percentage": 90, "cloud_cover": 5},
        {"item_id": "recent", "datetime": "2026-08-21T10:00:00Z", "accepted_for_timeseries": True,
         "scene_quality_score": 60, "valid_pixel_percentage": 80, "cloud_cover": 10},
    ]
    selected = select_strict_causal_scene(records, date(2026, 8, 21))
    assert selected is not None
    assert selected["item_id"] == "recent"
    assert selected["sentinel_scene_date"] <= "2026-08-21"
    assert selected["days_before_field_measurement"] == 0


def test_small_aoi_override_waives_only_absolute_pixel_count() -> None:
    small_aoi = {
        "accepted_for_timeseries": False,
        "processing_status": "processed",
        "quality_status": "high",
        "valid_pixel_count": 9,
        "valid_pixel_percentage": 100.0,
        "quality_reasons": ["insufficient_valid_pixel_count"],
    }
    assert field_scene_eligibility(small_aoi) == (
        True,
        "field_small_aoi_minimum_pixel_count_override",
    )
    contaminated = {
        **small_aoi,
        "quality_reasons": ["insufficient_valid_pixel_count", "no_valid_ndvi_pixels"],
    }
    assert field_scene_eligibility(contaminated) == (False, "rejected_by_quality")


def test_thirty_centimeters_is_le_30_boundary_case() -> None:
    sample = next(item for item in EXPECTED_HOLDOUTS if item.sample_id == "FIELD_VIDEO_02")
    assert sample.measured_height_cm == 30
    assert sample.real_class == "le_30_cm"
    assert sample.boundary_case is True


def test_classification_comparison_treats_inconclusive_as_abstention() -> None:
    assert classification_result("le_30_cm", "le_30_cm") == "correct"
    assert classification_result("le_30_cm", "gt_30_cm") == "false_positive"
    assert classification_result("le_30_cm", "inconclusive") == "abstained"
    assert classification_result("le_30_cm", None) == "unavailable"


def test_spatial_stability_uses_classes_and_score_amplitude() -> None:
    stable = spatial_stability([
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.20},
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.22},
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.25},
    ])
    assert stable["spatial_stability"] == "stable"
    assert stable["score_amplitude"] == pytest.approx(0.05)
    partial = spatial_stability([
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.20},
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.30},
        {"estimated_class": "inconclusive", "score_gt_30_cm": 0.45},
    ])
    assert partial["spatial_stability"] == "partially_stable"
    sensitive = spatial_stability([
        {"estimated_class": "le_30_cm", "score_gt_30_cm": 0.10},
        {"estimated_class": "inconclusive", "score_gt_30_cm": 0.50},
        {"estimated_class": "gt_30_cm", "score_gt_30_cm": 0.80},
    ])
    assert sensitive["spatial_stability"] == "spatially_sensitive"


def test_t3_is_fail_soft_when_temporal_history_is_absent() -> None:
    values = {
        "ndvi": 0.5,
        "ndre": 0.3,
        "ndii": 0.2,
        "swir1_reflectance": 0.25,
        "swir2_reflectance": 0.20,
    }
    stages = build_temporal_feature_stages(
        TemporalObservation(date(2026, 8, 21), "anchor", values), []
    )
    assert stages.t3 is None
    assert stages.invalid_reason_t3 == "no_previous_scene"
