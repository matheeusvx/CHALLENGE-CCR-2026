"""Executa Spatial Segmentation V1 shadow sobre um GeoJSON local."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.operational_profile import DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.outputs import to_json_compatible
from src.satellite_monitoring.service import run_monitoring_analysis


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa segmentacao espacial experimental sem substituir a recommendation global."
    )
    parser.add_argument("--geometry-file", required=True, type=Path)
    parser.add_argument("--start-date", default="2026-07-24")
    parser.add_argument("--end-date", default="2026-08-24")
    parser.add_argument("--case", dest="case_name")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation",
    )
    return parser


def _zone_feature(zone: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": zone["geometry"],
        "properties": {
            key: value
            for key, value in zone.items()
            if key != "geometry"
        },
    }


def main() -> int:
    args = _parser().parse_args()
    geometry_file = args.geometry_file.resolve()
    if not geometry_file.is_file():
        raise FileNotFoundError(f"GeoJSON nao encontrado: {geometry_file}")
    case_name = args.case_name or geometry_file.stem
    profile = DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
    config = MonitoringConfig(
        geometry_file=geometry_file,
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        max_cloud_cover=profile.max_cloud_cover,
        max_scenes=profile.max_scenes,
        max_candidate_scenes=profile.max_candidate_scenes,
        scene_order=profile.scene_order,
        min_valid_pixel_percentage=profile.min_valid_pixel_percentage,
        min_valid_pixel_count=profile.min_valid_pixel_count,
        min_aoi_coverage_percentage=profile.min_aoi_coverage_percentage,
        min_observations=profile.min_observations,
        daily_aggregation=profile.daily_aggregation,
        decision_min_observations=profile.decision_min_observations,
        high_vegetation_percentile=profile.high_vegetation_percentile,
        significant_drop_absolute=profile.significant_drop_absolute,
        significant_drop_relative_percentage=profile.significant_drop_relative_percentage,
        trend_window=profile.trend_window,
        max_gap_days=profile.max_gap_days,
        recent_intervention_days=profile.recent_intervention_days,
        output_root=args.output_root / "pipeline_runs",
        height_estimation_enabled=False,
        spatial_segmentation_enabled=True,
    )
    result = run_monitoring_analysis(config)
    segmentation = result.spatial_segmentation or {
        "status": "unavailable",
        "warnings": ["SPATIAL_SEGMENTATION_RESULT_MISSING"],
    }
    payload = {
        "case": case_name,
        "source_geometry": str(geometry_file),
        "global_recommendation": result.recommendation,
        "global_recommendation_is_official": True,
        "spatial_segmentation": segmentation,
    }
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            _zone_feature(zone) for zone in segmentation.get("zones", [])
        ],
        "properties": {
            "case": case_name,
            "status": segmentation.get("status"),
            "mode": segmentation.get("mode"),
            "official_recommendation_changed": False,
        },
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    json_path = args.output_root / f"{case_name}.json"
    geojson_path = args.output_root / f"{case_name}.geojson"
    json_path.write_text(
        json.dumps(to_json_compatible(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    geojson_path.write_text(
        json.dumps(
            to_json_compatible(feature_collection),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": segmentation.get("status"),
                "global_recommendation": result.recommendation.get("recommendation"),
                "cells": segmentation.get("performance", {}).get("number_of_cells"),
                "zones": len(segmentation.get("zones", [])),
                "json": str(json_path),
                "geojson": str(geojson_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

