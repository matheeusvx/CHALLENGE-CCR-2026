"""Executa a prova segment-first reutilizando um unico carregamento por cena/AOI."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.operational_profile import DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.outputs import to_json_compatible
from src.satellite_monitoring.segment_first_analysis import (
    run_segment_first_temporal_analysis,
)
from src.satellite_monitoring.service import PipelineDependencies, run_monitoring_analysis
from src.satellite_monitoring.spatial_segmentation import run_spatial_segmentation


CASES = {
    "frango_assado": ROOT / "data" / "aoi" / "frango_assado_area_audit.geojson",
    "louveira": ROOT / "data" / "aoi" / "louveira_lateral.geojson",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa 25/50 m por serie temporal de section, sem rede por section."
    )
    parser.add_argument("--start-date", default="2026-07-24")
    parser.add_argument("--end-date", default="2026-08-24")
    parser.add_argument(
        "--roads-geojson",
        type=Path,
        default=ROOT / "data" / "roads" / "processed" / "motiva-sp-roads-state.geojson",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation",
    )
    return parser


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            to_json_compatible(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _feature_collection(experiment: dict[str, Any], case_name: str) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": zone["geometry"],
                "properties": {key: value for key, value in zone.items() if key != "geometry"},
            }
            for zone in experiment["zones"]
        ],
        "properties": {
            "case": case_name,
            "architecture": "segment_first_temporal_analysis",
            "section_length_m": experiment["section_length_m"],
            "shadow_mode": True,
        },
    }


def main() -> int:
    args = _parser().parse_args()
    road_document = json.loads(args.roads_geojson.read_text(encoding="utf-8"))
    profile = DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
    args.output_root.mkdir(parents=True, exist_ok=True)
    report_cases: dict[str, Any] = {}
    for case_name, geometry_file in CASES.items():
        def composite_segmenter(**kwargs):
            raw = run_spatial_segmentation(**kwargs)
            segment_first = run_segment_first_temporal_analysis(
                observations=kwargs["observations"],
                daily_records=kwargs["daily_records"],
                config=kwargs["config"],
                selected_area_m2=kwargs["selected_area_m2"],
                road_document=road_document,
                raw_segmentation=raw,
            )
            return {
                "status": "experimental",
                "mode": "shadow",
                "official_recommendation_changed": False,
                "raw_segmentation": raw,
                "segment_first_temporal": segment_first,
            }

        dependencies = replace(
            PipelineDependencies(), segment_spatial=composite_segmenter
        )
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
            significant_drop_relative_percentage=(
                profile.significant_drop_relative_percentage
            ),
            trend_window=profile.trend_window,
            max_gap_days=profile.max_gap_days,
            recent_intervention_days=profile.recent_intervention_days,
            output_root=args.output_root / "segment_first_pipeline_runs" / case_name,
            height_estimation_enabled=False,
            spatial_segmentation_enabled=True,
        )
        result = run_monitoring_analysis(config, dependencies=dependencies)
        spatial = result.spatial_segmentation or {}
        segment_first = spatial.get("segment_first_temporal", {})
        report_cases[case_name] = {
            "global_recommendation": result.recommendation,
            "analysis_status": result.status,
            "road_association": segment_first.get("road_association"),
            "raw_segmentation_unchanged": segment_first.get(
                "raw_segmentation_unchanged"
            ),
            "raw_classes_used_as_decision_input": False,
            "experiments": [
                {
                    key: value
                    for key, value in experiment.items()
                    if key not in {"sections", "zones"}
                }
                for experiment in segment_first.get("experiments", [])
            ],
        }
        for experiment in segment_first.get("experiments", []):
            length = experiment["section_length_m"]
            _write(
                args.output_root / f"{case_name}_segment_first_{length}m.geojson",
                _feature_collection(experiment, case_name),
            )
            _write(
                args.output_root / f"{case_name}_segment_first_{length}m_sections.json",
                experiment,
            )
    report = {
        "status": "experimental",
        "mode": "shadow",
        "architecture": "segment_first_temporal_analysis",
        "section_lengths_m": [25, 50],
        "dominance_threshold": None,
        "external_queries_per_section": 0,
        "cases": report_cases,
    }
    report_path = args.output_root / "segment_first_temporal_report.json"
    _write(report_path, report)
    print(json.dumps({"report": str(report_path), "cases": report_cases}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
