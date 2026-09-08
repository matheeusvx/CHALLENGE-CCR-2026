from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from apps.api.app.dependencies import get_validation_holdout_benchmark_path
from apps.api.app.main import app
from apps.api.app.validation.holdout_benchmark import (
    ENGINEERING_GATES,
    HoldoutIntegrityError,
    build_validation_holdout_benchmark_v1,
    create_preregistration,
    freeze_preregistration,
    validate_preregistration,
    write_json_atomic,
)
from apps.api.app.validation.repository import (
    DuplicateAoiError,
    ValidationSampleRepository,
)


def _row(index: int, *, truth: str, decision: str, cohort: str = "holdout") -> dict:
    return {
        "sample_id": f"sample-{index:03d}",
        "analysis_id": f"analysis-{index:03d}",
        "aoi_fingerprint": f"aoi-{index:03d}",
        "cohort": cohort,
        "schema_version": 1,
        "created_at": f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00",
        "vegetation_class": ("shrub", "mixed", "tree")[index % 3],
        "maintenance_truth": truth,
        "reference_date": "2026-09-07",
        "s2_decision": decision,
        "s2_confidence": "high",
    }


def _temporal(rows: list[dict], *, false_reviews: int = 0) -> dict:
    samples = []
    correct_cut_seen = 0
    for row in rows:
        is_error = row["maintenance_truth"] in {"cut", "no_cut"} and row["s2_decision"] != (
            "cortar" if row["maintenance_truth"] == "cut" else "nao_cortar"
        )
        status = "mixed" if is_error and row["s2_decision"] == "cortar" else "stable"
        if (
            not is_error
            and row["s2_decision"] == "cortar"
            and correct_cut_seen < false_reviews
        ):
            status = "mixed"
            correct_cut_seen += 1
        samples.append({
            "sample_id": row["sample_id"],
            "processing_status": "completed",
            "combined_status": status,
            "canonical_relative_orbit": 53 if len(samples) % 2 else 126,
        })
    return {"generated_at": "2026-09-07T12:00:00+00:00", "samples": samples}


def _frozen(rows: list[dict]) -> dict:
    return freeze_preregistration(
        create_preregistration(created_at="2026-09-01T00:00:00+00:00"),
        rows,
        frozen_at="2026-09-02T00:00:00+00:00",
    )


def _passing_rows() -> list[dict]:
    rows = [
        _row(index, truth="no_cut", decision="cortar")
        for index in range(10)
    ]
    rows.extend(
        _row(index, truth="cut", decision="cortar")
        for index in range(10, 35)
    )
    rows.extend(
        _row(index, truth="no_cut", decision="nao_cortar")
        for index in range(35, 60)
    )
    return rows


def test_empty_holdout_is_insufficient() -> None:
    preregistration = _frozen([])
    result = build_validation_holdout_benchmark_v1(
        [], preregistration, {"samples": []}, generated_at="2026-09-07T00:00:00+00:00"
    )
    assert result["dataset"]["total_samples"] == 0
    assert result["recommendation_gate"]["status"] == "INSUFFICIENT_HOLDOUT_DATA"
    assert result["rule_b"]["intention_to_review"]["error_capture_rate"]["value"] is None


def test_less_than_50_binary_truths_never_passes_review_gate() -> None:
    rows = _passing_rows()[:49]
    result = build_validation_holdout_benchmark_v1(rows, _frozen(rows), _temporal(rows))
    assert result["dataset"]["binary_samples"] == 49
    assert result["recommendation_gate"]["status"] == "INSUFFICIENT_HOLDOUT_DATA"


def test_less_than_10_s2_errors_never_passes_review_gate() -> None:
    rows = _passing_rows()
    for index in range(9, 10):
        rows[index]["s2_decision"] = "nao_cortar"
    result = build_validation_holdout_benchmark_v1(rows, _frozen(rows), _temporal(rows))
    assert result["dataset"]["s2_errors"] == 9
    assert result["recommendation_gate"]["status"] == "INSUFFICIENT_HOLDOUT_DATA"


def test_complete_synthetic_holdout_can_pass_all_preregistered_gates() -> None:
    rows = _passing_rows()
    result = build_validation_holdout_benchmark_v1(
        rows, _frozen(rows), _temporal(rows), generated_at="2026-09-07T00:00:00+00:00"
    )
    metrics = result["rule_b"]["intention_to_review"]
    assert metrics["confusion"] == {"tp": 10, "fn": 0, "fp": 0, "tn": 50}
    assert result["recommendation_gate"]["status"] == "REVIEW_MODE_CANDIDATE"
    assert all(result["recommendation_gate"]["requirements"].values())


def test_any_failed_performance_requirement_prevents_review_candidate() -> None:
    rows = _passing_rows()
    result = build_validation_holdout_benchmark_v1(
        rows, _frozen(rows), _temporal(rows, false_reviews=15)
    )
    assert result["recommendation_gate"]["requirements"]["performance_wilson_gates_met"] is False
    assert result["recommendation_gate"]["status"] == "SHADOW_REVIEW_CANDIDATE"


def test_uncertain_is_excluded_and_soak_repetitions_never_increase_n() -> None:
    rows = _passing_rows()
    rows.append(_row(60, truth="uncertain", decision="cortar"))
    soak = [
        {"aoi_id": row["sample_id"], "repetition": repetition}
        for row in rows for repetition in (1, 2, 3)
    ]
    result = build_validation_holdout_benchmark_v1(
        rows, _frozen(rows), _temporal(rows), soak_runs=soak
    )
    assert result["dataset"]["total_samples"] == 61
    assert result["dataset"]["binary_samples"] == 60
    assert result["dataset"]["soak_run_count"] == 183
    assert result["dataset"]["soak_scientific_weight"] == 0
    assert result["methodology"]["uncertain_used_in_supervised_metrics"] is False


def test_frozen_preregistration_rejects_rule_gate_and_membership_changes() -> None:
    rows = _passing_rows()
    frozen = _frozen(rows)
    changed_rule = deepcopy(frozen)
    changed_rule["rule_definition"]["sentinel1_temporal_combined_status"] = "stable"
    with pytest.raises(HoldoutIntegrityError, match="rule_definition_changed"):
        validate_preregistration(changed_rule, require_frozen=True)
    changed_gate = deepcopy(frozen)
    changed_gate["engineering_gates"]["minimum_binary_truths"] = 10
    with pytest.raises(HoldoutIntegrityError, match="engineering_gates_changed"):
        validate_preregistration(changed_gate, require_frozen=True)
    with pytest.raises(HoldoutIntegrityError, match="membership_changed"):
        build_validation_holdout_benchmark_v1(rows[:-1], frozen, _temporal(rows[:-1]))


def test_development_holdout_identity_leakage_is_blocked() -> None:
    rows = [
        _row(1, truth="cut", decision="cortar", cohort="development"),
        _row(2, truth="no_cut", decision="nao_cortar", cohort="holdout"),
    ]
    rows[1]["analysis_id"] = rows[0]["analysis_id"]
    with pytest.raises(HoldoutIntegrityError, match="duplicate_analysis_id"):
        freeze_preregistration(create_preregistration(), rows)


def test_artifact_is_deterministic_and_contains_no_raw_sensitive_fields() -> None:
    rows = _passing_rows()
    original = deepcopy(rows)
    preregistration = _frozen(rows)
    kwargs = {"generated_at": "2026-09-07T00:00:00+00:00"}
    first = build_validation_holdout_benchmark_v1(rows, preregistration, _temporal(rows), **kwargs)
    second = build_validation_holdout_benchmark_v1(rows, preregistration, _temporal(rows), **kwargs)
    assert first == second
    assert rows == original
    serialized = json.dumps(first, allow_nan=False)
    assert '"geometry"' not in serialized
    assert '"notes"' not in serialized
    assert first["methodology"]["candidate_rules_evaluated"] == ["B"]
    assert first["methodology"]["recommendation_changed"] is False


def test_repository_defaults_existing_and_new_samples_to_development_and_locks_cohort(tmp_path: Path) -> None:
    database = tmp_path / "validation.sqlite3"
    repository = ValidationSampleRepository(database)
    snapshot = {"aoi": {"geometry_geojson": {"type": "Polygon", "coordinates": []}}}
    record = {
        "sample_id": "one", "analysis_id": "analysis-one", "schema_version": 1,
        "created_at": "2026-09-07T00:00:00+00:00", "vegetation_class": "shrub",
        "maintenance_truth": "cut", "validation_source": "field_inspection",
        "reference_date": "2026-09-07", "snapshot_json": json.dumps(snapshot),
        "aoi_fingerprint": "unique-aoi",
    }
    repository.insert(record)
    assert repository.get("one")["cohort"] == "development"
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match="cohort is immutable"):
        connection.execute("UPDATE validation_samples SET cohort='holdout' WHERE sample_id='one'")


def test_legacy_samples_are_migrated_to_development(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    repository = ValidationSampleRepository(database)
    record = {
        "sample_id": "legacy", "analysis_id": "analysis-legacy", "schema_version": 1,
        "created_at": "2026-09-07T00:00:00+00:00", "vegetation_class": "shrub",
        "maintenance_truth": "cut", "validation_source": "field_inspection",
        "reference_date": "2026-09-07", "snapshot_json": json.dumps({"aoi": {}}),
    }
    repository.insert(record)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER validation_samples_cohort_immutable")
        connection.execute("DROP TRIGGER validation_holdout_aoi_unique_insert")
        connection.execute("DROP TRIGGER validation_samples_cohort_valid_insert")
        connection.execute("ALTER TABLE validation_samples DROP COLUMN cohort")
        connection.execute("ALTER TABLE validation_samples DROP COLUMN aoi_fingerprint")
        connection.execute("DELETE FROM validation_schema_migrations WHERE version = 2")
    migrated = ValidationSampleRepository(database)
    assert migrated.get("legacy")["cohort"] == "development"


def test_repository_blocks_aoi_duplication_when_holdout_is_involved(tmp_path: Path) -> None:
    repository = ValidationSampleRepository(tmp_path / "validation.sqlite3")
    base = {
        "schema_version": 1, "created_at": "2026-09-07T00:00:00+00:00",
        "vegetation_class": "shrub", "maintenance_truth": "cut",
        "validation_source": "field_inspection", "reference_date": "2026-09-07",
        "aoi_fingerprint": "same-aoi", "snapshot_json": json.dumps({"aoi": {}}),
    }
    repository.insert({**base, "sample_id": "dev", "analysis_id": "analysis-dev"})
    with pytest.raises(DuplicateAoiError):
        repository.insert({**base, "sample_id": "holdout", "analysis_id": "analysis-holdout", "cohort": "holdout"})


def test_holdout_endpoint_is_read_only_and_auditable(client, tmp_path: Path) -> None:
    rows = _passing_rows()
    artifact = build_validation_holdout_benchmark_v1(
        rows, _frozen(rows), _temporal(rows), generated_at="2026-09-07T00:00:00+00:00"
    )
    path = tmp_path / "holdout.json"
    write_json_atomic(path, artifact)
    app.dependency_overrides[get_validation_holdout_benchmark_path] = lambda: path
    response = client.get("/api/validation-holdout-benchmark")
    assert response.status_code == 200
    assert response.json()["recommendation_gate"]["status"] == "REVIEW_MODE_CANDIDATE"

    app.dependency_overrides[get_validation_holdout_benchmark_path] = lambda: tmp_path / "missing.json"
    assert client.get("/api/validation-holdout-benchmark").status_code == 404
    path.write_text('{"schema_version":"0"}', encoding="utf-8")
    app.dependency_overrides[get_validation_holdout_benchmark_path] = lambda: path
    assert client.get("/api/validation-holdout-benchmark").status_code == 503
