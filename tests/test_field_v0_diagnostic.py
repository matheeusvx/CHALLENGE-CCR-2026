"""Testes direcionados do diagnóstico explicativo GT35/V0."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.satellite_monitoring.experiments.field_v0_diagnostic import (
    build_gt35_v0_diagnostic,
    decompose_v0_score,
    write_gt35_v0_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "height_estimator_v0.json"


def _rows() -> list[dict[str, object]]:
    shared = {
        "sentinel_item_id": "causal-item",
        "scene_datetime": "2026-08-23T13:12:41.025000+00:00",
        "observed_at": "2026-08-31T12:00:00-03:00",
        "scene_age_days": 8,
    }
    return [
        {
            **shared,
            "sample_id": "GT35-01",
            "red_reflectance": 0.0219,
            "nir_reflectance": 0.24545,
            "ndvi": 0.8367741776572052,
        },
        {
            **shared,
            "sample_id": "GT35-02",
            "area_m2": 3557.4769335091114,
        },
    ]


def _pixels() -> dict[str, object]:
    pixels = [
        {
            "pixel_id": f"r0_c{index}",
            "height_valid": index < 2,
            "rejection_reason": [] if index < 2 else ["SCL_REJECTED"],
        }
        for index in range(7)
    ]
    return {
        "pixels": pixels,
        "summary": {
            "total_pixels": 7,
            "valid_height_pixels": 2,
            "invalid_height_pixels": 5,
        },
    }


def test_v0_decomposition_reproduces_existing_score_without_artifact_change() -> None:
    before = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    result = decompose_v0_score(_rows()[0], model_path=MODEL)
    after = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    assert result["reconstructed_score"] == pytest.approx(0.3536942160966543)
    assert result["reconstructed_score"] == pytest.approx(result["artifact_score"])
    assert before == after


def test_gt35_diagnostic_output_is_deterministic(tmp_path: Path) -> None:
    payload = build_gt35_v0_diagnostic(
        _rows(),
        _pixels(),
        model_path=MODEL,
        training_path=tmp_path / "missing-original-training.csv",
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_gt35_v0_diagnostic(payload, first)
    write_gt35_v0_diagnostic(payload, second)
    assert first.read_bytes() == second.read_bytes()
    assert (
        payload["GT35-01"]["training_distribution"]["status"]
        == "ORIGINAL_TRAINING_DATASET_UNAVAILABLE"
    )
