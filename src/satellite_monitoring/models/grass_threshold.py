"""Contrato matematico canonico do estimador binario experimental v0."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

MODEL_VERSION = "height-estimator-v0"
MODEL_FEATURES = ("red_reflectance", "nir_reflectance", "ndvi")
REFERENCE_THRESHOLD_CM = 30
LOW_DECISION_THRESHOLD = 0.35
HIGH_DECISION_THRESHOLD = 0.65
MEDIUM_CONFIDENCE_LOW = 0.20
MEDIUM_CONFIDENCE_HIGH = 0.80
CALIBRATION_STATUS = "uncalibrated"


def model_feature_vector(features: Mapping[str, Any]) -> list[float]:
    values = [float(features[name]) for name in MODEL_FEATURES]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Height features must be finite.")
    return values


def score_gt_30_cm(features: Sequence[float], model: Mapping[str, Any]) -> float:
    """Retorna o score logistico v0; nao implica probabilidade calibrada."""
    normalized = [
        (value - mean) / scale
        for value, mean, scale in zip(
            features, model["scaler_mean"], model["scaler_scale"]
        )
    ]
    logit = float(model["intercept"]) + sum(
        coefficient * value
        for coefficient, value in zip(model["coefficients"], normalized)
    )
    if logit >= 0:
        score = 1.0 / (1.0 + math.exp(-logit))
    else:
        exponential = math.exp(logit)
        score = exponential / (1.0 + exponential)
    return min(1.0, max(0.0, score))


def classify_height_score(
    score: float,
    *,
    lower: float = LOW_DECISION_THRESHOLD,
    upper: float = HIGH_DECISION_THRESHOLD,
) -> str:
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("Height score must be finite and between zero and one.")
    if not 0.0 <= lower < upper <= 1.0:
        raise ValueError("Selective thresholds must satisfy 0 <= lower < upper <= 1.")
    if score <= lower:
        return "le_30_cm"
    if score >= upper:
        return "gt_30_cm"
    return "inconclusive"


def height_score_confidence(score: float) -> str:
    return (
        "medium"
        if score <= MEDIUM_CONFIDENCE_LOW or score >= MEDIUM_CONFIDENCE_HIGH
        else "low"
    )
