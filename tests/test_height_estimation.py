"""Testes do contrato fail-soft de inferencia de altura."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.satellite_monitoring.height_estimation import (
    MODEL_FEATURES,
    MODEL_VERSION,
    disabled_height_estimation,
    estimate_height_class,
    load_height_model,
)
from src.satellite_monitoring.raster_processing import (
    RasterSceneData,
    physical_reflectance_medians,
)


def _model(tmp_path, *, mean=None, scale=None, coefficients=None, intercept=0.0):
    path = tmp_path / "height.json"
    path.write_text(
        json.dumps(
            {
                "model_version": MODEL_VERSION,
                "features": list(MODEL_FEATURES),
                "scaler_mean": mean or [0.0, 0.0, 0.0],
                "scaler_scale": scale or [1.0, 1.0, 1.0],
                "coefficients": coefficients or [1.0, 0.0, 0.0],
                "intercept": intercept,
            }
        ),
        encoding="utf-8",
    )
    return path


def _features(red: float) -> dict[str, float]:
    return {"red_reflectance": red, "nir_reflectance": 0.3, "ndvi": 0.4}


@pytest.mark.parametrize(
    ("red", "expected"),
    [(-1.0, "le_30_cm"), (1.0, "gt_30_cm"), (0.0, "inconclusive")],
)
def test_probability_gates(red, expected, tmp_path) -> None:
    result = estimate_height_class(_features(red), model_path=_model(tmp_path))
    assert result["status"] == "experimental"
    assert result["estimated_class"] == expected
    assert 0 <= result["score_gt_30_cm"] <= 1
    assert result["probability_gt_30_cm"] == result["score_gt_30_cm"]
    assert result["calibration_status"] == "uncalibrated"


def test_normalization_and_confidence(tmp_path) -> None:
    model = _model(
        tmp_path,
        mean=[10.0, 0.0, 0.0],
        scale=[2.0, 1.0, 1.0],
        coefficients=[2.0, 0.0, 0.0],
    )
    result = estimate_height_class(_features(12.0), model_path=model)
    assert result["score_gt_30_cm"] == pytest.approx(0.8807970779)
    assert result["probability_gt_30_cm"] == result["score_gt_30_cm"]
    assert result["estimated_class"] == "gt_30_cm"
    assert result["confidence"] == "medium"
    assert result["model_version"] == MODEL_VERSION


def test_inconclusive_confidence_is_low(tmp_path) -> None:
    result = estimate_height_class(_features(0.0), model_path=_model(tmp_path))
    assert result["estimated_class"] == "inconclusive"
    assert result["confidence"] == "low"


def test_canonical_score_preserves_v0_numeric_output() -> None:
    result = estimate_height_class(
        {"red_reflectance": 0.0979, "nir_reflectance": 0.2683, "ndvi": 0.4}
    )
    assert result["score_gt_30_cm"] == pytest.approx(0.412857580797846)
    assert result["probability_gt_30_cm"] == result["score_gt_30_cm"]
    assert result["calibration_status"] == "uncalibrated"


@pytest.mark.parametrize(
    "features",
    [
        {"red_reflectance": 0.2, "nir_reflectance": 0.3},
        {"red_reflectance": float("nan"), "nir_reflectance": 0.3, "ndvi": 0.4},
        {"red_reflectance": "invalid", "nir_reflectance": 0.3, "ndvi": 0.4},
    ],
)
def test_invalid_features_are_fail_soft(features, tmp_path) -> None:
    assert estimate_height_class(features, model_path=_model(tmp_path))["status"] == (
        "unavailable"
    )


def test_missing_or_invalid_model_is_fail_soft(tmp_path) -> None:
    missing = estimate_height_class(_features(0.0), model_path=tmp_path / "missing.json")
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{", encoding="utf-8")
    invalid = estimate_height_class(_features(0.0), model_path=invalid_path)
    assert missing["status"] == "unavailable"
    assert invalid["status"] == "unavailable"


def test_model_contract_is_validated(tmp_path) -> None:
    model = load_height_model(_model(tmp_path))
    assert model["features"] == list(MODEL_FEATURES)
    incompatible = _model(tmp_path)
    payload = json.loads(incompatible.read_text(encoding="utf-8"))
    payload["model_version"] = "future-version"
    incompatible.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_height_model(incompatible)


def test_disabled_contract() -> None:
    result = disabled_height_estimation()
    assert result["status"] == "disabled"
    assert result["estimated_class"] is None
    assert result["score_gt_30_cm"] is None
    assert result["probability_gt_30_cm"] is None
    assert result["calibration_status"] == "uncalibrated"
    assert result["reference_threshold_cm"] == 30


def test_purity_gate_abstains_before_model_inference(tmp_path) -> None:
    result = estimate_height_class(
        {
            **_features(1.0),
            "vegetation_fraction": 0.1,
            "height_valid_pixel_count": 2,
            "height_total_pixel_count": 20,
            "mixed_pixel_risk": "high",
            "height_purity_gate_passed": False,
            "height_purity_gate_reasons": ["insufficient_vegetation_fraction"],
        },
        model_path=_model(tmp_path),
    )

    assert result["status"] == "experimental"
    assert result["estimated_class"] == "inconclusive"
    assert result["score_gt_30_cm"] is None
    assert result["vegetation_fraction"] == pytest.approx(0.1)
    assert result["mixed_pixel_risk"] == "high"


def test_pipeline_features_use_physical_reflectance_scale_and_offset() -> None:
    asset = SimpleNamespace(
        extra_fields={"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
    )
    item = SimpleNamespace(assets={"B04": asset, "B08": asset})
    raster = RasterSceneData(
        red=np.asarray([[1979.0]]),
        nir=np.asarray([[3683.0]]),
        valid_mask=np.asarray([[True]]),
        total_pixel_count=1,
        aoi_coverage_percentage=100.0,
        partial_raster_coverage=False,
        red_asset="B04",
        nir_asset="B08",
        scl_asset=None,
        scl_class_percentages={},
        quality_messages=[],
        red_raw=np.asarray([[1979.0]]),
        nir_raw=np.asarray([[3683.0]]),
    )

    features = physical_reflectance_medians(item, raster)

    assert features["red_median_reflectance"] == pytest.approx(0.0979)
    assert features["nir_median_reflectance"] == pytest.approx(0.2683)
