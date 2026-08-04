"""Testes dos artefatos e da serializacao dos resultados."""

import json
from datetime import datetime

import numpy as np
import pytest

from src.satellite_monitoring.outputs import create_run_directory, write_outputs


def test_writes_all_outputs_and_serializes_numpy_types(tmp_path) -> None:
    run_directory = create_run_directory(tmp_path, datetime(2026, 8, 4, 11, 0, 0))
    scenes = [
        {
            "item_id": "test-item",
            "datetime": "2026-08-01T10:00:00+00:00",
            "cloud_cover": np.float32(5.5),
            "platform": "sentinel-2b",
            "tile": "23KLP",
            "available_assets": "B04;B08;SCL",
            "status": "processed",
            "error": None,
        }
    ]
    timeseries = [
        {
            "item_id": "test-item",
            "datetime": "2026-08-01T10:00:00+00:00",
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
            "valid_pixel_count": 30,
            "valid_pixel_percentage": 75.0,
        }
    ]
    summary = {
        "scene_count_processed": np.int64(1),
        "metric": np.float32(0.6),
        "array": np.array([1, 2]),
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

    saved_summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert saved_summary["scene_count_processed"] == 1
    assert saved_summary["metric"] == pytest.approx(0.6)
    assert saved_summary["array"] == [1, 2]
    assert saved_summary["invalid"] is None
