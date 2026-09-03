"""Consulta Sentinel offline para construcao de datasets de treinamento."""

from __future__ import annotations

from datetime import date
from typing import Any

from shapely.geometry import mapping

from ..config import MonitoringConfig
from ..indices import InsufficientValidPixelsError, analyze_ndvi
from ..quality import assess_scene_quality
from ..raster_processing import read_scene_bands
from ..stac_client import NoScenesError, Scene, search_scenes

MAX_TRAINING_CANDIDATE_SCENES = 100


def process_training_scene(
    scene: Scene, aoi_geojson: dict[str, Any], config: MonitoringConfig
) -> dict[str, Any]:
    """Aplica o mesmo NDVI/quality assessment do pipeline sem recommendation."""
    record = scene.to_record()
    record.update(
        {
            "ndvi_mean": None,
            "ndvi_median": None,
            "ndvi_std": None,
            "ndvi_min": None,
            "ndvi_max": None,
            "valid_pixel_percentage": 0.0,
            "valid_pixel_count": 0,
            "aoi_coverage_percentage": None,
            "scene_quality_score": None,
            "quality_status": "unknown",
            "accepted_for_timeseries": False,
            "processing_status": "processing",
        }
    )
    try:
        raster_data = read_scene_bands(scene.item, aoi_geojson)
        statistics = None
        try:
            _, statistics = analyze_ndvi(
                raster_data.red,
                raster_data.nir,
                raster_data.valid_mask,
                raster_data.total_pixel_count,
            )
        except InsufficientValidPixelsError:
            pass
        valid_count = statistics.valid_pixel_count if statistics else 0
        valid_percentage = statistics.valid_pixel_percentage if statistics else 0.0
        assessment = assess_scene_quality(
            valid_pixel_percentage=valid_percentage,
            valid_pixel_count=valid_count,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
            min_valid_pixel_count=config.min_valid_pixel_count,
            medium_threshold=config.medium_quality_threshold,
            high_threshold=config.high_quality_threshold,
            has_scl=raster_data.scl_asset is not None,
            scl_class_percentages=raster_data.scl_class_percentages,
            cloud_cover=record.get("cloud_cover"),
            max_cloud_cover=config.max_cloud_cover,
            aoi_coverage_percentage=raster_data.aoi_coverage_percentage,
            min_aoi_coverage_percentage=config.min_aoi_coverage_percentage,
            partial_raster_coverage=raster_data.partial_raster_coverage,
            has_valid_ndvi_pixels=statistics is not None,
            include_low_quality_scenes=config.include_low_quality_scenes,
        )
        record.update(
            {
                "ndvi_mean": statistics.mean if statistics else None,
                "ndvi_median": statistics.median if statistics else None,
                "ndvi_std": statistics.std if statistics else None,
                "ndvi_min": statistics.minimum if statistics else None,
                "ndvi_max": statistics.maximum if statistics else None,
                "valid_pixel_percentage": valid_percentage,
                "valid_pixel_count": valid_count,
                "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                "scene_quality_score": assessment.scene_quality_score,
                "quality_status": assessment.quality_status,
                "quality_reasons": list(assessment.quality_reasons),
                "accepted_for_timeseries": assessment.accepted_for_timeseries,
                "processing_status": "processed",
            }
        )
    except Exception as exc:
        record.update(
            {
                "processing_status": "failed",
                "quality_reasons": ["processing_error"],
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    return record


def query_training_scenes(
    geometry: Any, start_date: date, end_date: date
) -> tuple[list[dict[str, Any]], str | None]:
    """Retorna cenas avaliadas e o item STAC necessário à extração de features."""
    aoi_geojson = mapping(geometry)
    config = MonitoringConfig(
        geometry=aoi_geojson,
        start_date=start_date,
        end_date=end_date,
        max_scenes=MAX_TRAINING_CANDIDATE_SCENES,
        max_candidate_scenes=MAX_TRAINING_CANDIDATE_SCENES,
        scene_order="oldest",
    )
    try:
        result = search_scenes(config, aoi_geojson)
    except NoScenesError:
        return [], None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    records: list[dict[str, Any]] = []
    for scene in result.scenes:
        record = process_training_scene(scene, aoi_geojson, config)
        record["_scene_item"] = scene.item
        records.append(record)
    return records, None
