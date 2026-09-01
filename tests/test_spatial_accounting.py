"""Regressoes offline da contabilidade espacial operacional."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import mapping, shape
from shapely.ops import transform

from src.satellite_monitoring.geometry import calculate_geometry_metadata
from src.satellite_monitoring.raster_processing import (
    RasterSceneData,
    calculate_effective_analysis_area,
)


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AOI = ROOT / "data" / "aoi" / "frango_assado_area_audit.geojson"


def _raster_data(
    accepted: np.ndarray,
    *,
    affine: Affine,
    projected_geometry: dict,
) -> RasterSceneData:
    values = np.ones(accepted.shape, dtype=np.float32)
    return RasterSceneData(
        red=values,
        nir=values,
        valid_mask=accepted,
        total_pixel_count=int(np.count_nonzero(accepted)),
        aoi_coverage_percentage=100.0,
        partial_raster_coverage=False,
        red_asset="B04",
        nir_asset="B08",
        scl_asset="SCL",
        scl_class_percentages={},
        quality_messages=[],
        spatial_transform=affine,
        spatial_aoi_geometry=projected_geometry,
        spatial_crs="EPSG:32723",
        spatial_crs_is_projected=True,
    )


def test_effective_area_uses_partial_intersections_not_pixel_count_times_100() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[5, 0], [15, 0], [15, 10], [5, 10], [5, 0]]],
    }
    accepted = np.array([[True, True]], dtype=bool)
    area = calculate_effective_analysis_area(
        _raster_data(
            accepted,
            affine=Affine(10, 0, 0, 0, 10, 0),
            projected_geometry=geometry,
        ),
        accepted,
    )

    assert area == pytest.approx(100.0)
    assert area != int(np.count_nonzero(accepted)) * 100


def test_frango_assado_reference_spatial_accounting() -> None:
    document = json.loads(OFFICIAL_AOI.read_text(encoding="utf-8"))
    geographic = shape(document["geometry"])
    projected = transform(
        Transformer.from_crs("EPSG:4326", "EPSG:32723", always_xy=True).transform,
        geographic,
    )
    affine = Affine(10, 0, 311020, 0, -10, 7423210)
    center_inside = geometry_mask(
        [mapping(projected)],
        out_shape=(9, 11),
        transform=affine,
        invert=True,
    )
    raster = _raster_data(
        center_inside,
        affine=affine,
        projected_geometry=mapping(projected),
    )
    selected = calculate_geometry_metadata(
        geographic,
        source="geojson_inline",
        feature_count=1,
    )["area_square_meters"]
    effective = calculate_effective_analysis_area(raster, center_inside)
    percentage = effective / selected * 100.0

    assert selected == pytest.approx(4419.4683, abs=0.01)
    assert effective == pytest.approx(4113.2001, abs=0.01)
    assert percentage == pytest.approx(93.07, abs=0.02)
    assert np.count_nonzero(center_inside) == 45
    assert effective != 45 * 100
