"""Executa somente a auditoria espacial da AOI oficial do Frango Assado."""

from __future__ import annotations

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
from src.satellite_monitoring.cut_recommendation import aggregate_daily_observations
from src.satellite_monitoring.experiments.spatial_area_audit import (
    audit_operational_scene,
    compare_frontend_area,
    geometry_area_diagnostic,
    write_spatial_audit,
)
from src.satellite_monitoring.geometry import extract_polygon_geometry
from src.satellite_monitoring.indices import InsufficientValidPixelsError, analyze_ndvi
from src.satellite_monitoring.quality import (
    assess_scene_quality,
    select_quality_assessed_observations,
)
from src.satellite_monitoring.raster_processing import read_scene_bands
from src.satellite_monitoring.stac_client import search_scenes


AOI_PATH = ROOT / "data" / "aoi" / "frango_assado_area_audit.geojson"
OUTPUT_PATH = ROOT / "outputs" / "spatial_audit" / "frango_assado_area_audit.json"
FRONTEND_AREA_M2 = 4419.0
FRONTEND_ESTIMATED_PIXELS = 45


def _quality_record(scene: Any, raster_data: Any, config: MonitoringConfig) -> dict[str, Any]:
    try:
        _, statistics = analyze_ndvi(
            raster_data.red,
            raster_data.nir,
            raster_data.valid_mask,
            raster_data.total_pixel_count,
        )
    except InsufficientValidPixelsError:
        statistics = None
    valid_count = statistics.valid_pixel_count if statistics else 0
    valid_percentage = statistics.valid_pixel_percentage if statistics else 0.0
    cloud_cover = scene.item.properties.get("eo:cloud_cover")
    assessment = assess_scene_quality(
        valid_pixel_percentage=valid_percentage,
        valid_pixel_count=valid_count,
        min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        min_valid_pixel_count=config.min_valid_pixel_count,
        medium_threshold=config.medium_quality_threshold,
        high_threshold=config.high_quality_threshold,
        has_scl=raster_data.scl_asset is not None,
        scl_class_percentages=raster_data.scl_class_percentages,
        cloud_cover=cloud_cover,
        max_cloud_cover=config.max_cloud_cover,
        aoi_coverage_percentage=raster_data.aoi_coverage_percentage,
        min_aoi_coverage_percentage=config.min_aoi_coverage_percentage,
        partial_raster_coverage=raster_data.partial_raster_coverage,
        has_valid_ndvi_pixels=statistics is not None,
        include_low_quality_scenes=config.include_low_quality_scenes,
    )
    return {
        "item_id": scene.item_id,
        "datetime": scene.datetime.isoformat(),
        "cloud_cover": cloud_cover,
        "platform": scene.item.properties.get("platform")
        or scene.item.properties.get("constellation"),
        "tile": scene.item.properties.get("s2:mgrs_tile"),
        "ndvi_mean": statistics.mean if statistics else None,
        "ndvi_median": statistics.median if statistics else None,
        "ndvi_std": statistics.std if statistics else None,
        "ndvi_min": statistics.minimum if statistics else None,
        "ndvi_max": statistics.maximum if statistics else None,
        "valid_pixel_count": valid_count,
        "total_pixel_count": raster_data.total_pixel_count,
        "valid_pixel_percentage": valid_percentage,
        "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
        "partial_raster_coverage": raster_data.partial_raster_coverage,
        "scene_quality_score": assessment.scene_quality_score,
        "quality_status": assessment.quality_status,
        "quality_reasons": list(assessment.quality_reasons),
        "accepted_for_timeseries": assessment.accepted_for_timeseries,
    }


def _base_payload(document: dict[str, Any]) -> dict[str, Any]:
    geometry = geometry_area_diagnostic(document)
    frontend = compare_frontend_area(
        geometry["geodesic_area_m2"], displayed_area_m2=FRONTEND_AREA_M2
    )
    frontend.update(
        {
            "estimated_pixels": FRONTEND_ESTIMATED_PIXELS,
            "estimated_pixel_formula": "max(1, ceil(area_m2 / 100))",
            "assumed_nominal_resolution_m": [10, 10],
            "implementation": {
                "preview_file": "apps/web/lib/map/geometry.ts",
                "preview_function": "calculateGeometryPreview",
                "api_file": "apps/api/app/routes/analyses.py",
                "api_function": "validate_geometry",
            },
            "analyzed_area_display_semantics": {
                "meaning": "SELECTED_GEODESIC_GEOMETRY_AREA",
                "not_meaning": [
                    "RASTER_COVERED_AREA",
                    "QUALITY_VALID_AREA",
                    "EFFECTIVE_ANALYSIS_AREA",
                ],
                "source_file": "apps/web/lib/utils/recommendation.ts",
                "source_function": "analyzedAreaSquareMeters",
            },
        }
    )
    return {
        "audit_case": "frango_assado_area_audit",
        "source_geometry": "data/aoi/frango_assado_area_audit.geojson",
        "analysis_period": {"start": "2026-07-24", "end": "2026-08-24"},
        "geometry": geometry,
        "frontend": frontend,
    }


def main() -> int:
    document = json.loads(AOI_PATH.read_text(encoding="utf-8"))
    payload = _base_payload(document)
    polygon, _ = extract_polygon_geometry(document)
    aoi_geometry = dict(polygon.__geo_interface__)
    profile = DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
    config = MonitoringConfig(
        geometry=aoi_geometry,
        start_date=date(2026, 7, 24),
        end_date=date(2026, 8, 24),
        max_cloud_cover=profile.max_cloud_cover,
        max_scenes=profile.max_scenes,
        max_candidate_scenes=profile.max_candidate_scenes,
        scene_order=profile.scene_order,
        min_valid_pixel_percentage=profile.min_valid_pixel_percentage,
        min_valid_pixel_count=profile.min_valid_pixel_count,
        min_aoi_coverage_percentage=profile.min_aoi_coverage_percentage,
        min_observations=profile.min_observations,
        daily_aggregation=profile.daily_aggregation,
        height_estimation_enabled=False,
    )
    search_result = search_scenes(config, aoi_geometry)
    records: list[dict[str, Any]] = []
    raster_by_id: dict[str, Any] = {}
    item_by_id: dict[str, Any] = {}
    processing_errors: list[dict[str, str]] = []
    for index, scene in enumerate(search_result.scenes, start=1):
        print(f"[{index}/{len(search_result.scenes)}] {scene.item_id}", flush=True)
        try:
            raster_data = read_scene_bands(scene.item, aoi_geometry)
            records.append(_quality_record(scene, raster_data, config))
            raster_by_id[scene.item_id] = raster_data
            item_by_id[scene.item_id] = scene.item
        except Exception as exc:
            processing_errors.append({"item_id": scene.item_id, "error": str(exc)})
    selected, discarded = select_quality_assessed_observations(
        records, max_scenes=config.max_scenes, scene_order=config.scene_order
    )
    daily = aggregate_daily_observations(selected, strategy=config.daily_aggregation)
    if not daily.observations:
        payload.update(
            {
                "scene_selection": {
                    "total_matches": search_result.total_matches,
                    "processed_records": records,
                    "processing_errors": processing_errors,
                    "status": "NO_OPERATIONAL_OBSERVATION_SELECTED",
                },
                "raster": "NOT_CURRENTLY_TRACKED",
                "operational_analysis": "NOT_CURRENTLY_TRACKED",
                "height_estimation": "NOT_CURRENTLY_TRACKED",
                "pixel_intersection_strategy": "NOT_CURRENTLY_TRACKED",
                "warnings": ["No quality-accepted operational observation was available."],
                "conclusion": "INSUFFICIENT_DATA",
            }
        )
        write_spatial_audit(payload, OUTPUT_PATH)
        return 2

    latest = daily.observations[-1]
    selected_item_id = str(latest["aggregation_selected_item_id"])
    scene_audit = audit_operational_scene(
        item_by_id[selected_item_id], aoi_geometry, raster_by_id[selected_item_id]
    )
    selected_record = next(row for row in records if row["item_id"] == selected_item_id)
    nominal_estimate_matches_center_count = (
        FRONTEND_ESTIMATED_PIXELS == scene_audit["pipeline_center_inside_pixel_count"]
    )
    intersection_closure_pct = abs(
        scene_audit["actual_intersection_area_m2"]
        - scene_audit["selected_area_projected_m2"]
    ) / scene_audit["selected_area_projected_m2"] * 100.0
    warnings = [
        "Frontend estimated_pixels is a nominal area/100 estimate, not a raster intersection count.",
        "Operational metrics use unweighted center-in pixels; exact intersection areas are post-hoc diagnostics.",
        "The API/UI label 'Area analisada' currently exposes selected geodesic area, not effective raster area.",
        "Operational effective-area fields are NOT_CURRENTLY_TRACKED by the production pipeline and were derived only for this audit.",
        "Height-estimation valid area is separate and does not participate in the operational recommendation.",
    ]
    if scene_audit["height_diagnostic_error"]:
        warnings.append(
            "Height valid-area diagnostic unavailable: "
            + str(scene_audit["height_diagnostic_error"])
        )

    effective_pct = scene_audit["effective_analysis_pct_of_selected"]
    if intersection_closure_pct > 1.0:
        conclusion = "PIXEL_ACCOUNTING_INCORRECT"
    elif effective_pct < 99.0:
        conclusion = "RASTER_EFFECTIVE_AREA_SMALLER_THAN_DISPLAYED"
    else:
        conclusion = "AREA_ACCOUNTING_CONSISTENT"
    payload.update(
        {
            "scene_selection": {
                "method": "existing quality selection and daily best aggregation; no recommendation executed",
                "total_matches": search_result.total_matches,
                "candidate_scene_count": len(search_result.scenes),
                "processed_scene_count": len(records),
                "quality_selected_scene_count": len(selected),
                "discarded_after_quality_count": len(discarded),
                "selected_item_id": selected_item_id,
                "selected_datetime": selected_record["datetime"],
                "selected_quality": selected_record,
                "processing_errors": processing_errors,
            },
            "raster": {
                "working_crs": scene_audit["working_crs"],
                "effective_resolution_m": scene_audit["effective_resolution_m"],
                "band_grids": {
                    "RED": "native 10 m reference grid",
                    "NIR": "aligned to RED with bilinear resampling",
                    "NDVI": "computed on RED reference grid",
                    "SCL": "native 20 m, aligned to RED with nearest-neighbor resampling",
                    "quality_masks": "RED reference grid at 10 m",
                },
                "intersecting_pixel_count": scene_audit["intersecting_pixel_count"],
                "pipeline_center_inside_pixel_count": scene_audit[
                    "pipeline_center_inside_pixel_count"
                ],
                "accepted_pixel_count": scene_audit["accepted_pixel_count"],
                "rejected_pixel_count": scene_audit["rejected_pixel_count"],
                "pipeline_rejected_pixel_count": scene_audit[
                    "pipeline_rejected_pixel_count"
                ],
                "nominal_pixel_area_m2": scene_audit["nominal_pixel_area_m2"],
                "nominal_intersecting_area_m2": scene_audit[
                    "nominal_intersecting_area_m2"
                ],
                "actual_intersection_area_m2": scene_audit[
                    "actual_intersection_area_m2"
                ],
                "intersection_closure_difference_pct": intersection_closure_pct,
                "frontend_estimate_matches_center_inside_count": nominal_estimate_matches_center_count,
                "rejection_reason_counts": scene_audit["rejection_reason_counts"],
                "pixel_intersections": scene_audit["pixel_intersections"],
            },
            "operational_analysis": {
                "selected_area_m2": payload["geometry"]["geodesic_area_m2"],
                "covered_area_m2": scene_audit["covered_area_m2"],
                "quality_valid_area_m2": scene_audit["quality_valid_area_m2"],
                "effective_analysis_area_m2": scene_audit[
                    "analysis_effective_area_m2"
                ],
                "coverage_pct": scene_audit["coverage_pct"],
                "quality_valid_pct_of_selected": scene_audit[
                    "quality_valid_pct_of_selected"
                ],
                "effective_analysis_pct": effective_pct,
                "production_tracking": "NOT_CURRENTLY_TRACKED",
                "derivation": "post-hoc exact AOI-pixel intersections for center-in accepted pixels",
                "funnel": [
                    {
                        "stage": "selected",
                        "area_m2": payload["geometry"]["geodesic_area_m2"],
                        "pct_of_selected": 100.0,
                    },
                    {
                        "stage": "raster_covered",
                        "area_m2": scene_audit["covered_area_m2"],
                        "pct_of_selected": scene_audit["coverage_pct"],
                    },
                    {
                        "stage": "quality_valid",
                        "area_m2": scene_audit["quality_valid_area_m2"],
                        "pct_of_selected": scene_audit[
                            "quality_valid_pct_of_selected"
                        ],
                    },
                    {
                        "stage": "effective_analysis",
                        "area_m2": scene_audit["analysis_effective_area_m2"],
                        "pct_of_selected": effective_pct,
                    },
                ],
            },
            "height_estimation": {
                "valid_area_m2": scene_audit["height_valid_area_m2"],
                "valid_area_pct": scene_audit["height_valid_pct_of_selected"],
                "status": scene_audit["height_diagnostic_status"],
                "participates_in_operational_recommendation": False,
            },
            "pixel_intersection_strategy": {
                **scene_audit["pixel_intersection_strategy"],
                "implementation_file": "src/satellite_monitoring/raster_processing.py",
                "implementation_function": "read_scene_bands",
            },
            "warnings": warnings,
            "recommended_next_correction": (
                "Expose selected, covered, quality-valid, and effective areas as distinct API/UI fields; "
                "keep estimated_pixels explicitly labeled as a nominal estimate. Do not change recommendation semantics without separate validation."
                if conclusion != "AREA_ACCOUNTING_CONSISTENT"
                else None
            ),
            "conclusion": conclusion,
        }
    )
    write_spatial_audit(payload, OUTPUT_PATH)
    print(OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
