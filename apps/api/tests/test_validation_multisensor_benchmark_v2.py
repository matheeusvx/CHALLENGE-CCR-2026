from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.app.dependencies import get_validation_multisensor_benchmark_v2_path
from apps.api.app.main import app
from apps.api.app.validation.multisensor_benchmark_v2 import (
    BenchmarkIntegrityError,
    build_validation_multisensor_benchmark_v2,
    load_multisensor_benchmark_v2_artifact,
    write_multisensor_benchmark_v2_artifact,
)


def _inputs() -> tuple[list[dict], dict, list[dict]]:
    rows: list[dict] = []
    temporal: list[dict] = []
    soak: list[dict] = []
    definitions = [
        # TP, FN, FP, TN for the Sentinel-2 cut baseline: 4/1/1/8.
        *(('cut', 'cortar') for _ in range(4)),
        ('cut', 'nao_cortar'),
        ('no_cut', 'cortar'),
        *(('no_cut', 'nao_cortar') for _ in range(8)),
        ('uncertain', 'inconclusivo'),
    ]
    for index, (truth, decision) in enumerate(definitions):
        sample_id = f"sample-{index:02d}"
        status = "stable"
        if index == 4:  # known-style false negative: rule A captures it
            status = "increasing"
        elif index == 0:  # correct S2 case: B creates one false review
            status = "mixed"
        rows.append(
            {
                "sample_id": sample_id,
                "analysis_id": f"analysis-{index}",
                "schema_version": 1,
                "vegetation_class": "shrub" if index % 2 else "mixed",
                "maintenance_truth": truth,
                "reference_date": "2026-09-05",
                "s2_decision": decision,
                "s2_confidence": "high",
                "s2_ndvi_mean": .5 + index / 100,
                "s1_status": "available",
                "s1_canonical_relative_orbit": 53 if index % 2 else 126,
                "snapshot": {"sentinel2": {}, "sentinel1": {"canonical_metrics": {}}},
            }
        )
        temporal.append(
            {
                "sample_id": sample_id,
                "processing_status": "completed",
                "combined_status": status,
                "vv_temporal_status": status,
                "vh_temporal_status": status,
                "vv_slope": .01,
                "vh_slope": .02,
                "vv_modeled_change_db": .3,
                "vh_modeled_change_db": .4,
                "span_days": 31,
                "canonical_relative_orbit": 53 if index % 2 else 126,
                "canonical_observation_count": 4,
                "analysis_period": {"start_date": "2026-08-05", "end_date": "2026-09-05", "source": "reference_date_fallback"},
                "warnings": [],
            }
        )
        for repetition in (1, 2, 3):
            soak.append(
                {
                    "schema_version": "1.0",
                    "aoi_id": sample_id,
                    "repetition": repetition,
                    "analysis_period": {"start_date": "2026-08-01", "end_date": "2026-08-31"},
                    "status": "successful",
                    "availability": "available",
                    "canonical_relative_orbit": 126,
                    "canonical_observation_count": 4,
                    "temporal_status": "stable",
                    "scenes_accepted": 8,
                    "processing_duration_ms": 1000 + repetition,
                    "failure_counts": {},
                    "warnings": [],
                }
            )
    return rows, {"experimental": True, "samples": temporal}, soak


def _build(**kwargs) -> dict:
    rows, temporal, soak = _inputs()
    return build_validation_multisensor_benchmark_v2(
        rows,
        temporal,
        soak,
        generated_at="2026-09-07T12:00:00+00:00",
        bootstrap_iterations=100,
        **kwargs,
    )


def test_consolidates_soak_without_pseudoreplication_and_recalculates_baseline() -> None:
    result = _build()
    assert result["schema_version"] == "2.0"
    assert result["dataset"]["sample_count"] == 15
    assert result["dataset"]["scientific_sample_count"] == 15
    assert result["dataset"]["soak_run_count"] == 45
    assert result["dataset"]["soak_scientific_weight"] == 0
    assert result["s2_baseline"]["eligible_samples"] == 14
    assert result["s2_baseline"]["confusion"] == {"tp": 4, "fn": 1, "fp": 1, "tn": 8}
    assert result["s2_baseline"]["accuracy"]["value"] == pytest.approx(12 / 14)
    assert result["s2_baseline"]["precision_cut"]["value"] == .8
    assert result["s2_baseline"]["specificity"]["value"] == pytest.approx(8 / 9)
    assert result["s2_baseline"]["balanced_accuracy"]["value"] == pytest.approx((.8 + 8 / 9) / 2)


def test_rules_unions_metrics_uncertain_and_hard_cap() -> None:
    result = _build()
    rule_a = result["candidate_rules"]["A"]["primary_intention_to_review"]
    rule_b = result["candidate_rules"]["B"]["primary_intention_to_review"]
    union = result["combinations"]["A+B"]["primary_intention_to_review"]
    assert rule_a["confusion"] == {"tp": 1, "fn": 1, "fp": 0, "tn": 12}
    assert rule_b["confusion"] == {"tp": 0, "fn": 2, "fp": 1, "tn": 11}
    assert union["confusion"] == {"tp": 1, "fn": 1, "fp": 1, "tn": 11}
    assert union["triggered_samples"] == 2  # union, no repeat-level double counting
    assert union["error_capture_rate"]["value"] == .5
    assert union["false_review_rate"]["value"] == pytest.approx(1 / 12)
    assert result["dataset"]["partitions"]["uncertain_ground_truth"] == 1
    assert result["recommendation_gate"]["overall_status"] == "SHADOW_REVIEW_CANDIDATE"
    assert all(
        entry["gate"]["status"] != "REVIEW_MODE_CANDIDATE"
        for entry in [*result["candidate_rules"].values(), *result["combinations"].values()]
    )


def test_unavailable_s1_is_non_trigger_primary_but_excluded_per_protocol() -> None:
    rows, temporal, soak = _inputs()
    temporal["samples"][4]["combined_status"] = "insufficient_data"
    result = build_validation_multisensor_benchmark_v2(rows, temporal, soak, bootstrap_iterations=20)
    primary = result["candidate_rules"]["A"]["primary_intention_to_review"]
    protocol = result["candidate_rules"]["A"]["secondary_s1_available_only"]
    assert primary["eligible_samples"] == 14
    assert primary["confusion"]["fn"] == 2
    assert protocol["eligible_samples"] == 13


def test_wilson_bootstrap_are_deterministic_and_zero_denominators_are_null() -> None:
    first, second = _build(), _build()
    assert first["candidate_rules"]["A"]["primary_intention_to_review"] == second["candidate_rules"]["A"]["primary_intention_to_review"]
    rows, temporal, soak = _inputs()
    for row in rows:
        if row["maintenance_truth"] in {"cut", "no_cut"}:
            row["s2_decision"] = "inconclusivo"
    result = build_validation_multisensor_benchmark_v2(rows, temporal, soak, bootstrap_iterations=10)
    metric = result["candidate_rules"]["A"]["primary_intention_to_review"]
    assert metric["eligible_samples"] == 0
    assert metric["review_precision"]["value"] is None
    assert metric["balanced_accuracy"]["bootstrap_95"] is None


def test_duplicate_and_incomplete_joins_are_auditable() -> None:
    rows, temporal, soak = _inputs()
    with pytest.raises(BenchmarkIntegrityError, match="temporal_duplicate_sample_id"):
        build_validation_multisensor_benchmark_v2(rows, {"samples": [*temporal["samples"], temporal["samples"][0]]}, soak)
    missing = build_validation_multisensor_benchmark_v2(rows, {"samples": temporal["samples"][:-1]}, soak, bootstrap_iterations=1)
    assert any(item.startswith("temporal_missing:") for item in missing["warnings"])
    assert missing["recommendation_gate"]["technical_pass"] is False


def test_atomic_artifact_round_trip_contains_no_geometry_notes_or_nan(tmp_path: Path) -> None:
    result = _build()
    path = tmp_path / "benchmark.json"
    write_multisensor_benchmark_v2_artifact(path, result)
    loaded = load_multisensor_benchmark_v2_artifact(path)
    serialized = path.read_text(encoding="utf-8")
    assert loaded == result
    assert '"geometry"' not in serialized
    assert '"notes"' not in serialized
    assert "NaN" not in serialized


def test_endpoint_reads_frozen_artifact_and_reports_missing_or_invalid(client, tmp_path: Path) -> None:
    path = tmp_path / "benchmark.json"
    write_multisensor_benchmark_v2_artifact(path, _build())
    app.dependency_overrides[get_validation_multisensor_benchmark_v2_path] = lambda: path
    response = client.get("/api/validation-multisensor-benchmark-v2")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "2.0"

    missing = tmp_path / "missing.json"
    app.dependency_overrides[get_validation_multisensor_benchmark_v2_path] = lambda: missing
    response = client.get("/api/validation-multisensor-benchmark-v2")
    assert response.status_code == 404
    assert response.json()["error"]["code"].endswith("NOT_FOUND")

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    app.dependency_overrides[get_validation_multisensor_benchmark_v2_path] = lambda: invalid
    response = client.get("/api/validation-multisensor-benchmark-v2")
    assert response.status_code == 503


def test_soak_missing_run_breaks_technical_gate_and_does_not_remove_sample() -> None:
    rows, temporal, soak = _inputs()
    removed = soak.pop()
    result = build_validation_multisensor_benchmark_v2(rows, temporal, soak, bootstrap_iterations=1)
    assert len(result["sample_matrix"]) == len(rows)
    sample = next(row for row in result["sample_matrix"] if row["sample_id"] == removed["aoi_id"])
    assert sample["soak_reproducibility"]["observed_runs"] == 2
    assert result["soak_reproducibility"]["complete"] is False
    assert result["recommendation_gate"]["overall_status"] == "NO_REVIEW_MODE_YET"


def test_review_gate_requires_and_accepts_large_independent_preregistered_holdout() -> None:
    rows, temporal_rows, soak = [], [], []
    for index in range(50):
        is_error = index < 10
        sample_id = f"holdout-{index}"
        rows.append({
            "sample_id": sample_id, "analysis_id": f"analysis-{index}",
            "schema_version": 1, "vegetation_class": "mixed",
            "maintenance_truth": "cut", "reference_date": "2026-09-05",
            "s2_decision": "nao_cortar" if is_error else "cortar",
            "snapshot": {"sentinel2": {}, "sentinel1": {"canonical_metrics": {}}},
        })
        temporal_rows.append({
            "sample_id": sample_id, "processing_status": "completed",
            "combined_status": "increasing" if is_error else "stable",
            "vv_temporal_status": "stable", "vh_temporal_status": "stable",
            "canonical_relative_orbit": 126, "canonical_observation_count": 4,
        })
        for repetition in (1, 2, 3):
            soak.append({
                "schema_version": "1.0", "aoi_id": sample_id,
                "repetition": repetition, "analysis_period": {"start_date": "2026-08-01", "end_date": "2026-08-31"},
                "status": "successful", "availability": "available",
                "canonical_relative_orbit": 126, "canonical_observation_count": 4,
                "temporal_status": "stable", "scenes_accepted": 8,
                "processing_duration_ms": 1, "failure_counts": {}, "warnings": [],
            })
    result = build_validation_multisensor_benchmark_v2(
        rows, {"experimental": True, "samples": temporal_rows}, soak,
        bootstrap_iterations=10,
        holdout={"independent": True, "preregistered": True},
    )
    assert result["candidate_rules"]["A"]["gate"]["status"] == "REVIEW_MODE_CANDIDATE"
    assert result["recommendation_gate"]["overall_status"] == "REVIEW_MODE_CANDIDATE"


def test_c_is_standalone_and_d_is_only_complementary() -> None:
    result = _build()
    c = result["candidate_rules"]["C"]["primary_intention_to_review"]
    assert c["confusion"]["tp"] == 1  # S2 false positive with stable S1
    assert result["complementary_evidence"]["D"]["creates_decision"] is False
    assert "D" not in result["combinations"]
