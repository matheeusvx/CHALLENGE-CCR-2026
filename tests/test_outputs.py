"""Testes dos artefatos e da serializacao dos resultados."""

import csv
import json
from datetime import datetime

import numpy as np
import pytest

from src.satellite_monitoring.outputs import create_run_directory, write_outputs

AOI_GEOJSON = {
    "type": "Feature",
    "properties": {"geometry_source": "circle"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-47.0, -23.0], [-46.999, -23.0], [-46.999, -22.999], [-47.0, -23.0]]],
    },
}
RECOMMENDATION = {
    "recommendation": "cortar",
    "confidence": "medium",
    "experimental": True,
    "area_type": "roadside_grass",
    "summary": "Recomendacao experimental de corte.",
    "reasons": ["current_percentile_at_or_above_high_threshold"],
    "blocking_reasons": [],
    "metrics": {
        "observation_count": 4,
        "current_ndvi_mean": 0.6,
        "historical_median": 0.5,
        "current_percentile": 87.5,
        "recent_trend": 0.01,
        "significant_drop_detected": False,
        "days_since_significant_drop": None,
        "significant_drop_dates": [],
    },
    "thresholds": {},
    "quality": {},
    "date_range": {"start": "2026-08-01", "end": "2026-08-03"},
    "limitations": [],
}


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
        "aoi_coverage_percentage": 100.0,
        "partial_raster_coverage": False,
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
        "aoi_coverage_percentage": 100.0,
        "partial_raster_coverage": False,
        "quality_status": quality_status,
        "quality_reasons": ["insufficient_valid_pixels"] if quality_status == "low" else [],
        "accepted_for_timeseries": True,
        "daily_aggregation": "best",
        "aggregation_scene_count": 1,
        "aggregation_source_item_ids": [item_id],
        "aggregation_selected_item_id": item_id,
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
        "aoi": {
            "source": "circle",
            "geometry_type": "Polygon",
            "area_square_meters": 100.0,
            "feature_count": 1,
        },
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
        "cut_recommendation": {
            "recommendation": "cortar",
            "confidence": "medium",
        },
    }

    paths = write_outputs(
        run_directory,
        scenes,
        timeseries,
        summary,
        AOI_GEOJSON,
        RECOMMENDATION,
        raw_daily_records=[
            {**timeseries[0], "included_in_analysis": False, "exclusion_reasons": ["isolated_temporal_drop"]},
            timeseries[1],
        ],
        quality_report={
            "analysis_quality": {"score": np.float32(82.5), "status": "medium"},
            "outliers": [{"item_id": "newer", "reasons": ["isolated_temporal_drop"]}],
        },
    )

    assert set(path.name for path in paths.values()) == {
        "scenes.csv",
        "ndvi_timeseries.csv",
        "raw_daily_timeseries.csv",
        "summary.json",
        "quality_report.json",
        "ndvi_timeseries.png",
        "aoi.geojson",
        "cut_recommendation.json",
        "cut_recommendation.csv",
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
    assert saved_summary["aoi"]["source"] == "circle"
    assert saved_summary["cut_recommendation"]["recommendation"] == "cortar"

    saved_aoi = json.loads(paths["aoi"].read_text(encoding="utf-8"))
    assert saved_aoi == AOI_GEOJSON

    saved_recommendation = json.loads(
        paths["recommendation_json"].read_text(encoding="utf-8")
    )
    assert saved_recommendation["recommendation"] == "cortar"
    saved_quality_report = json.loads(paths["quality_report"].read_text(encoding="utf-8"))
    assert saved_quality_report["analysis_quality"]["score"] == 82.5
    assert saved_quality_report["outliers"][0]["reasons"] == ["isolated_temporal_drop"]
    with paths["raw_timeseries"].open(encoding="utf-8", newline="") as file:
        raw_rows = list(csv.DictReader(file))
    assert raw_rows[1]["exclusion_reasons"] == "isolated_temporal_drop"
    with paths["recommendation_csv"].open(encoding="utf-8", newline="") as file:
        recommendation_rows = list(csv.DictReader(file))
    assert len(recommendation_rows) == 1
    assert recommendation_rows[0]["confidence"] == "medium"
    assert recommendation_rows[0]["observation_count"] == "4"


def test_empty_timeseries_does_not_invent_observations(tmp_path) -> None:
    run_directory = create_run_directory(tmp_path, datetime(2026, 8, 4, 12, 0, 0))
    paths = write_outputs(
        run_directory,
        [],
        [],
        {"accepted_scene_count": 0},
        AOI_GEOJSON,
        {
            **RECOMMENDATION,
            "recommendation": "inconclusivo",
            "confidence": "low",
            "metrics": {**RECOMMENDATION["metrics"], "observation_count": 0},
        },
    )

    with paths["timeseries"].open(encoding="utf-8", newline="") as file:
        assert list(csv.DictReader(file)) == []
    assert paths["plot"].stat().st_size > 0
