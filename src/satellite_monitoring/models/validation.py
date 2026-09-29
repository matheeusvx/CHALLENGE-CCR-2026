"""Validacao group-aware e avaliacao seletiva do baseline v0."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.base import clone
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .grass_threshold import MODEL_FEATURES

TRAINING_RANDOM_SEED = 20260818
MAX_VALIDATION_FOLDS = 5
RISK_COVERAGE_THRESHOLDS = (
    (0.20, 0.80),
    (0.25, 0.75),
    (0.30, 0.70),
    (0.35, 0.65),
    (0.40, 0.60),
)


def build_training_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=TRAINING_RANDOM_SEED,
                ),
            ),
        ]
    )


def training_arrays(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not rows:
        raise ValueError("No training samples with valid features.")
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x = np.asarray(
        [[float(row[feature]) for feature in MODEL_FEATURES] for row in ordered],
        dtype=float,
    )
    y = np.asarray([int(row["target"]) for row in ordered], dtype=int)
    groups = np.asarray([str(row["group_id"]) for row in ordered], dtype=object)
    if not np.all(np.isfinite(x)):
        raise ValueError("Training features contain non-finite values.")
    if set(y.tolist()) != {0, 1}:
        raise ValueError("Training data must contain both binary targets.")
    if len(set(str(value) for value in groups)) < 2:
        raise ValueError("Training data must contain at least two local groups.")
    return x, y, groups


def grouped_validation_predictions(
    x: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> tuple[np.ndarray | None, int, list[str]]:
    limitations: list[str] = []
    unique_groups = len(set(str(value) for value in groups))
    for fold_count in range(min(MAX_VALIDATION_FOLDS, unique_groups), 1, -1):
        splitter = StratifiedGroupKFold(
            n_splits=fold_count,
            shuffle=True,
            random_state=TRAINING_RANDOM_SEED,
        )
        splits = list(splitter.split(x, y, groups))
        if not all(
            set(y[train].tolist()) == {0, 1} and set(y[test].tolist()) == {0, 1}
            for train, test in splits
        ):
            continue
        scores = np.full(y.shape, np.nan, dtype=float)
        for train, test in splits:
            model = clone(build_training_pipeline())
            model.fit(x[train], y[train])
            scores[test] = model.predict_proba(x[test])[:, 1]
        if np.all(np.isfinite(scores)):
            return scores, fold_count, limitations
    limitations.append(
        "No StratifiedGroupKFold configuration produced train/test folds with both classes."
    )
    return None, 0, limitations


def experimental_validation_metrics(
    y: np.ndarray, scores: np.ndarray | None
) -> dict[str, Any]:
    if scores is None:
        return {
            "roc_auc": None,
            "balanced_accuracy": None,
            "precision_gt_30": None,
            "recall_gt_30": None,
            "f1_gt_30": None,
            "confusion_matrix": None,
        }
    predicted = (scores >= 0.5).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y, scores)),
        "balanced_accuracy": float(balanced_accuracy_score(y, predicted)),
        "precision_gt_30": float(precision_score(y, predicted, zero_division=0)),
        "recall_gt_30": float(recall_score(y, predicted, zero_division=0)),
        "f1_gt_30": float(f1_score(y, predicted, zero_division=0)),
        "confusion_matrix": confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
    }


def selective_validation_metrics(
    y: np.ndarray,
    scores: np.ndarray,
    *,
    lower: float,
    upper: float,
) -> dict[str, Any]:
    y_values = np.asarray(y, dtype=int)
    score_values = np.asarray(scores, dtype=float)
    if y_values.shape != score_values.shape:
        raise ValueError("Targets and scores must have the same shape.")
    if not np.all(np.isfinite(score_values)):
        raise ValueError("Selective-validation scores must be finite.")
    if not 0.0 <= lower < upper <= 1.0:
        raise ValueError("Selective thresholds must satisfy 0 <= lower < upper <= 1.")
    total = int(y_values.size)
    decided_mask = (score_values <= lower) | (score_values >= upper)
    decided = int(np.count_nonzero(decided_mask))
    abstained = total - decided
    coverage = float(decided / total) if total else 0.0
    base = {
        "lower_threshold": lower,
        "upper_threshold": upper,
        "total_samples": total,
        "decided_samples": decided,
        "abstained_samples": abstained,
        "decision_coverage": coverage,
        "abstention_rate": 1.0 - coverage if total else 0.0,
    }
    if not decided:
        return {
            **base,
            "precision_gt_30": None,
            "recall_gt_30": None,
            "f1_gt_30": None,
            "balanced_accuracy": None,
            "confusion_matrix_decided": [[0, 0], [0, 0]],
            "false_positive_count": 0,
            "false_negative_count": 0,
            "true_positive_count": 0,
            "true_negative_count": 0,
        }
    decided_y = y_values[decided_mask]
    decided_scores = score_values[decided_mask]
    predicted = (decided_scores >= upper).astype(int)
    matrix = confusion_matrix(decided_y, predicted, labels=[0, 1])
    tn, fp, fn, tp = (int(value) for value in matrix.ravel())
    both_classes = set(decided_y.tolist()) == {0, 1}
    return {
        **base,
        "precision_gt_30": float(precision_score(decided_y, predicted, zero_division=0)),
        "recall_gt_30": float(recall_score(decided_y, predicted, zero_division=0)),
        "f1_gt_30": float(f1_score(decided_y, predicted, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(decided_y, predicted))
        if both_classes
        else None,
        "confusion_matrix_decided": matrix.tolist(),
        "false_positive_count": fp,
        "false_negative_count": fn,
        "true_positive_count": tp,
        "true_negative_count": tn,
    }


def risk_coverage_table(
    y: np.ndarray,
    scores: np.ndarray,
    thresholds: Sequence[tuple[float, float]] = RISK_COVERAGE_THRESHOLDS,
) -> list[dict[str, Any]]:
    return [
        selective_validation_metrics(y, scores, lower=lower, upper=upper)
        for lower, upper in thresholds
    ]
