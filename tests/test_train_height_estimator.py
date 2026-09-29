"""Testes offline do treinamento group-aware do estimador v0."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

from scripts.train_height_estimator import (
    MODEL_FEATURES,
    binary_height_target,
    build_training_pipeline,
    grouped_validation_predictions,
    select_training_scene,
    train_artifact,
    training_group_id,
    write_model_artifact,
)


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(20):
        target = index % 2
        rows.append(
            {
                "sample_id": f"sample-{index:02d}",
                "group_id": f"SP-021|{index:03d}",
                "geometry_group_id": f"geometry-{index:03d}",
                "target": target,
                "red_reflectance": 0.12 + target * 0.05 + index * 0.0001,
                "nir_reflectance": 0.31 - target * 0.04 + index * 0.0001,
                "ndvi": 0.46 - target * 0.12 + index * 0.0001,
            }
        )
    return rows


def test_binary_target_combines_classes_one_and_two() -> None:
    assert binary_height_target(1) == 0
    assert binary_height_target(2) == 0
    assert binary_height_target(3) == 1
    with pytest.raises(ValueError):
        binary_height_target(4)


def test_grouping_uses_road_and_km_local() -> None:
    assert training_group_id({"road": "SP-021", "km_m": "12345"}) == (
        "SP-021|12345"
    )


def test_training_scene_is_nearest_valid_within_five_days() -> None:
    records = [
        {
            "item_id": "future-nearest",
            "datetime": "2026-03-16T13:00:00Z",
            "accepted_for_timeseries": True,
            "scene_quality_score": 80,
            "valid_pixel_percentage": 90,
            "cloud_cover": 4,
        },
        {
            "item_id": "past-farther",
            "datetime": "2026-03-09T13:00:00Z",
            "accepted_for_timeseries": True,
            "scene_quality_score": 99,
            "valid_pixel_percentage": 99,
            "cloud_cover": 0,
        },
    ]

    selected = select_training_scene(records, date(2026, 3, 13))

    assert selected is not None
    assert selected["item_id"] == "future-nearest"
    assert selected["absolute_field_scene_lag_days"] == 3


def test_training_pipeline_is_scaler_plus_balanced_logistic_regression() -> None:
    pipeline = build_training_pipeline()
    assert list(pipeline.named_steps) == ["scaler", "classifier"]
    assert pipeline.named_steps["classifier"].class_weight == "balanced"
    assert pipeline.named_steps["classifier"].max_iter == 1000


def test_validation_is_group_aware_and_produces_oof_predictions() -> None:
    rows = _rows()
    x = np.asarray([[row[name] for name in MODEL_FEATURES] for row in rows])
    y = np.asarray([row["target"] for row in rows])
    groups = np.asarray([row["group_id"] for row in rows])

    probabilities, folds, limitations = grouped_validation_predictions(x, y, groups)

    assert probabilities is not None
    assert np.all((0 <= probabilities) & (probabilities <= 1))
    assert folds >= 2
    assert limitations == []


def test_artifact_is_reproducible_and_contains_fitted_scaler(tmp_path) -> None:
    timestamp = "2026-08-18T12:00:00+00:00"
    first = train_artifact(_rows(), training_timestamp=timestamp)
    second = train_artifact(list(reversed(_rows())), training_timestamp=timestamp)

    assert first == second
    assert first["features"] == list(MODEL_FEATURES)
    assert first["validation_strategy"] == "StratifiedGroupKFold_by_KM_local"
    assert len(first["scaler_mean"]) == 3
    assert all(value > 0 for value in first["scaler_scale"])
    assert len(first["coefficients"]) == 3
    output = write_model_artifact(tmp_path / "model.json", first)
    assert json.loads(output.read_text(encoding="utf-8")) == first


def test_training_rejects_nonfinite_features() -> None:
    rows = _rows()
    rows[0]["ndvi"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        train_artifact(rows)
