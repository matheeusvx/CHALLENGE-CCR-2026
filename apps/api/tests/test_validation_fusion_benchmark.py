from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import (
    get_validation_repository,
    get_validation_temporal_benchmark_runner,
)
from apps.api.app.main import app
from apps.api.app.validation.fusion_benchmark import (
    build_validation_fusion_benchmark,
)
from apps.api.app.validation.repository import ValidationSampleRepository


def sample(
    sample_id: str,
    truth: str,
    s2: str,
    s1: str,
    *,
    vv: str | None = None,
    vh: str | None = None,
) -> dict:
    return {
        "sample_id": sample_id,
        "maintenance_truth": truth,
        "s2_decision": s2,
        "combined_status": s1,
        "vv_temporal_status": vv or s1,
        "vh_temporal_status": vh or s1,
        "canonical_relative_orbit": 53,
    }


def benchmark(samples: list[dict]) -> dict:
    return {"samples": samples}


def test_rule_a_captures_false_negative_and_counts_correct_review() -> None:
    result = build_validation_fusion_benchmark(
        benchmark(
            [
                sample("error", "cut", "nao_cortar", "increasing"),
                sample("correct-trigger", "no_cut", "nao_cortar", "increasing"),
                sample("correct-quiet", "no_cut", "nao_cortar", "stable"),
            ]
        )
    )
    rule = result["candidate_rules"]["A"]

    assert rule["eligible_samples"] == 3
    assert rule["triggered_samples"] == 2
    assert rule["s2_errors_triggered"] == 1
    assert rule["s2_correct_cases_triggered"] == 1
    assert rule["error_capture_rate"] == 1.0
    assert rule["false_review_rate"] == 0.5
    assert rule["precision_for_detecting_s2_error"] == 0.5


def test_rule_b_only_triggers_s2_cut_with_mixed_s1() -> None:
    result = build_validation_fusion_benchmark(
        benchmark(
            [
                sample("error", "no_cut", "cortar", "mixed"),
                sample("correct", "cut", "cortar", "mixed"),
                sample("stable", "no_cut", "cortar", "stable"),
            ]
        )
    )
    rule = result["candidate_rules"]["B"]

    assert rule["triggered_samples"] == 2
    assert rule["s2_errors_triggered"] == 1
    assert rule["s2_correct_cases_triggered"] == 1


def test_combinations_use_union_without_double_counting() -> None:
    samples = [
        sample("a", "cut", "nao_cortar", "increasing"),
        sample("b", "no_cut", "cortar", "mixed"),
        sample("c", "no_cut", "cortar", "stable"),
        sample("quiet", "cut", "cortar", "increasing"),
    ]
    result = build_validation_fusion_benchmark(benchmark(samples))["combinations"]

    assert result["A"]["triggered_samples"] == 1
    assert result["B"]["triggered_samples"] == 1
    assert result["A+B"]["triggered_samples"] == 2
    assert result["A+B+C"]["triggered_samples"] == 3
    assert result["A+B"]["error_capture_rate"] == pytest.approx(2 / 3)


def test_uncertain_ground_truth_is_separate_from_binary_evaluation() -> None:
    result = build_validation_fusion_benchmark(
        benchmark(
            [
                sample("binary", "cut", "cortar", "stable"),
                sample("uncertain", "uncertain", "nao_cortar", "increasing"),
            ]
        )
    )

    assert result["partitions"]["binary_evaluation_samples"] == 1
    assert result["partitions"]["uncertain_ground_truth_samples"] == 1
    uncertain = result["sample_matrix"][1]
    assert uncertain["evaluation_partition"] == "uncertain_ground_truth"
    assert uncertain["s2_was_correct"] is None


def test_s2_inconclusive_is_only_complementary_evidence() -> None:
    result = build_validation_fusion_benchmark(
        benchmark([sample("inc", "cut", "inconclusivo", "mixed")])
    )
    rule = result["candidate_rules"]["D"]

    assert result["partitions"]["s2_inconclusive_samples"] == 1
    assert rule["signal"] == "complementary_evidence"
    assert rule["eligible_samples"] == 1
    assert rule["triggered_samples"] == 1
    assert rule["error_capture_rate"] is None


def test_error_capture_and_false_review_use_global_comparable_denominators() -> None:
    result = build_validation_fusion_benchmark(
        benchmark(
            [
                sample("captured", "cut", "nao_cortar", "increasing"),
                sample("missed", "no_cut", "cortar", "increasing"),
                sample("false-review", "no_cut", "nao_cortar", "increasing"),
                sample("correct-quiet", "cut", "cortar", "increasing"),
            ]
        )
    )["candidate_rules"]["A"]

    assert result["error_capture_rate"] == 0.5
    assert result["false_review_rate"] == 0.5


def test_recommendation_is_unchanged_and_never_emitted() -> None:
    source = benchmark([sample("one", "cut", "nao_cortar", "increasing")])
    original = deepcopy(source)

    result = build_validation_fusion_benchmark(source)

    assert source == original
    assert "recommendation" not in result
    assert result["methodology"]["recommendation_changed"] is False
    assert result["methodology"]["runtime_fusion_applied"] is False


def test_get_validation_fusion_benchmark_endpoint(
    client: TestClient, tmp_path: Path
) -> None:
    repository = ValidationSampleRepository(tmp_path / "validation.sqlite3")

    class EmptyTemporalRunner:
        def run(self, rows):
            assert rows == []
            return {"samples": []}

    app.dependency_overrides[get_validation_repository] = lambda: repository
    app.dependency_overrides[get_validation_temporal_benchmark_runner] = (
        EmptyTemporalRunner
    )

    response = client.get("/api/validation-fusion-benchmark")

    assert response.status_code == 200
    assert response.json()["partitions"]["binary_evaluation_samples"] == 0
    assert response.json()["methodology"]["runtime_fusion_applied"] is False
