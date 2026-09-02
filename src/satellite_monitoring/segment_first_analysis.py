"""Analise temporal por trechos rodoviarios antes da classificacao espacial."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from .config import MonitoringConfig
from .cut_recommendation import aggregate_daily_observations
from .quality import assess_scene_quality
from .raster_processing import SCL_CLASS_NAMES, calculate_mask_intersection_area
from .road_aligned_segmentation import (
    RoadAssociation,
    associate_road,
    build_longitudinal_section_geometries,
)
from .spatial_segmentation import (
    SpatialRasterObservation,
    _reference_item_id,
    _source_ids,
    align_observations_to_reference,
    classify_cell_timeseries,
    run_spatial_segmentation,
)


CLASSES = ("cortar", "nao_cortar", "inconclusivo")
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}
DEFAULT_ROAD_DOCUMENT = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "roads"
    / "processed"
    / "motiva-sp-roads-state.geojson"
)


def _canonical_hash(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _minimum_level(values: Sequence[str]) -> str:
    return min(values, key=lambda value: LEVEL_ORDER.get(value, -1), default="low")


def _level_summary(values: Sequence[str]) -> dict[str, Any]:
    counts = Counter(values)
    return {
        "minimum": _minimum_level(values),
        "counts": {level: int(counts.get(level, 0)) for level in LEVEL_ORDER},
    }


def _local_scl_percentages(
    observation: SpatialRasterObservation,
    section_mask: np.ndarray,
) -> dict[str, float]:
    if observation.scl_values is None:
        return dict(observation.scl_class_percentages or {})
    total = int(np.count_nonzero(section_mask))
    if total == 0:
        return {name: 0.0 for name in SCL_CLASS_NAMES.values()}
    values = np.asarray(observation.scl_values)
    return {
        name: float(np.count_nonzero(section_mask & (values == class_value)) / total * 100.0)
        for class_value, name in SCL_CLASS_NAMES.items()
    }


def build_section_scene_record(
    *,
    section_geometry: Any,
    observation: SpatialRasterObservation,
    config: MonitoringConfig,
) -> dict[str, Any]:
    """Calcula estatisticas locais com a mascara e grade operacionais existentes."""
    shape_rows, shape_columns = observation.ndvi.shape
    selected_mask = geometry_mask(
        [mapping(section_geometry)],
        out_shape=observation.ndvi.shape,
        transform=observation.transform,
        all_touched=False,
        invert=True,
    ) & np.asarray(observation.inside_aoi_mask, dtype=bool)
    valid_mask = (
        selected_mask
        & np.asarray(observation.valid_mask, dtype=bool)
        & np.isfinite(observation.ndvi)
    )
    total_pixel_count = int(np.count_nonzero(selected_mask))
    valid_pixel_count = int(np.count_nonzero(valid_mask))
    valid_percentage = (
        valid_pixel_count / total_pixel_count * 100.0 if total_pixel_count else 0.0
    )
    west, south, east, north = array_bounds(
        shape_rows, shape_columns, observation.transform
    )
    covered_area = float(section_geometry.intersection(box(west, south, east, north)).area)
    selected_area = float(section_geometry.area)
    coverage_percentage = (
        min(100.0, covered_area / selected_area * 100.0) if selected_area else 0.0
    )
    partial_coverage = coverage_percentage < 99.999999
    scl_percentages = _local_scl_percentages(observation, selected_mask)
    assessment = assess_scene_quality(
        valid_pixel_percentage=valid_percentage,
        valid_pixel_count=valid_pixel_count,
        min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        min_valid_pixel_count=config.min_valid_pixel_count,
        medium_threshold=config.medium_quality_threshold,
        high_threshold=config.high_quality_threshold,
        has_scl=observation.has_scl,
        scl_class_percentages=scl_percentages,
        cloud_cover=observation.cloud_cover,
        max_cloud_cover=config.max_cloud_cover,
        aoi_coverage_percentage=coverage_percentage,
        min_aoi_coverage_percentage=config.min_aoi_coverage_percentage,
        partial_raster_coverage=partial_coverage,
        has_valid_ndvi_pixels=valid_pixel_count > 0,
        include_low_quality_scenes=config.include_low_quality_scenes,
    )
    values = np.asarray(observation.ndvi, dtype=float)[valid_mask]
    effective_area = calculate_mask_intersection_area(
        transform=observation.transform,
        geometry_document=mapping(section_geometry),
        crs_is_projected=observation.crs_is_projected,
        accepted_pixel_mask=valid_mask,
    )
    return {
        "item_id": observation.item_id,
        "datetime": observation.datetime,
        "cloud_cover": observation.cloud_cover,
        "ndvi_mean": float(np.mean(values)) if values.size else None,
        "ndvi_median": float(np.median(values)) if values.size else None,
        "ndvi_std": float(np.std(values)) if values.size else None,
        "ndvi_min": float(np.min(values)) if values.size else None,
        "ndvi_max": float(np.max(values)) if values.size else None,
        "valid_pixel_count": valid_pixel_count,
        "total_pixel_count": total_pixel_count,
        "valid_pixel_percentage": valid_percentage,
        "local_valid_pixel_percentage": valid_percentage,
        "local_invalid_pixel_percentage": max(0.0, 100.0 - valid_percentage),
        "aoi_coverage_percentage": coverage_percentage,
        "partial_raster_coverage": partial_coverage,
        "scene_quality_score": assessment.scene_quality_score,
        "quality_status": assessment.quality_status,
        "quality_reasons": list(assessment.quality_reasons),
        "accepted_for_timeseries": assessment.accepted_for_timeseries,
        "effective_analysis_area_m2": effective_area,
        "scl_class_percentages": scl_percentages,
    }


def build_section_timeseries(
    *,
    section_geometry: Any,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconstrói a serie local somente com snapshots de cenas ja carregados."""
    used_item_ids = {
        item_id for record in daily_records for item_id in _source_ids(record)
    }
    scene_records = [
        build_section_scene_record(
            section_geometry=section_geometry,
            observation=observation,
            config=config,
        )
        for observation in observations
        if observation.item_id in used_item_ids
    ]
    daily = aggregate_daily_observations(scene_records, strategy=config.daily_aggregation)
    audit = [
        {
            "item_id": record["item_id"],
            "datetime": record["datetime"],
            "accepted_for_timeseries": record["accepted_for_timeseries"],
            "valid_pixel_count": record["valid_pixel_count"],
            "total_pixel_count": record["total_pixel_count"],
            "valid_pixel_percentage": record["valid_pixel_percentage"],
            "quality_status": record["quality_status"],
            "quality_reasons": record["quality_reasons"],
        }
        for record in sorted(scene_records, key=lambda value: (value["datetime"], value["item_id"]))
    ]
    return daily.observations, audit


def _section_result(
    section: Mapping[str, Any],
    *,
    section_geometry_reference: Any,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
) -> dict[str, Any]:
    series, audit = build_section_timeseries(
        section_geometry=section_geometry_reference,
        observations=observations,
        daily_records=daily_records,
        config=config,
    )
    rejected = sum(not row["accepted_for_timeseries"] for row in audit)
    recommendation, quality, analyzed = classify_cell_timeseries(
        series,
        config=config,
        processed_scene_count=len(audit),
        rejected_scene_count=rejected,
    )
    effective_area = (
        float(analyzed[-1].get("effective_analysis_area_m2") or 0.0)
        if analyzed
        else 0.0
    )
    selected_area = float(section["selected_area_m2"])
    return {
        "section_id": section["section_id"],
        "sequence_index": section["sequence_index"],
        "road_ref": section["road_ref"],
        "road_name": section["road_name"],
        "start_distance_m": section["start_distance_m"],
        "end_distance_m": section["end_distance_m"],
        "length_m": section["length_m"],
        "recommendation": recommendation["recommendation"],
        "confidence": recommendation["confidence"],
        "analysis_quality": quality["status"],
        "reasons": sorted(
            set(recommendation.get("reasons") or [])
            | set(recommendation.get("blocking_reasons") or [])
        ),
        "selected_area_m2": selected_area,
        "effective_area_m2": effective_area,
        "effective_analysis_pct": (
            effective_area / selected_area * 100.0 if selected_area else 0.0
        ),
        "valid_observation_count": len(analyzed),
        "section_daily_timeseries": analyzed,
        "observation_audit": audit,
        "geometry": section["geometry"],
    }


def merge_segment_first_sections(
    sections: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        sections,
        key=lambda item: (
            str(item["road_ref"]),
            float(item["start_distance_m"]),
            int(item["sequence_index"]),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    for section in ordered:
        if not groups:
            groups.append([section])
            continue
        previous = groups[-1][-1]
        consecutive = abs(
            float(previous["end_distance_m"]) - float(section["start_distance_m"])
        ) <= 1e-6
        if (
            consecutive
            and previous["road_ref"] == section["road_ref"]
            and previous["recommendation"] == section["recommendation"]
        ):
            groups[-1].append(section)
        else:
            groups.append([section])
    zones: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        zones.append(
            {
                "zone_id": f"segment_first_zone_{index:04d}",
                "road_ref": group[0]["road_ref"],
                "road_name": group[0]["road_name"],
                "start_distance_m": group[0]["start_distance_m"],
                "end_distance_m": group[-1]["end_distance_m"],
                "length_m": float(group[-1]["end_distance_m"])
                - float(group[0]["start_distance_m"]),
                "recommendation": group[0]["recommendation"],
                "selected_area_m2": float(
                    sum(section["selected_area_m2"] for section in group)
                ),
                "area_m2": float(
                    sum(section["selected_area_m2"] for section in group)
                ),
                "effective_area_m2": float(
                    sum(section["effective_area_m2"] for section in group)
                ),
                "section_count": len(group),
                "confidence_summary": _level_summary(
                    [section["confidence"] for section in group]
                ),
                "confidence": _minimum_level(
                    [section["confidence"] for section in group]
                ),
                "analysis_quality": _minimum_level(
                    [section["analysis_quality"] for section in group]
                ),
                "reasons": sorted(
                    {
                        reason
                        for section in group
                        for reason in section.get("reasons", [])
                    }
                ),
                "geometry": mapping(
                    unary_union([shape(section["geometry"]) for section in group])
                ),
            }
        )
    return zones


def evaluate_segment_first_length(
    *,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
    selected_area_m2: float,
    association: RoadAssociation,
    aoi_wgs84: Any,
    reference: SpatialRasterObservation,
    section_length_m: int,
    raw_segmentation: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    started = clock()
    geometry_sections = build_longitudinal_section_geometries(
        aoi_wgs84, association, section_length_m=section_length_m
    )
    sections: list[dict[str, Any]] = []
    for section in geometry_sections:
        geometry_reference = shape(
            transform_geom("EPSG:4326", reference.crs, section["geometry"], precision=12)
        ).intersection(shape(reference.aoi_geometry))
        sections.append(
            _section_result(
                section,
                section_geometry_reference=geometry_reference,
                observations=observations,
                daily_records=daily_records,
                config=config,
            )
        )
    zones = merge_segment_first_sections(sections)
    area_by_class = {
        recommendation: float(
            sum(
                section["selected_area_m2"]
                for section in sections
                if section["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }
    segmentable_area = float(sum(area_by_class.values()))
    raw_areas = dict((raw_segmentation or {}).get("area_by_recommendation_m2") or {})
    elapsed = max(0.0, clock() - started)
    return {
        "section_length_m": section_length_m,
        "section_count": len(sections),
        "merged_zone_count": len(zones),
        "scene_count": len({item for record in daily_records for item in _source_ids(record)}),
        "selected_area_m2": float(selected_area_m2),
        "road_segmentable_area_m2": segmentable_area,
        "unassigned_area_m2": max(0.0, float(selected_area_m2) - segmentable_area),
        "area_by_recommendation_m2": area_by_class,
        "area_pct_by_recommendation": {
            recommendation: (
                area_by_class[recommendation] / segmentable_area * 100.0
                if segmentable_area
                else 0.0
            )
            for recommendation in CLASSES
        },
        "effective_area_m2": float(sum(section["effective_area_m2"] for section in sections)),
        "effective_coverage_pct": (
            sum(section["effective_area_m2"] for section in sections)
            / segmentable_area
            * 100.0
            if segmentable_area
            else 0.0
        ),
        "raw_diagnostic_area_by_recommendation_m2": raw_areas,
        "raw_classes_used_as_decision_input": False,
        "processing_time_seconds": round(elapsed, 6),
        "sections": sections,
        "zones": zones,
    }


def run_segment_first_temporal_analysis(
    *,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
    selected_area_m2: float,
    road_document: Mapping[str, Any],
    raw_segmentation: Mapping[str, Any] | None = None,
    section_lengths_m: Sequence[int] = (25, 50),
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    if not observations:
        raise ValueError("Raster snapshots are required for segment-first analysis.")
    original_by_id = {observation.item_id: observation for observation in observations}
    reference_id = _reference_item_id(daily_records)
    reference = original_by_id.get(reference_id)
    if reference is None:
        raise ValueError(f"Raster de referencia indisponivel: {reference_id}.")
    used_ids = {item for record in daily_records for item in _source_ids(record)}
    used = [observation for observation in observations if observation.item_id in used_ids]
    aligned = align_observations_to_reference(used, reference)
    aoi_wgs84 = shape(
        transform_geom(reference.crs, "EPSG:4326", reference.aoi_geometry, precision=12)
    )
    association = associate_road(
        [{"geometry": mapping(aoi_wgs84)}],
        road_document,
    )
    if association.status != "ASSOCIATED":
        return {
            "status": "unavailable",
            "mode": "shadow",
            "official_recommendation_changed": False,
            "road_association": association.to_dict(),
            "warnings": [association.status],
        }
    raw_copy = deepcopy(raw_segmentation) if raw_segmentation is not None else None
    raw_hash_before = _canonical_hash(raw_segmentation) if raw_segmentation is not None else None
    experiments = [
        evaluate_segment_first_length(
            observations=aligned,
            daily_records=daily_records,
            config=config,
            selected_area_m2=selected_area_m2,
            association=association,
            aoi_wgs84=aoi_wgs84,
            reference=reference,
            section_length_m=length,
            raw_segmentation=raw_copy,
            clock=clock,
        )
        for length in sorted(set(section_lengths_m))
    ]
    raw_hash_after = _canonical_hash(raw_segmentation) if raw_segmentation is not None else None
    return {
        "status": "experimental",
        "mode": "shadow",
        "architecture": "segment_first_temporal_analysis",
        "official_recommendation_changed": False,
        "road_association": association.to_dict(),
        "reference_item_id": reference_id,
        "section_lengths_m": sorted(set(section_lengths_m)),
        "experiments": experiments,
        "raw_segmentation_hash_before": raw_hash_before,
        "raw_segmentation_hash_after": raw_hash_after,
        "raw_segmentation_unchanged": raw_hash_before == raw_hash_after,
        "raw_classes_used_as_decision_input": False,
        "external_queries_per_section": 0,
        "raster_arrays_reused_in_memory": True,
        "warnings": [],
    }


def run_segment_first_shadow_segmentation(
    *,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
    selected_area_m2: float,
    effective_analysis_area_m2: float | None,
    road_document: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Preserva V1/RAW e adiciona segment-first sob a mesma feature flag."""
    raw = run_spatial_segmentation(
        observations=observations,
        daily_records=daily_records,
        config=config,
        selected_area_m2=selected_area_m2,
        effective_analysis_area_m2=effective_analysis_area_m2,
        clock=clock,
    )
    try:
        roads = road_document or json.loads(
            DEFAULT_ROAD_DOCUMENT.read_text(encoding="utf-8")
        )
        segment_first = run_segment_first_temporal_analysis(
            observations=observations,
            daily_records=daily_records,
            config=config,
            selected_area_m2=selected_area_m2,
            road_document=roads,
            raw_segmentation=raw,
            section_lengths_m=(config.spatial_section_length_m,),
            clock=clock,
        )
        if segment_first.get("status") != "experimental":
            raise ValueError(
                ",".join(segment_first.get("warnings") or ["SEGMENT_FIRST_UNAVAILABLE"])
            )
        return build_spatial_segmentation_contract(
            segment_first["experiments"][0],
            section_length_m=config.spatial_section_length_m,
            decision_min_observations=config.decision_min_observations,
        )
    except Exception:
        return {
            "status": "unavailable",
            "experimental": True,
            "section_length_m": config.spatial_section_length_m,
            "effective_coverage_pct": None,
            "zones": [],
        }


def build_spatial_segmentation_contract(
    experiment: Mapping[str, Any],
    *,
    section_length_m: int,
    decision_min_observations: int,
) -> dict[str, Any]:
    """Aplica o gate sem criar novos thresholds de recommendation ou qualidade."""
    sections = list(experiment.get("sections") or [])
    supported_sections = [
        section
        for section in sections
        if section["selected_area_m2"] > 0
        and section["effective_area_m2"] > 0
        and section["valid_observation_count"] >= decision_min_observations
    ]
    eligible = bool(sections) and len(supported_sections) == len(sections)
    zones = (
        [
            {
                "zone_id": zone["zone_id"],
                "recommendation": zone["recommendation"],
                "geometry": zone["geometry"],
                "area_m2": zone.get("area_m2", zone["selected_area_m2"]),
                "confidence": zone.get(
                    "confidence",
                    (zone.get("confidence_summary") or {}).get("minimum", "low"),
                ),
                "analysis_quality": zone["analysis_quality"],
                "reasons": list(zone.get("reasons") or []),
                "start_distance_m": zone["start_distance_m"],
                "end_distance_m": zone["end_distance_m"],
                "road_ref": zone["road_ref"],
            }
            for zone in experiment.get("zones") or []
        ]
        if eligible
        else []
    )
    return {
        "status": "available" if eligible else "not_applicable",
        "experimental": True,
        "section_length_m": section_length_m,
        "effective_coverage_pct": experiment.get("effective_coverage_pct"),
        "zones": zones,
    }
