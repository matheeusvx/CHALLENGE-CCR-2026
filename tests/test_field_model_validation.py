"""Testes offline da validação externa V0/C2/T3."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.satellite_monitoring.experiments.field_model_validation import (
    assert_external_holdouts_excluded,
    comparison_table,
    evaluate_gt35_v0,
    metric_classification_result,
    qualitative_consistency,
    write_external_validation_outputs,
    write_gt35_v0_validation,
)


def _gt35_row(sample_id: str, *, rejected: bool = False) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "observed_at": "2026-08-31T12:00:00-03:00",
        "measurement_type": "threshold_lower_bound",
        "height_lower_bound_cm": 35,
        "scene_datetime": "2026-08-23T13:12:41.025000+00:00",
        "scene_date": "2026-08-23",
        "scene_age_days": 8.07452517361111,
        "sentinel_item_id": "sentinel-causal",
        "scene_quality_score": 100,
        "valid_pixel_percentage": 100,
        "aoi_coverage_percentage": 100,
        "cloud_cover": 0.03,
        "red_reflectance": None if rejected else 0.0219,
        "nir_reflectance": None if rejected else 0.24545,
        "ndvi": None if rejected else 0.83677,
        "height_valid_pixel_count": 2 if rejected else 18,
        "height_total_pixel_count": 7 if rejected else 18,
        "vegetation_fraction": 2 / 7 if rejected else 1,
        "mixed_pixel_risk": "high" if rejected else "low",
        "spectral_extraction_status": "rejected_by_height_mask"
        if rejected
        else "available",
        "warnings": ["height mask rejected"] if rejected else [],
    }


def test_external_holdout_can_never_enter_fit() -> None:
    with pytest.raises(ValueError, match="leaked into fit"):
        assert_external_holdouts_excluded(
            [{"sample_id": "CAMPO_20260821_VIDEO_01_LOW", "target": 0}]
        )
    assert_external_holdouts_excluded([{"sample_id": "HISTORICAL_001", "target": 0}])


def test_thirty_centimeters_is_le_30_boundary() -> None:
    assert (
        metric_classification_result(
            measured_height_cm=30,
            boundary_case=True,
            estimated_class="le_30_cm",
        )
        == "correct"
    )
    assert (
        metric_classification_result(
            measured_height_cm=30,
            boundary_case=True,
            estimated_class="gt_30_cm",
        )
        == "boundary_false_positive"
    )


def test_inconclusive_is_abstention_not_error() -> None:
    assert (
        metric_classification_result(
            measured_height_cm=14,
            boundary_case=False,
            estimated_class="inconclusive",
        )
        == "abstained"
    )


def test_confirmed_threshold_uses_classification_ground_truth() -> None:
    assert (
        metric_classification_result(
            measured_height_cm=None,
            real_class=">30",
            boundary_case=False,
            estimated_class="gt_30_cm",
        )
        == "correct"
    )
    assert (
        metric_classification_result(
            measured_height_cm=None,
            real_class="gt_30_cm",
            boundary_case=False,
            estimated_class="le_30_cm",
        )
        == "false_negative"
    )
    assert (
        metric_classification_result(
            measured_height_cm=None,
            real_class="gt_30_cm",
            boundary_case=False,
            estimated_class="inconclusive",
        )
        == "abstained"
    )
    assert (
        metric_classification_result(
            measured_height_cm=30,
            boundary_case=True,
            estimated_class="inconclusive",
        )
        == "acceptable_boundary_abstention"
    )


def test_high_hill_is_qualitative_only() -> None:
    assert (
        metric_classification_result(
            measured_height_cm=None,
            boundary_case=False,
            estimated_class="gt_30_cm",
        )
        == "not_metric_ground_truth"
    )
    assert (
        qualitative_consistency("high_vegetation", "gt_30_cm")
        == "consistent_with_high_vegetation"
    )


def test_validation_output_is_deterministic(tmp_path: Path) -> None:
    results = [
        {
            "sample_id": "CAMPO_20260821_VIDEO_01_HIGH",
            "measured_height_cm": None,
            "real_class": "unknown",
            "boundary_case": False,
            "qualitative_condition": "high_vegetation",
            "recommendation_context": "cortar",
            "model": "C2",
            "score_gt_30_cm": 0.8,
            "estimated_class": "gt_30_cm",
            "classification_result": "not_metric_ground_truth",
            "qualitative_score": 0.8,
            "qualitative_consistency": "consistent_with_high_vegetation",
            "vegetation_fraction": 1.0,
            "mixed_pixel_risk": "low",
            "height_valid_pixel_count": 10,
            "height_total_pixel_count": 10,
            "temporal_status": "available",
            "model_version": "C2-reproduced-historical-only",
            "warnings": [],
        }
    ]
    reproduction = {
        "historical_only": True,
        "external_holdouts_used_in_fit": False,
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_external_validation_outputs(results, first, reproduction=reproduction)
    write_external_validation_outputs(results, second, reproduction=reproduction)
    assert (first / "field_model_validation.csv").read_bytes() == (
        second / "field_model_validation.csv"
    ).read_bytes()
    assert (first / "field_model_validation_summary.json").read_bytes() == (
        second / "field_model_validation_summary.json"
    ).read_bytes()
    assert (first / "field_model_validation_comparison.csv").read_bytes() == (
        second / "field_model_validation_comparison.csv"
    ).read_bytes()


def test_comparison_table_orders_scores_descending() -> None:
    results = []
    for sample_id, score in (
        ("CAMPO_20260821_VIDEO_01_LOW", 0.1),
        ("CAMPO_20260821_VIDEO_02", 0.4),
        ("GT35-01", 0.8),
        ("GT35-02", 0.7),
    ):
        for model in ("V0", "C2", "T3"):
            results.append(
                {
                    "sample_id": sample_id,
                    "model": model,
                    "real_class": "gt_30_cm" if sample_id.startswith("GT35") else "le_30_cm",
                    "measurement_type": "threshold_lower_bound" if sample_id.startswith("GT35") else "exact_single",
                    "height_lower_bound_cm": 35 if sample_id.startswith("GT35") else None,
                    "score_gt_30_cm": score,
                    "estimated_class": "gt_30_cm" if score >= 0.65 else "le_30_cm",
                }
            )
    table, ordering = comparison_table(results)
    assert [row["sample_id"] for row in table] == [
        "14 cm",
        "30 cm",
        "GT35-01",
        "GT35-02",
    ]
    assert ordering["V0"] == ["GT35-01", "GT35-02", "30 cm", "14 cm"]


def test_gt35_runs_independently_and_never_reconstructs_c2_t3() -> None:
    payload = evaluate_gt35_v0(
        [_gt35_row("GT35-01"), _gt35_row("GT35-02", rejected=True)]
    )
    assert [row["sample_id"] for row in payload["results"]] == [
        "GT35-01",
        "GT35-02",
    ]
    assert all(
        row["C2_status"] == "FROZEN_ARTIFACT_UNAVAILABLE"
        and row["T3_status"] == "FROZEN_ARTIFACT_UNAVAILABLE"
        for row in payload["results"]
    )
    assert payload["models_retrained"] is False
    assert all(
        row["status"] == "WAITING_FOR_FIELD_AOI_GEOJSON"
        for row in payload["blocked_holdouts"]
    )


def test_gt35_rejects_future_scene() -> None:
    future = _gt35_row("GT35-01")
    future["scene_datetime"] = "2026-08-31T15:30:00Z"
    with pytest.raises(ValueError, match="Future Sentinel scene"):
        evaluate_gt35_v0([future, _gt35_row("GT35-02")])


def test_gt35_output_is_deterministic(tmp_path: Path) -> None:
    rows = [_gt35_row("GT35-01"), _gt35_row("GT35-02", rejected=True)]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_gt35_v0_validation(rows, first)
    write_gt35_v0_validation(rows, second)
    assert first.read_bytes() == second.read_bytes()
