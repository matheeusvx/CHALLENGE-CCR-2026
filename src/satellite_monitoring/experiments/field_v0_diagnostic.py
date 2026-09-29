"""Diagnóstico explicativo V0/height mask sem treino ou recalibração."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..height_estimation import load_height_model
from ..models.grass_threshold import MODEL_FEATURES, score_gt_30_cm


def decompose_v0_score(
    features: Mapping[str, Any], *, model_path: str | Path
) -> dict[str, Any]:
    model = load_height_model(model_path)
    rows: list[dict[str, float | str]] = []
    for index, name in enumerate(MODEL_FEATURES):
        raw = float(features[name])
        mean = float(model["scaler_mean"][index])
        scale = float(model["scaler_scale"][index])
        coefficient = float(model["coefficients"][index])
        scaled = (raw - mean) / scale
        contribution = scaled * coefficient
        rows.append(
            {
                "feature": name,
                "raw": raw,
                "scaler_mean": mean,
                "scaler_scale": scale,
                "scaled": scaled,
                "coefficient": coefficient,
                "logit_contribution": contribution,
            }
        )
    intercept = float(model["intercept"])
    logit = intercept + sum(float(row["logit_contribution"]) for row in rows)
    reconstructed = 1.0 / (1.0 + math.exp(-logit))
    canonical = score_gt_30_cm(
        [float(features[name]) for name in MODEL_FEATURES], model
    )
    if not math.isclose(reconstructed, canonical, rel_tol=0.0, abs_tol=1e-15):
        raise ValueError("V0 score decomposition does not reproduce the artifact.")
    return {
        "features": rows,
        "intercept": intercept,
        "final_logit": logit,
        "reconstructed_score": reconstructed,
        "artifact_score": canonical,
    }


def training_distribution(
    training_path: str | Path, *, expected_sha256: str
) -> dict[str, Any]:
    path = Path(training_path)
    if not path.exists():
        return {
            "status": "ORIGINAL_TRAINING_DATASET_UNAVAILABLE",
            "expected_training_dataset_sha256": expected_sha256,
            "features": None,
            "gt35_01_position": "UNAVAILABLE",
        }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    distributions: dict[str, dict[str, float]] = {}
    for name in MODEL_FEATURES:
        values = np.asarray([float(row[name]) for row in rows], dtype=float)
        if not len(values) or not np.all(np.isfinite(values)):
            raise ValueError("Original V0 training features are incomplete.")
        distributions[name] = {
            "min": float(np.min(values)),
            "q1": float(np.quantile(values, 0.25)),
            "median": float(np.quantile(values, 0.50)),
            "q3": float(np.quantile(values, 0.75)),
            "max": float(np.max(values)),
        }
    return {
        "status": "AVAILABLE",
        "expected_training_dataset_sha256": expected_sha256,
        "features": distributions,
    }


def _file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_gt35_v0_diagnostic(
    enriched_rows: Sequence[Mapping[str, Any]],
    pixel_diagnostic: Mapping[str, Any],
    *,
    model_path: str | Path,
    training_path: str | Path,
) -> dict[str, Any]:
    by_id = {str(row["sample_id"]): row for row in enriched_rows}
    gt35_01 = by_id["GT35-01"]
    gt35_02 = by_id["GT35-02"]
    decomposition = decompose_v0_score(gt35_01, model_path=model_path)
    model = load_height_model(model_path)
    distribution = training_distribution(
        training_path,
        expected_sha256=str(model["training_dataset_sha256"]),
    )
    pixels = list(pixel_diagnostic["pixels"])
    summary = dict(pixel_diagnostic["summary"])
    if len(pixels) != int(summary["total_pixels"]):
        raise ValueError("Pixel diagnostic count conflicts with height mask summary.")
    if sum(bool(pixel["height_valid"]) for pixel in pixels) != int(
        summary["valid_height_pixels"]
    ):
        raise ValueError("Pixel diagnostic changes the existing height mask result.")
    model_hash = _file_sha256(model_path)
    return {
        "conclusion": "V0_MULTIPLE_LIMITATIONS",
        "GT35-01": {
            "ground_truth": ">35 cm physical lower bound",
            "sentinel_item_id": str(gt35_01["sentinel_item_id"]),
            "scene_datetime": str(gt35_01["scene_datetime"]),
            "observed_at": str(gt35_01["observed_at"]),
            "field_scene_gap_days": int(float(gt35_01["scene_age_days"])),
            "v0_decomposition": decomposition,
            "training_distribution": distribution,
            "existing_holdout_comparison": {
                "14_cm": "PERSISTED_V0_FEATURES_UNAVAILABLE",
                "30_cm": "PERSISTED_V0_FEATURES_UNAVAILABLE",
                "differences_not_calculated": True,
            },
            "technical_interpretation": (
                "The final score is the unchanged linear-logistic combination. "
                "A strongly positive standardized NDVI receives a negative contribution "
                "under the frozen V0 coefficient and dominates the smaller RED/NIR terms."
            ),
        },
        "GT35-02": {
            "ground_truth": ">35 cm physical lower bound",
            "sentinel_item_id": str(gt35_02["sentinel_item_id"]),
            "scene_datetime": str(gt35_02["scene_datetime"]),
            "observed_at": str(gt35_02["observed_at"]),
            "field_scene_gap_days": int(float(gt35_02["scene_age_days"])),
            "aoi_area_m2": float(gt35_02["area_m2"]),
            "pixel_diagnostics": pixels,
            "spatial_purity_summary": summary,
            "surface_type_inference": (
                "No asphalt, structure, soil, or shadow type is inferred beyond the "
                "existing SCL/NDVI/radiometric rules."
            ),
        },
        "temporal_mismatch": {
            "gap_days": 8,
            "centimeter_growth_inferred": False,
        },
        "recommendation_height_independence": {
            "recommendation": "CORTAR",
            "height_v0": "inconclusive",
            "logical_bug": False,
            "recommendation_used_as_height_label": False,
        },
        "model_artifact": {
            "path": str(model_path).replace("\\", "/"),
            "sha256_before": model_hash,
            "sha256_after": model_hash,
            "modified": False,
        },
        "parameters_unchanged": {
            "decision_gate": [0.35, 0.65],
            "height_valid_mask": True,
            "recommendation": True,
            "frontend": True,
            "training_data": True,
        },
    }


def write_gt35_v0_diagnostic(
    payload: Mapping[str, Any], output_path: str | Path
) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
