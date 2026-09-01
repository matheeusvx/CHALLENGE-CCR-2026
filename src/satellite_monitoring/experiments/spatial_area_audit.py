"""Auditoria independente de área geométrica e contabilidade raster."""

from __future__ import annotations

import json
import math
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import rasterio
from pyproj import CRS, Geod, Transformer
from rasterio.features import geometry_window
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, box, shape
from shapely.ops import orient, transform

from ..features.vegetation_mask import build_height_valid_mask
from ..geometry import extract_polygon_geometry, validate_polygon_geometry
from ..indices import calculate_ndvi
from ..raster_processing import (
    SCL_EXCLUDED_CLASSES,
    _scale_and_offset,
    find_asset_key,
    physical_reflectance_arrays,
)


def geometry_area_diagnostic(document: Mapping[str, Any]) -> dict[str, Any]:
    geometry, feature_count = extract_polygon_geometry(dict(document))
    validated = validate_polygon_geometry(geometry)
    centroid = validated.centroid
    zone = int((centroid.x + 180) // 6) + 1
    epsg = 32700 + zone if centroid.y < 0 else 32600 + zone
    projected_crs = CRS.from_epsg(epsg)
    forward = Transformer.from_crs(
        "EPSG:4326", projected_crs, always_xy=True
    ).transform
    projected = transform(forward, validated)
    geod = Geod(ellps="WGS84")
    geodesic_area = 0.0
    polygons = validated.geoms if validated.geom_type == "MultiPolygon" else [validated]
    for polygon in polygons:
        area, _ = geod.geometry_area_perimeter(orient(polygon, sign=1.0))
        geodesic_area += abs(float(area))
    projected_area = float(projected.area)
    absolute_difference = abs(geodesic_area - projected_area)
    return {
        "geometry_type": validated.geom_type,
        "feature_count": feature_count,
        "valid": validated.is_valid,
        "self_intersects": not validated.is_simple,
        "coordinates_plausible": (
            -180 <= validated.bounds[0] <= 180
            and -90 <= validated.bounds[1] <= 90
            and -180 <= validated.bounds[2] <= 180
            and -90 <= validated.bounds[3] <= 90
        ),
        "positive_area": geodesic_area > 0 and projected_area > 0,
        "geodesic_area_m2": geodesic_area,
        "projected_area_m2": projected_area,
        "projected_crs": projected_crs.to_string(),
        "absolute_difference_m2": absolute_difference,
        "relative_difference_pct": (
            absolute_difference / geodesic_area * 100.0 if geodesic_area else None
        ),
        "bounding_box": [float(value) for value in validated.bounds],
    }


def compare_frontend_area(
    geodesic_area_m2: float, *, displayed_area_m2: float
) -> dict[str, Any]:
    difference = displayed_area_m2 - geodesic_area_m2
    percentage = difference / geodesic_area_m2 * 100.0
    absolute_percentage = abs(percentage)
    classification = (
        "CONSISTENT"
        if absolute_percentage <= 1.0
        else "MINOR_DIFFERENCE"
        if absolute_percentage <= 5.0
        else "MATERIAL_DIFFERENCE"
    )
    return {
        "displayed_area_m2": displayed_area_m2,
        "frontend_vs_geodesic_difference_m2": difference,
        "frontend_vs_geodesic_difference_pct": percentage,
        "diagnostic_classification": classification,
        "classification_thresholds_pct": {
            "consistent_max": 1.0,
            "minor_difference_max": 5.0,
        },
    }


def pixel_intersection_records(
    geometry: Any,
    *,
    transform_affine: Any,
    shape_rows_columns: tuple[int, int],
    center_inside_mask: np.ndarray,
    quality_valid_mask: np.ndarray,
    height_valid_mask: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    rows, columns = shape_rows_columns
    for row in range(rows):
        for column in range(columns):
            corners = [
                transform_affine @ (column, row),
                transform_affine @ (column + 1, row),
                transform_affine @ (column + 1, row + 1),
                transform_affine @ (column, row + 1),
            ]
            cell = Polygon(corners)
            intersection_area = float(geometry.intersection(cell).area)
            if intersection_area <= 0:
                continue
            nominal_area = float(cell.area)
            records.append(
                {
                    "pixel_id": f"r{row}_c{column}",
                    "row": row,
                    "column": column,
                    "pixel_nominal_area_m2": nominal_area,
                    "intersection_area_m2": intersection_area,
                    "intersection_fraction": intersection_area / nominal_area,
                    "center_inside_aoi": bool(center_inside_mask[row, column]),
                    "quality_valid": bool(quality_valid_mask[row, column]),
                    "height_valid": bool(height_valid_mask[row, column])
                    if height_valid_mask is not None
                    else None,
                }
            )
    return records


def audit_operational_scene(
    item: Any,
    aoi_geojson: Mapping[str, Any],
    raster_data: Any,
) -> dict[str, Any]:
    red_key = find_asset_key(item, {"red"}, ("red", "B04"))
    nir_key = find_asset_key(item, {"nir", "nir08"}, ("nir", "B08"))
    if red_key is None or nir_key is None:
        raise ValueError("Operational RED/NIR reference assets are unavailable.")
    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    with rasterio.Env(**env_options), ExitStack() as stack:
        reference = stack.enter_context(rasterio.open(item.assets[red_key].href))
        aoi_projected_document = transform_geom(
            "EPSG:4326", reference.crs, dict(aoi_geojson), precision=15
        )
        aoi_projected = shape(aoi_projected_document)
        window = geometry_window(reference, [aoi_projected_document])
        output_transform = reference.window_transform(window)
        raster_extent = box(*reference.bounds)
        covered_geometry = aoi_projected.intersection(raster_extent)
        working_crs = str(reference.crs)
    center_inside = np.asarray(raster_data.inside_aoi_mask, dtype=bool)
    operational_valid = np.asarray(raster_data.valid_mask, dtype=bool)
    _, ndvi_valid = calculate_ndvi(
        raster_data.red, raster_data.nir, operational_valid
    )
    operational_valid &= ndvi_valid
    height_valid: np.ndarray | None = None
    height_diagnostic_error: str | None = None
    if raster_data.scl_values is not None and raster_data.scl_valid_mask is not None:
        try:
            red_physical, nir_physical, _ = physical_reflectance_arrays(item, raster_data)
            height_valid = build_height_valid_mask(
                red_physical,
                nir_physical,
                quality_valid_mask=np.asarray(raster_data.valid_mask, dtype=bool),
                inside_aoi_mask=center_inside,
                scl_values=np.asarray(raster_data.scl_values),
                scl_valid_mask=np.asarray(raster_data.scl_valid_mask, dtype=bool),
            ).valid_mask
        except Exception as exc:  # Diagnostico de altura nao bloqueia a area operacional.
            height_diagnostic_error = str(exc)
    records = pixel_intersection_records(
        aoi_projected,
        transform_affine=output_transform,
        shape_rows_columns=center_inside.shape,
        center_inside_mask=center_inside,
        quality_valid_mask=operational_valid,
        height_valid_mask=height_valid,
    )
    _, _, red_nodata = _scale_and_offset(item.assets[red_key])
    _, _, nir_nodata = _scale_and_offset(item.assets[nir_key])
    reasons: Counter[str] = Counter()
    scl = raster_data.scl_values
    scl_valid = raster_data.scl_valid_mask
    for record in records:
        row = int(record["row"])
        column = int(record["column"])
        pixel_reasons: list[str] = []
        if not record["center_inside_aoi"]:
            pixel_reasons.append("PIXEL_CENTER_OUTSIDE_AOI")
        else:
            red_raw = float(raster_data.red_raw[row, column])
            nir_raw = float(raster_data.nir_raw[row, column])
            if not math.isfinite(red_raw) or not math.isfinite(nir_raw):
                pixel_reasons.append("NON_FINITE")
            elif red_raw == float(red_nodata or 0) or nir_raw == float(nir_nodata or 0):
                pixel_reasons.append("INVALID_RADIOMETRY")
            if scl is not None and scl_valid is not None:
                if not bool(scl_valid[row, column]) or int(scl[row, column]) in SCL_EXCLUDED_CLASSES:
                    pixel_reasons.append("SCL_REJECTED")
            if not ndvi_valid[row, column] and not pixel_reasons:
                pixel_reasons.append("NON_FINITE_NDVI")
            if not bool(operational_valid[row, column]) and not pixel_reasons:
                pixel_reasons.append("INVALID_RADIOMETRY")
        record["accepted"] = bool(operational_valid[row, column])
        record["rejection_reasons"] = [] if record["accepted"] else pixel_reasons
        reasons.update(record["rejection_reasons"])
    selected_area = float(aoi_projected.area)
    covered_area = float(covered_geometry.area)
    intersecting_area = sum(float(row["intersection_area_m2"]) for row in records)
    valid_area = sum(
        float(row["intersection_area_m2"]) for row in records if row["accepted"]
    )
    height_area = sum(
        float(row["intersection_area_m2"])
        for row in records
        if row["height_valid"] is True
    )
    accepted_count = sum(bool(row["accepted"]) for row in records)
    pipeline_rejected_count = sum(
        bool(row["center_inside_aoi"]) and not bool(row["accepted"])
        for row in records
    )
    return {
        "working_crs": working_crs,
        "effective_resolution_m": [
            abs(float(output_transform.a)),
            abs(float(output_transform.e)),
        ],
        "nominal_pixel_area_m2": abs(
            float(output_transform.a * output_transform.e - output_transform.b * output_transform.d)
        ),
        "intersecting_pixel_count": len(records),
        "pipeline_center_inside_pixel_count": int(np.count_nonzero(center_inside)),
        "accepted_pixel_count": accepted_count,
        "rejected_pixel_count": len(records) - accepted_count,
        "pipeline_rejected_pixel_count": pipeline_rejected_count,
        "nominal_intersecting_area_m2": sum(
            float(row["pixel_nominal_area_m2"]) for row in records
        ),
        "actual_intersection_area_m2": intersecting_area,
        "selected_area_projected_m2": selected_area,
        "covered_area_m2": covered_area,
        "quality_valid_area_m2": valid_area,
        "analysis_effective_area_m2": valid_area,
        "height_valid_area_m2": height_area,
        "height_diagnostic_status": (
            "AVAILABLE" if height_valid is not None else "NOT_CURRENTLY_TRACKED"
        ),
        "height_diagnostic_error": height_diagnostic_error,
        "coverage_pct": covered_area / selected_area * 100.0,
        "quality_valid_pct_of_selected": valid_area / selected_area * 100.0,
        "effective_analysis_pct_of_selected": valid_area / selected_area * 100.0,
        "height_valid_pct_of_selected": height_area / selected_area * 100.0,
        "rejection_reason_counts": dict(sorted(reasons.items())),
        "pixel_intersections": records,
        "pixel_intersection_strategy": {
            "pipeline": "CENTER_OF_PIXEL_INSIDE_OUTSIDE",
            "all_touched": False,
            "fraction_weighted_metrics": False,
            "diagnostic_intersection_fractions_only": True,
        },
    }


def write_spatial_audit(payload: Mapping[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
