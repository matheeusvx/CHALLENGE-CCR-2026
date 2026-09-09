from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import get_validation_repository
from apps.api.app.main import app
from apps.api.app.validation.benchmark import (
    build_validation_benchmark,
    cliffs_delta,
    effect_magnitude,
    iqr_overlap,
    spearman_rho,
)
from apps.api.app.validation.repository import ValidationSampleRepository


def benchmark_row(**updates) -> dict:
    sample_id = str(uuid4())
    row = {
        "sample_id": sample_id,
        "analysis_id": str(uuid4()),
        "schema_version": 1,
        "created_at": "2026-09-06T12:00:00+00:00",
        "vegetation_class": "shrub",
        "maintenance_truth": "cut",
        "validation_source": "field_inspection",
        "reference_date": "2026-09-06",
        "notes": None,
        "selected_area_m2": 1000.0,
        "s2_decision": "cortar",
        "s2_confidence": "high",
        "s2_ndvi_mean": 0.6,
        "s2_ndvi_median": 0.58,
        "s2_current_percentile": 75.0,
        "s1_status": "available",
        "s1_quality": 90.0,
        "s1_coverage": 95.0,
        "s1_canonical_relative_orbit": 53,
        "s1_canonical_observation_count": 5,
        "s1_vv_sigma0_linear": 0.04,
        "s1_vh_sigma0_linear": 0.01,
        "s1_vv_sigma0_db": -14.0,
        "s1_vh_sigma0_db": -20.0,
        "s1_vh_minus_vv_db": -6.0,
        "s1_vh_vv_sigma0_ratio": 0.25,
        "snapshot": {
            "sentinel2": {
                "observation_count": 6,
                "analysis_quality_score": 91.0,
            },
            "sentinel1": {
                "radiometric_calibration_status": "calibrated",
                "calibrated_observation_count": 5,
            },
        },
    }
    row.update(updates)
    return row


def persist(repository: ValidationSampleRepository, row: dict) -> None:
    record = dict(row)
    record["snapshot_json"] = json.dumps(record.pop("snapshot"))
    repository.insert(record)


def test_empty_dataset_is_explicit_and_does_not_create_zero_statistics() -> None:
    result = build_validation_benchmark([])

    assert result["dataset"]["total_samples"] == 0
    assert result["dataset"]["samples_with_s1"] == 0
    assert result["class_statistics"]["tree"]["features"][
        "current_ndvi_mean"
    ] is None
    assert result["sentinel2_performance"]["accuracy"] is None
    assert result["disagreements"] == []


def test_single_sample_and_nulls_preserve_missing_values() -> None:
    result = build_validation_benchmark(
        [benchmark_row(s2_ndvi_median=None, s1_vh_sigma0_db=None)]
    )
    shrub = result["class_statistics"]["shrub"]["features"]

    assert shrub["current_ndvi_mean"]["count"] == 1
    assert shrub["current_ndvi_mean"]["standard_deviation"] is None
    assert shrub["current_ndvi_median"] is None
    assert shrub["vh_sigma0_db"] is None
    assert "feature_missing_data:current_ndvi_median" in result["warnings"]


def test_statistics_by_class_and_maintenance_include_quartiles_and_std() -> None:
    rows = [
        benchmark_row(s2_ndvi_mean=value, vegetation_class="tree", maintenance_truth="no_cut")
        for value in (1.0, 2.0, 3.0, 4.0)
    ]
    result = build_validation_benchmark(rows)
    class_stats = result["class_statistics"]["tree"]["features"]["current_ndvi_mean"]
    truth_stats = result["maintenance_statistics"]["no_cut"]["features"][
        "current_ndvi_mean"
    ]

    assert class_stats["q25"] == pytest.approx(1.75)
    assert class_stats["q75"] == pytest.approx(3.25)
    assert class_stats["standard_deviation"] == pytest.approx(1.2909944487)
    assert truth_stats == class_stats


def test_sentinel2_binary_performance_keeps_inconclusive_separate() -> None:
    rows = [
        benchmark_row(maintenance_truth="cut", s2_decision="cortar"),
        benchmark_row(maintenance_truth="cut", s2_decision="nao_cortar"),
        benchmark_row(maintenance_truth="no_cut", s2_decision="cortar"),
        benchmark_row(maintenance_truth="no_cut", s2_decision="nao_cortar"),
        benchmark_row(maintenance_truth="cut", s2_decision="inconclusivo"),
        benchmark_row(maintenance_truth="uncertain", s2_decision="cortar"),
    ]

    result = build_validation_benchmark(rows)
    performance = result["sentinel2_performance"]

    assert performance["eligible_binary_samples"] == 4
    assert performance["correct"] == 2
    assert performance["incorrect"] == 2
    assert performance["accuracy"] == pytest.approx(0.5)
    assert performance["true_cut_pred_cut"] == 1
    assert performance["true_cut_pred_no_cut"] == 1
    assert performance["true_no_cut_pred_cut"] == 1
    assert performance["true_no_cut_pred_no_cut"] == 1
    assert performance["inconclusive_predictions"] == 1


def test_false_positive_false_negative_and_disagreements_are_auditable() -> None:
    false_negative = benchmark_row(
        maintenance_truth="cut", s2_decision="nao_cortar", s2_ndvi_mean=0.31
    )
    false_positive = benchmark_row(
        maintenance_truth="no_cut", s2_decision="cortar", s2_ndvi_mean=0.77
    )

    result = build_validation_benchmark([false_negative, false_positive])

    assert len(result["disagreements"]) == 2
    assert result["disagreements"][0]["sample_id"] == false_negative["sample_id"]
    assert result["disagreements"][0]["current_ndvi_mean"] == 0.31
    assert result["disagreements"][0]["s1_canonical_relative_orbit"] == 53
    assert result["sentinel2_performance"]["incorrect"] == 2


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0.0, "negligible"),
        (0.2, "small"),
        (-0.4, "medium"),
        (0.8, "large"),
    ],
)
def test_cliffs_delta_and_effect_magnitude(delta: float, expected: str) -> None:
    assert effect_magnitude(delta) == expected
    assert cliffs_delta([3.0, 4.0], [1.0, 2.0]) == 1.0


def test_pairwise_requires_two_finite_values_per_class() -> None:
    result = build_validation_benchmark(
        [
            benchmark_row(vegetation_class="tree", s1_vv_sigma0_db=-10.0),
            benchmark_row(vegetation_class="shrub", s1_vv_sigma0_db=-20.0),
            benchmark_row(vegetation_class="shrub", s1_vv_sigma0_db=-19.0),
        ]
    )
    assert result["pairwise_separation"]["vv_sigma0_db"] == []


def test_pairwise_reports_cliffs_delta_iqr_overlap_and_feature_rank() -> None:
    rows = [
        benchmark_row(vegetation_class="tree", s1_vv_sigma0_db=value)
        for value in (-10.0, -9.0, -8.0)
    ] + [
        benchmark_row(vegetation_class="shrub", s1_vv_sigma0_db=value)
        for value in (-20.0, -19.0, -18.0)
    ]
    result = build_validation_benchmark(rows)
    comparison = result["pairwise_separation"]["vv_sigma0_db"][0]

    assert comparison["class_a"] == "shrub"
    assert comparison["class_b"] == "tree"
    assert comparison["cliffs_delta"] == -1.0
    assert comparison["effect_magnitude"] == "large"
    assert comparison["iqr_overlap"] == "separated"
    assert comparison["median_difference"] == pytest.approx(-10.0)
    summary = result["feature_summary"]["features"]["vv_sigma0_db"]
    assert summary["largest_absolute_pairwise_effect"] == 1.0
    assert summary["medium_or_large_comparison_count"] == 1


def test_iqr_overlap_rules_cover_all_categories() -> None:
    separated_a = {"q25": 0.0, "median": 0.5, "q75": 1.0}
    separated_b = {"q25": 2.0, "median": 2.5, "q75": 3.0}
    strong_b = {"q25": 0.4, "median": 0.7, "q75": 1.4}
    partial_b = {"q25": 0.9, "median": 1.2, "q75": 1.5}

    assert iqr_overlap(separated_a, separated_b) == "separated"
    assert iqr_overlap(separated_a, strong_b) == "strong_overlap"
    assert iqr_overlap(separated_a, partial_b) == "partial_overlap"


def test_spearman_uses_average_ranks_and_requires_three_pairs() -> None:
    assert spearman_rho([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    assert spearman_rho([1.0, 1.0, 2.0], [1.0, 1.0, 3.0]) == pytest.approx(1.0)
    assert spearman_rho([1.0, 2.0], [2.0, 1.0]) is None


def test_s1_s2_correlations_use_only_complete_pairs() -> None:
    rows = [
        benchmark_row(s2_ndvi_mean=1.0, s1_vv_sigma0_db=-3.0),
        benchmark_row(s2_ndvi_mean=2.0, s1_vv_sigma0_db=-2.0),
        benchmark_row(s2_ndvi_mean=3.0, s1_vv_sigma0_db=-1.0),
        benchmark_row(s2_ndvi_mean=None, s1_vv_sigma0_db=0.0),
    ]
    correlation = build_validation_benchmark(rows)["s1_s2_correlations"][
        "ndvi_vs_vv_sigma0_db"
    ]
    assert correlation == {"n": 3, "rho": pytest.approx(1.0)}


def test_orbit_aggregation_and_within_class_53_126_comparison() -> None:
    rows = [
        benchmark_row(
            vegetation_class="shrub",
            s1_canonical_relative_orbit=53,
            s1_vv_sigma0_db=-15.0,
        ),
        benchmark_row(
            vegetation_class="shrub",
            s1_canonical_relative_orbit=126,
            s1_vv_sigma0_db=-10.0,
        ),
    ]
    result = build_validation_benchmark(rows)
    orbit = result["orbit_bias_check"]

    assert orbit["by_canonical_relative_orbit"]["53"]["count"] == 1
    comparison = orbit["within_vegetation_class_comparisons"]["shrub"][0]
    assert comparison["orbit_a"] == 53
    assert comparison["orbit_b"] == 126
    assert comparison["metrics"]["vv_sigma0_db"]["median_difference"] == -5.0
    assert orbit["automatic_correction_applied"] is False
    assert "orbit_comparison_sample_size_low" in result["warnings"]


def test_sample_size_warning_levels_are_explicit() -> None:
    rows = [benchmark_row(vegetation_class="tree") for _ in range(3)]
    warnings = build_validation_benchmark(rows)["warnings"]

    assert "class_sample_size_low:tree" in warnings
    assert "class_sample_size_very_low:tall_dense_grass" in warnings


def test_benchmark_endpoint_and_existing_validation_routes_remain_available(
    client: TestClient, tmp_path: Path
) -> None:
    repository = ValidationSampleRepository(tmp_path / "benchmark.sqlite3")
    row = benchmark_row()
    persist(repository, row)
    app.dependency_overrides[get_validation_repository] = lambda: repository

    benchmark = client.get("/api/validation-benchmark")

    assert benchmark.status_code == 200
    assert benchmark.json()["dataset"]["total_samples"] == 1
    assert client.get("/api/validation-summary").status_code == 200
    assert client.get("/api/validation-samples").status_code == 200
    assert client.get("/api/validation-export").status_code == 200


def test_benchmark_does_not_emit_or_modify_operational_decisions() -> None:
    row = benchmark_row(s2_decision="nao_cortar")
    original = row["s2_decision"]

    result = build_validation_benchmark([row])

    assert row["s2_decision"] == original
    assert "recommendation" not in result
    assert "fusion" not in result
    assert result["methodology"]["purpose"].startswith("Exploratory")
