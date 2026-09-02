"""Segmentacao espacial experimental sobre rasters Sentinel ja processados."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any, Callable, Sequence

import numpy as np
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_geom
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .config import MonitoringConfig
from .cut_recommendation import (
    RecommendationInput,
    RecommendationThresholds,
    recommend_cut,
)
from .temporal_quality import calculate_analysis_quality, diagnose_temporal_consistency


@dataclass(frozen=True)
class SpatialRasterObservation:
    """Snapshot em memoria de uma cena ja adquirida e processada."""

    item_id: str
    datetime: str
    ndvi: np.ndarray
    valid_mask: np.ndarray
    inside_aoi_mask: np.ndarray
    transform: Any
    aoi_geometry: dict[str, Any]
    crs: str
    crs_is_projected: bool
    scene_quality_score: float | None = None
    quality_status: str = "unknown"
    cloud_cover: float | None = None
    rejection_reasons: np.ndarray | None = None
    has_scl: bool = True
    scl_values: np.ndarray | None = None
    scl_class_percentages: dict[str, float] | None = None
    aoi_coverage_percentage: float = 100.0
    partial_raster_coverage: bool = False


@dataclass(frozen=True)
class _CellPart:
    row: int
    column: int
    cell_id: str
    nominal_geometry: Polygon
    geometry: Any
    cell_area_m2: float
    intersection_area_m2: float
    intersection_fraction: float


def _thresholds(config: MonitoringConfig) -> RecommendationThresholds:
    return RecommendationThresholds(
        decision_min_observations=config.decision_min_observations,
        high_vegetation_percentile=config.high_vegetation_percentile,
        significant_drop_absolute=config.significant_drop_absolute,
        significant_drop_relative_percentage=config.significant_drop_relative_percentage,
        trend_window=config.trend_window,
        max_gap_days=config.max_gap_days,
        recent_intervention_days=config.recent_intervention_days,
    )


def _same_grid(
    observation: SpatialRasterObservation,
    reference: SpatialRasterObservation,
) -> bool:
    return (
        observation.ndvi.shape == reference.ndvi.shape
        and observation.crs == reference.crs
        and np.allclose(tuple(observation.transform), tuple(reference.transform))
    )


def _align_observation(
    observation: SpatialRasterObservation,
    reference: SpatialRasterObservation,
) -> SpatialRasterObservation:
    """Reprojeta arrays ja carregados uma unica vez para a grade RED de referencia."""
    if _same_grid(observation, reference):
        return observation
    destination_shape = reference.ndvi.shape
    ndvi = np.full(destination_shape, np.nan, dtype=np.float64)
    reproject(
        source=np.asarray(observation.ndvi, dtype=np.float64),
        destination=ndvi,
        src_transform=observation.transform,
        src_crs=observation.crs,
        src_nodata=np.nan,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
        init_dest_nodata=True,
    )
    valid = np.zeros(destination_shape, dtype=np.uint8)
    reproject(
        source=np.asarray(observation.valid_mask, dtype=np.uint8),
        destination=valid,
        src_transform=observation.transform,
        src_crs=observation.crs,
        src_nodata=0,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        dst_nodata=0,
        resampling=Resampling.nearest,
        init_dest_nodata=True,
    )
    reason_names = {
        1: "SCL_REJECTED",
        2: "QUALITY_MASK_REJECTED",
        3: "NON_FINITE_NDVI",
    }
    source_reasons = np.zeros(observation.ndvi.shape, dtype=np.uint8)
    if observation.rejection_reasons is not None:
        source_reasons[observation.rejection_reasons == "SCL_REJECTED"] = 1
        source_reasons[observation.rejection_reasons == "QUALITY_MASK_REJECTED"] = 2
        source_reasons[observation.rejection_reasons == "NON_FINITE_NDVI"] = 3
    aligned_reason_codes = np.zeros(destination_shape, dtype=np.uint8)
    reproject(
        source=source_reasons,
        destination=aligned_reason_codes,
        src_transform=observation.transform,
        src_crs=observation.crs,
        src_nodata=0,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        dst_nodata=0,
        resampling=Resampling.nearest,
        init_dest_nodata=True,
    )
    rejection_reasons = np.full(destination_shape, "", dtype=object)
    for code, name in reason_names.items():
        rejection_reasons[aligned_reason_codes == code] = name
    aligned_valid = valid.astype(bool) & np.isfinite(ndvi)
    rejection_reasons[~aligned_valid & (rejection_reasons == "")] = (
        "OUTSIDE_SOURCE_RASTER_OR_INVALID"
    )
    aligned_scl = None
    if observation.scl_values is not None:
        aligned_scl = np.zeros(destination_shape, dtype=np.int16)
        reproject(
            source=np.asarray(observation.scl_values, dtype=np.int16),
            destination=aligned_scl,
            src_transform=observation.transform,
            src_crs=observation.crs,
            src_nodata=0,
            dst_transform=reference.transform,
            dst_crs=reference.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
            init_dest_nodata=True,
        )
    return SpatialRasterObservation(
        item_id=observation.item_id,
        datetime=observation.datetime,
        ndvi=ndvi,
        valid_mask=aligned_valid,
        inside_aoi_mask=np.asarray(reference.inside_aoi_mask, dtype=bool),
        transform=reference.transform,
        aoi_geometry=reference.aoi_geometry,
        crs=reference.crs,
        crs_is_projected=reference.crs_is_projected,
        scene_quality_score=observation.scene_quality_score,
        quality_status=observation.quality_status,
        cloud_cover=observation.cloud_cover,
        rejection_reasons=rejection_reasons,
        has_scl=observation.has_scl,
        scl_values=aligned_scl,
        scl_class_percentages=observation.scl_class_percentages,
        aoi_coverage_percentage=observation.aoi_coverage_percentage,
        partial_raster_coverage=observation.partial_raster_coverage,
    )


def align_observations_to_reference(
    observations: Sequence[SpatialRasterObservation],
    reference: SpatialRasterObservation,
) -> list[SpatialRasterObservation]:
    """Alinha uma vez por cena; nunca executa consulta externa por celula."""
    return [_align_observation(observation, reference) for observation in observations]


def _pixel_polygon(transform: Any, row: int, column: int) -> Polygon:
    return Polygon(
        [
            transform @ (column, row),
            transform @ (column + 1, row),
            transform @ (column + 1, row + 1),
            transform @ (column, row + 1),
        ]
    )


def build_grid_cells(reference: SpatialRasterObservation) -> list[_CellPart]:
    """Constroi celulas deterministicas aceitas na observacao de referencia."""
    if not reference.crs_is_projected:
        raise ValueError("A grade de segmentacao precisa estar em CRS metrico projetado.")
    accepted = np.asarray(reference.valid_mask, dtype=bool)
    inside = np.asarray(reference.inside_aoi_mask, dtype=bool)
    if accepted.shape != inside.shape or accepted.shape != reference.ndvi.shape:
        raise ValueError("NDVI e mascaras espaciais precisam compartilhar a mesma grade.")
    aoi = shape(reference.aoi_geometry)
    cells: list[_CellPart] = []
    for row, column in np.argwhere(accepted & inside):
        row_value = int(row)
        column_value = int(column)
        nominal = _pixel_polygon(reference.transform, row_value, column_value)
        clipped = nominal.intersection(aoi)
        if clipped.is_empty or clipped.area <= 0:
            continue
        nominal_area = float(nominal.area)
        intersection_area = float(clipped.area)
        cells.append(
            _CellPart(
                row=row_value,
                column=column_value,
                cell_id=f"cell_r{row_value:06d}_c{column_value:06d}",
                nominal_geometry=nominal,
                geometry=clipped,
                cell_area_m2=nominal_area,
                intersection_area_m2=intersection_area,
                intersection_fraction=(intersection_area / nominal_area),
            )
        )
    return cells


def _source_ids(daily_record: dict[str, Any]) -> list[str]:
    if daily_record.get("daily_aggregation") == "median":
        return sorted(str(value) for value in daily_record.get("aggregation_source_item_ids") or [])
    selected = daily_record.get("aggregation_selected_item_id")
    if selected:
        return [str(selected)]
    return sorted(str(value) for value in daily_record.get("aggregation_source_item_ids") or [])[:1]


def build_cell_timeseries(
    *,
    row: int,
    column: int,
    daily_records: Sequence[dict[str, Any]],
    observations_by_id: dict[str, SpatialRasterObservation],
    reference: SpatialRasterObservation,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extrai NDVI local sem novas consultas e preserva invalidacoes conhecidas."""
    valid_rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for daily_record in sorted(daily_records, key=lambda value: str(value.get("datetime", ""))):
        values: list[float] = []
        source_ids = _source_ids(daily_record)
        reasons: list[str] = []
        usable_sources: list[SpatialRasterObservation] = []
        for item_id in source_ids:
            observation = observations_by_id.get(item_id)
            if observation is None:
                reasons.append("RASTER_SNAPSHOT_UNAVAILABLE")
                continue
            if not _same_grid(observation, reference):
                reasons.append("GRID_MISMATCH")
                continue
            usable_sources.append(observation)
            if bool(observation.valid_mask[row, column]):
                value = float(observation.ndvi[row, column])
                if np.isfinite(value):
                    values.append(value)
                else:
                    reasons.append("NON_FINITE_NDVI")
            else:
                reason = "QUALITY_MASK_REJECTED"
                if observation.rejection_reasons is not None:
                    candidate = observation.rejection_reasons[row, column]
                    if candidate:
                        reason = str(candidate)
                reasons.append(reason)

        is_valid = bool(values)
        audit.append(
            {
                "datetime": daily_record.get("datetime"),
                "source_item_ids": source_ids,
                "valid": is_valid,
                "rejection_reasons": sorted(set(reasons)) if not is_valid else [],
            }
        )
        if not is_valid:
            continue
        value = float(np.median(values)) if len(values) > 1 else values[0]
        quality_scores = [
            float(item.scene_quality_score)
            for item in usable_sources
            if item.scene_quality_score is not None
        ]
        quality_levels = [item.quality_status for item in usable_sources]
        quality_status = min(
            quality_levels or ["unknown"],
            key={"unknown": -1, "low": 0, "medium": 1, "high": 2}.get,
        )
        valid_rows.append(
            {
                "item_id": f"{daily_record.get('item_id', 'daily')}:{row}:{column}",
                "datetime": daily_record.get("datetime"),
                "ndvi_mean": value,
                "ndvi_median": value,
                "valid_pixel_percentage": 100.0,
                "aoi_coverage_percentage": 100.0,
                "partial_raster_coverage": False,
                "scene_quality_score": (
                    float(np.median(quality_scores)) if quality_scores else None
                ),
                "quality_status": quality_status,
                "accepted_for_timeseries": True,
                "aggregation_source_item_ids": source_ids,
            }
        )
    return valid_rows, audit


def classify_cell_timeseries(
    records: Sequence[dict[str, Any]],
    *,
    config: MonitoringConfig,
    processed_scene_count: int | None = None,
    rejected_scene_count: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Aplica a mesma implementacao central da recommendation ao escopo local."""
    temporal = diagnose_temporal_consistency(
        records,
        min_deviation=config.temporal_outlier_min_deviation,
        mad_multiplier=config.temporal_outlier_mad_multiplier,
        return_ratio=config.temporal_return_ratio,
    )
    quality = calculate_analysis_quality(
        temporal.analysis_timeseries,
        raw_records=temporal.raw_daily_timeseries,
        processed_scene_count=(
            len(records) if processed_scene_count is None else processed_scene_count
        ),
        rejected_scene_count=rejected_scene_count,
        max_gap_days=config.max_gap_days,
        min_observations=config.min_observations,
    )
    for record in temporal.analysis_timeseries:
        record["analysis_quality_status"] = quality["status"]
    assert config.end_date is not None
    recommendation = recommend_cut(
        RecommendationInput(
            observations=temporal.analysis_timeseries,
            thresholds=_thresholds(config),
            reference_date=config.end_date,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        )
    ).to_dict()
    return recommendation, quality, temporal.analysis_timeseries


def _reference_item_id(daily_records: Sequence[dict[str, Any]]) -> str:
    if not daily_records:
        raise ValueError("Nao ha observacao temporal para definir a grade de referencia.")
    latest = sorted(daily_records, key=lambda value: str(value.get("datetime", "")))[-1]
    ids = _source_ids(latest)
    if not ids:
        raise ValueError("A observacao mais recente nao referencia raster processado.")
    return ids[0]


def _representative_codes(cells: Sequence[dict[str, Any]]) -> list[str]:
    counts = Counter(
        code
        for cell in cells
        for code in cell.get("reason_codes", [])
    )
    return [code for code, _ in sorted(counts.items(), key=lambda value: (-value[1], value[0]))]


def _conservative_level(cells: Sequence[dict[str, Any]], field: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return min(
        (str(cell.get(field, "low")) for cell in cells),
        key=lambda value: order.get(value, -1),
        default="low",
    )


def group_cells_4_neighbor(
    cells: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Agrupa somente vizinhos ortogonais com a mesma recommendation."""
    by_position = {(int(cell["row"]), int(cell["column"])): cell for cell in cells}
    pending = set(by_position)
    groups: list[list[dict[str, Any]]] = []
    for start in sorted(by_position):
        if start not in pending:
            continue
        expected = by_position[start]["recommendation"]
        queue: deque[tuple[int, int]] = deque([start])
        pending.remove(start)
        group: list[dict[str, Any]] = []
        while queue:
            position = queue.popleft()
            group.append(by_position[position])
            row, column = position
            for neighbor in ((row - 1, column), (row, column - 1), (row, column + 1), (row + 1, column)):
                if neighbor in pending and by_position[neighbor]["recommendation"] == expected:
                    pending.remove(neighbor)
                    queue.append(neighbor)
        groups.append(sorted(group, key=lambda cell: (cell["row"], cell["column"])))
    return groups


def _geojson_geometry(projected_geometry: Any, crs: str) -> dict[str, Any]:
    document = mapping(projected_geometry)
    return transform_geom(crs, "EPSG:4326", document, precision=12)


def run_spatial_segmentation(
    *,
    observations: Sequence[SpatialRasterObservation],
    daily_records: Sequence[dict[str, Any]],
    config: MonitoringConfig,
    selected_area_m2: float,
    effective_analysis_area_m2: float | None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Executa segmentacao shadow sem rede e sem modificar metricas globais."""
    started = clock()
    original_by_id = {observation.item_id: observation for observation in observations}
    reference_id = _reference_item_id(daily_records)
    reference = original_by_id.get(reference_id)
    if reference is None:
        raise ValueError(f"Raster de referencia indisponivel: {reference_id}.")
    used_item_ids = {
        item_id for record in daily_records for item_id in _source_ids(record)
    }
    used_observations = [
        observation
        for observation in observations
        if observation.item_id in used_item_ids
    ]
    aligned_observations = align_observations_to_reference(
        used_observations, reference
    )
    by_id = {observation.item_id: observation for observation in aligned_observations}
    cell_parts = build_grid_cells(reference)
    cells: list[dict[str, Any]] = []
    geometry_by_cell: dict[str, Any] = {}
    for part in cell_parts:
        series, observation_audit = build_cell_timeseries(
            row=part.row,
            column=part.column,
            daily_records=daily_records,
            observations_by_id=by_id,
            reference=reference,
        )
        recommendation, quality, analyzed_series = classify_cell_timeseries(
            series,
            config=config,
        )
        reason_codes = sorted(
            set(recommendation.get("reasons") or [])
            | set(recommendation.get("blocking_reasons") or [])
        )
        geometry_by_cell[part.cell_id] = part.geometry
        cells.append(
            {
                "cell_id": part.cell_id,
                "row": part.row,
                "column": part.column,
                "geometry": _geojson_geometry(part.geometry, reference.crs),
                "cell_area_m2": part.cell_area_m2,
                "intersection_area_m2": part.intersection_area_m2,
                "intersection_fraction": part.intersection_fraction,
                "effective_area_m2": part.intersection_area_m2,
                "valid_observation_count": len(analyzed_series),
                "recommendation": recommendation["recommendation"],
                "confidence": recommendation["confidence"],
                "analysis_quality": quality["status"],
                "reason_codes": reason_codes,
                "latest_ndvi": recommendation["metrics"].get("current_ndvi_mean"),
                "recent_trend": recommendation["metrics"].get("recent_trend"),
                "observation_audit": observation_audit,
            }
        )

    aoi = shape(reference.aoi_geometry)
    zones: list[dict[str, Any]] = []
    for index, grouped_cells in enumerate(group_cells_4_neighbor(cells), start=1):
        projected = unary_union(
            [geometry_by_cell[cell["cell_id"]] for cell in grouped_cells]
        ).intersection(aoi)
        zones.append(
            {
                "zone_id": f"zone_{index:04d}",
                "recommendation": grouped_cells[0]["recommendation"],
                "geometry": _geojson_geometry(projected, reference.crs),
                "area_m2": float(projected.area),
                "cell_count": len(grouped_cells),
                "confidence": _conservative_level(grouped_cells, "confidence"),
                "analysis_quality": _conservative_level(grouped_cells, "analysis_quality"),
                "representative_reason_codes": _representative_codes(grouped_cells),
            }
        )

    segmented_area = float(sum(zone["area_m2"] for zone in zones))
    areas = {
        recommendation: float(
            sum(zone["area_m2"] for zone in zones if zone["recommendation"] == recommendation)
        )
        for recommendation in ("cortar", "nao_cortar", "inconclusivo")
    }
    elapsed = max(0.0, clock() - started)
    return {
        "status": "experimental",
        "mode": "shadow",
        "official_recommendation_changed": False,
        "reference_item_id": reference_id,
        "grid_crs": reference.crs,
        "grid_resolution": {
            "x": abs(float(reference.transform.a)),
            "y": abs(float(reference.transform.e)),
            "unit": "crs_unit",
            "precision_claim": False,
        },
        "pixel_intersection_strategy": "CENTER_OF_PIXEL_INSIDE_OUTSIDE",
        "selected_area_m2": float(selected_area_m2),
        "effective_analysis_area_m2": effective_analysis_area_m2,
        "segmented_area_m2": segmented_area,
        "unclassified_area_m2": max(0.0, float(selected_area_m2) - segmented_area),
        "segmented_pct_of_selected": (
            segmented_area / selected_area_m2 * 100.0 if selected_area_m2 else None
        ),
        "unclassified_pct_of_selected": (
            max(0.0, selected_area_m2 - segmented_area) / selected_area_m2 * 100.0
            if selected_area_m2
            else None
        ),
        "area_by_recommendation_m2": areas,
        "cells": cells,
        "zones": zones,
        "performance": {
            "number_of_cells": len(cells),
            "number_of_scenes": len(
                {item_id for item_id in used_item_ids if item_id in by_id}
            ),
            "processing_time_seconds": round(elapsed, 6),
            "external_queries_per_cell": 0,
            "raster_arrays_reused_in_memory": True,
            "raster_alignment_count": sum(
                not _same_grid(observation, reference)
                for observation in used_observations
            ),
        },
        "warnings": [],
    }
