"""Testes das metricas seletivas do gate operacional v0."""

from __future__ import annotations

import numpy as np
import pytest

from src.satellite_monitoring.models.validation import (
    risk_coverage_table,
    selective_validation_metrics,
)


def test_selective_metrics_use_only_decided_samples() -> None:
    y = np.asarray([0, 0, 1, 1, 1])
    scores = np.asarray([0.1, 0.5, 0.9, 0.2, 0.6])
    metrics = selective_validation_metrics(y, scores, lower=0.35, upper=0.65)

    assert metrics["total_samples"] == 5
    assert metrics["decided_samples"] == 3
    assert metrics["abstained_samples"] == 2
    assert metrics["decision_coverage"] == pytest.approx(0.6)
    assert metrics["abstention_rate"] == pytest.approx(0.4)
    assert metrics["confusion_matrix_decided"] == [[1, 0], [1, 1]]
    assert metrics["false_positive_count"] == 0
    assert metrics["false_negative_count"] == 1


def test_no_decisions_has_no_division_by_zero() -> None:
    metrics = selective_validation_metrics(
        np.asarray([0, 1]),
        np.asarray([0.45, 0.55]),
        lower=0.35,
        upper=0.65,
    )
    assert metrics["decided_samples"] == 0
    assert metrics["decision_coverage"] == 0.0
    assert metrics["abstention_rate"] == 1.0
    assert metrics["precision_gt_30"] is None


def test_risk_coverage_thresholds_are_deterministic() -> None:
    y = np.asarray([0, 0, 1, 1])
    scores = np.asarray([0.1, 0.3, 0.7, 0.9])
    thresholds = ((0.2, 0.8), (0.4, 0.6))
    first = risk_coverage_table(y, scores, thresholds)
    second = risk_coverage_table(y, scores, thresholds)
    assert first == second
    assert first[0]["decision_coverage"] == pytest.approx(0.5)
    assert first[1]["decision_coverage"] == pytest.approx(1.0)
