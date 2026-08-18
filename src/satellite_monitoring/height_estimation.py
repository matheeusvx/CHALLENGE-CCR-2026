"""Inferencia leve e fail-soft da faixa experimental de altura v0."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

MODEL_VERSION = "height-estimator-v0"
MODEL_FEATURES = ("red_reflectance", "nir_reflectance", "ndvi")
REFERENCE_THRESHOLD_CM = 30
LOW_DECISION_THRESHOLD = 0.35
HIGH_DECISION_THRESHOLD = 0.65
MEDIUM_CONFIDENCE_LOW = 0.20
MEDIUM_CONFIDENCE_HIGH = 0.80
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "height_estimator_v0.json"


def _base_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "estimated_class": None,
        "probability_gt_30_cm": None,
        "confidence": None,
        "reference_threshold_cm": REFERENCE_THRESHOLD_CM,
        "model_version": None,
    }


def disabled_height_estimation() -> dict[str, Any]:
    return _base_result("disabled")


def unavailable_height_estimation() -> dict[str, Any]:
    return _base_result("unavailable")


def _finite_vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != len(MODEL_FEATURES):
        raise ValueError(f"{name} must contain exactly three values.")
    numbers = [float(item) for item in value]
    if not all(math.isfinite(item) for item in numbers):
        raise ValueError(f"{name} contains a non-finite value.")
    return numbers


def load_height_model(path: str | Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    model = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(model, dict):
        raise ValueError("Height model must be a JSON object.")
    if model.get("model_version") != MODEL_VERSION:
        raise ValueError("Incompatible height model version.")
    if model.get("features") != list(MODEL_FEATURES):
        raise ValueError("Incompatible height model features.")
    means = _finite_vector(model.get("scaler_mean"), "scaler_mean")
    scales = _finite_vector(model.get("scaler_scale"), "scaler_scale")
    coefficients = _finite_vector(model.get("coefficients"), "coefficients")
    if any(value <= 0 for value in scales):
        raise ValueError("scaler_scale must be positive.")
    intercept = float(model.get("intercept"))
    if not math.isfinite(intercept):
        raise ValueError("intercept must be finite.")
    return {
        **model,
        "scaler_mean": means,
        "scaler_scale": scales,
        "coefficients": coefficients,
        "intercept": intercept,
    }


def _probability_gt_30(features: list[float], model: Mapping[str, Any]) -> float:
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
        probability = 1.0 / (1.0 + math.exp(-logit))
    else:
        exponential = math.exp(logit)
        probability = exponential / (1.0 + exponential)
    return min(1.0, max(0.0, probability))


def estimate_height_class(
    features: Mapping[str, Any],
    *,
    model_path: str | Path = DEFAULT_MODEL_PATH,
) -> dict[str, Any]:
    """Retorna evidência experimental; qualquer falha resulta em unavailable."""
    try:
        values = [float(features[name]) for name in MODEL_FEATURES]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Height features must be finite.")
        model = load_height_model(model_path)
        probability = _probability_gt_30(values, model)
        if probability <= LOW_DECISION_THRESHOLD:
            estimated_class = "le_30_cm"
        elif probability >= HIGH_DECISION_THRESHOLD:
            estimated_class = "gt_30_cm"
        else:
            estimated_class = "inconclusive"
        confidence = (
            "medium"
            if probability <= MEDIUM_CONFIDENCE_LOW
            or probability >= MEDIUM_CONFIDENCE_HIGH
            else "low"
        )
        return {
            "status": "experimental",
            "estimated_class": estimated_class,
            "probability_gt_30_cm": probability,
            "confidence": confidence,
            "reference_threshold_cm": REFERENCE_THRESHOLD_CM,
            "model_version": MODEL_VERSION,
        }
    except Exception:
        return unavailable_height_estimation()
