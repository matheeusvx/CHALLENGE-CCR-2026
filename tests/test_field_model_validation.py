"""Testes offline da validação externa V0/C2/T3."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.satellite_monitoring.experiments.field_model_validation import (
    assert_external_holdouts_excluded,
    metric_classification_result,
    qualitative_consistency,
    write_external_validation_outputs,
)


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
