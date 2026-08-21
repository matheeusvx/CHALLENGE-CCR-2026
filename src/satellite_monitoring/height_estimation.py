"""Inferencia leve e fail-soft da faixa experimental de altura v0."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .features.vegetation_mask import (
    DEFAULT_MIN_HEIGHT_VALID_PIXELS,
    DEFAULT_MIN_VEGETATION_FRACTION,
)
from .models.grass_threshold import (
    CALIBRATION_STATUS,
    HIGH_DECISION_THRESHOLD,
    LOW_DECISION_THRESHOLD,
    MODEL_FEATURES,
    MODEL_VERSION,
    REFERENCE_THRESHOLD_CM,
    classify_height_score,
    height_score_confidence,
    model_feature_vector,
    score_gt_30_cm,
)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "height_estimator_v0.json"


def _base_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "estimated_class": None,
        "score_gt_30_cm": None,
        # Alias temporario para historicos/clientes v0; nao e probabilidade calibrada.
        "probability_gt_30_cm": None,
        "calibration_status": CALIBRATION_STATUS,
        "vegetation_fraction": None,
        "height_valid_pixel_count": None,
        "height_total_pixel_count": None,
        "mixed_pixel_risk": None,
        "confidence": None,
        "reference_threshold_cm": REFERENCE_THRESHOLD_CM,
        "model_version": None,
        "provenance": None,
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


def _purity_metadata(features: Mapping[str, Any]) -> dict[str, Any]:
    keys = {
        "vegetation_fraction",
        "height_valid_pixel_count",
        "height_total_pixel_count",
        "mixed_pixel_risk",
        "height_purity_gate_passed",
    }
    if not keys.intersection(features):
        # Compatibilidade para chamadas v0 e historicos sem diagnostico espacial.
        return {
            "available": False,
            "passed": True,
            "vegetation_fraction": None,
            "height_valid_pixel_count": None,
            "height_total_pixel_count": None,
            "mixed_pixel_risk": None,
            "reasons": [],
        }
    fraction = float(features["vegetation_fraction"])
    valid_count = int(features["height_valid_pixel_count"])
    total_count = int(features["height_total_pixel_count"])
    risk = str(features["mixed_pixel_risk"])
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        raise ValueError("vegetation_fraction must be finite and between zero and one.")
    if valid_count < 0 or total_count < 0 or valid_count > total_count:
        raise ValueError("Height pixel counts are inconsistent.")
    if risk not in {"low", "medium", "high"}:
        raise ValueError("mixed_pixel_risk is invalid.")
    reasons = list(features.get("height_purity_gate_reasons") or [])
    passed = bool(features.get("height_purity_gate_passed", True))
    passed &= fraction >= DEFAULT_MIN_VEGETATION_FRACTION
    passed &= valid_count >= DEFAULT_MIN_HEIGHT_VALID_PIXELS
    passed &= risk != "high"
    return {
        "available": True,
        "passed": passed,
        "vegetation_fraction": fraction,
        "height_valid_pixel_count": valid_count,
        "height_total_pixel_count": total_count,
        "mixed_pixel_risk": risk,
        "reasons": reasons,
    }


def estimate_height_class(
    features: Mapping[str, Any],
    *,
    model_path: str | Path = DEFAULT_MODEL_PATH,
) -> dict[str, Any]:
    """Retorna evidência experimental; qualquer falha resulta em unavailable."""
    try:
        purity = _purity_metadata(features)
        provenance = {
            "feature_pipeline": "height_valid_mask_v1",
            "score_semantics": "uncalibrated_logistic_score",
            "legacy_probability_alias": True,
            "height_mask_configuration": features.get("height_mask_configuration"),
            "purity_gate_reasons": purity["reasons"],
        }
        if not purity["passed"]:
            return {
                "status": "experimental",
                "estimated_class": "inconclusive",
                "score_gt_30_cm": None,
                "probability_gt_30_cm": None,
                "calibration_status": CALIBRATION_STATUS,
                "vegetation_fraction": purity["vegetation_fraction"],
                "height_valid_pixel_count": purity["height_valid_pixel_count"],
                "height_total_pixel_count": purity["height_total_pixel_count"],
                "mixed_pixel_risk": purity["mixed_pixel_risk"],
                "confidence": "low",
                "reference_threshold_cm": REFERENCE_THRESHOLD_CM,
                "model_version": MODEL_VERSION,
                "provenance": provenance,
            }
        values = model_feature_vector(features)
        model = load_height_model(model_path)
        score = score_gt_30_cm(values, model)
        return {
            "status": "experimental",
            "estimated_class": classify_height_score(score),
            "score_gt_30_cm": score,
            "probability_gt_30_cm": score,
            "calibration_status": CALIBRATION_STATUS,
            "vegetation_fraction": purity["vegetation_fraction"],
            "height_valid_pixel_count": purity["height_valid_pixel_count"],
            "height_total_pixel_count": purity["height_total_pixel_count"],
            "mixed_pixel_risk": purity["mixed_pixel_risk"],
            "confidence": height_score_confidence(score),
            "reference_threshold_cm": REFERENCE_THRESHOLD_CM,
            "model_version": MODEL_VERSION,
            "provenance": provenance,
        }
    except Exception:
        return unavailable_height_estimation()
