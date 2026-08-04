"""Testes dos artefatos e da serializacao dos resultados."""

import csv
import json
from datetime import datetime

import numpy as np
import pytest

from src.satellite_monitoring.outputs import create_run_directory, write_outputs


def _scene_record(item_id: str, observed_at: str, accepted: bool) -> dict:
    return {
        "item_id": item_id,
        "datetime": observed_at,
        "cloud_cover": np.float32(5.5),
        "platform": "sentinel-2b",
        "tile": "23KLP",
        "available_assets": "B04;B08;SCL",
        "valid_pixel_percentage": 65.0 if not accepted else 90.0,
        "valid_pixel_count": 65 if not accepted else 90,
        "total_pixel_count": 100,
        "quality_status": "low" if not accepted else "high",
        "quality_reasons": ["insufficient_valid_pixels"] if not accepted else [],
        "accepted_for_timeseries": accepted,
        "processing_status": "processed",
        "error": None,
    }


def _timeseries_record(item_id: str, observed_at: str, quality_status: str) -> dict:
    return {
        "item_id": item_id,
        "datetime": observed_at,
        "cloud_cover": 5.5,
        "platform": "sentinel-2b",
        "tile": "23KLP",
        "red_asset": "B04",
        "nir_asset": "B08",
        "scl_asset": "SCL",
        "ndvi_mean": 0.6,
        "ndvi_median": 0.61,
        "ndvi_std": 0.05,
        "ndvi_min": 0.2,
        "ndvi_max": 0.8,
        "valid_pixel_count": 90,
        "total_pixel_count": 100,
        "valid_pixel_percentage": 90.0,
        "quality_status": quality_status,
        "quality_reasons": ["insufficient_valid_pixels"] if quality_status == "low" else [],
        "accepted_for_timeseries": True,
    }


def test_writes_outputs_in_chronological_order_and_serializes_quality(tmp_path) -> None:
    run_directory = create_run_directory(tmp_path, datetime(2026, 8, 4, 11, 0, 0))
    scenes = [
        _scene_record("newer", "2026-08-03T10:00:00+00:00", False),
        _scene_record("older", "2026-08-01T10:00:00+00:00", True),
    ]
    timeseries = [
        _timeseries_record("newer", "2026-08-03T10:00:00+00:00", "low"),
        _timeseries_record("older", "2026-08-01T10:00:00+00:00", "high"),
    ]
    summary = {
        "accepted_scene_count": np.int64(1),
        "rejected_scene_count": np.int64(1),
        "rejected_scenes": [
            {
                "item_id": "newer",
                "quality_reasons": ["insufficient_valid_pixels"],
            }
        ],
        "rejection_reasons": {"insufficient_valid_pixels": np.int64(1)},
        "invalid": np.nan,
    }

    paths = write_outputs(run_directory, scenes, timeseries, summary)

    assert set(path.name for path in paths.values()) == {
        "scenes.csv",
        "ndvi_timeseries.csv",
        "summary.json",
        "ndvi_timeseries.png",
    }
    assert all(path.exists() and path.stat().st_size > 0 for path in paths.values())

    with paths["timeseries"].open(encoding="utf-8", newline="") as file:
        saved_timeseries = list(csv.DictReader(file))
    assert [row["item_id"] for row in saved_timeseries] == ["older", "newer"]
    assert saved_timeseries[1]["quality_reasons"] == "insufficient_valid_pixels"

    with paths["scenes"].open(encoding="utf-8", newline="") as file:
        saved_scenes = list(csv.DictReader(file))
    assert [row["item_id"] for row in saved_scenes] == ["older", "newer"]
    assert saved_scenes[1]["accepted_for_timeseries"] == "False"

    saved_summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert saved_summary["accepted_scene_count"] == 1
    assert saved_summary["rejected_scene_count"] == 1
    assert saved_summary["rejected_scenes"][0]["quality_reasons"] == [
        "insufficient_valid_pixels"
    ]
    assert saved_summary["invalid"] is None
    assert saved_summary["rejection_reasons"]["insufficient_valid_pixels"] == 1


def test_empty_timeseries_does_not_invent_observations(tmp_path) -> None:
    run_directory = create_run_directory(tmp_path, datetime(2026, 8, 4, 12, 0, 0))
    paths = write_outputs(run_directory, [], [], {"accepted_scene_count": 0})

    with paths["timeseries"].open(encoding="utf-8", newline="") as file:
        assert list(csv.DictReader(file)) == []
    assert paths["plot"].stat().st_size > 0
