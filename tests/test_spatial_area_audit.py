"""Testes offline e direcionados da auditoria de contabilidade espacial."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from shapely.geometry import box, mapping

from src.satellite_monitoring.experiments.spatial_area_audit import (
    geometry_area_diagnostic,
    pixel_intersection_records,
    write_spatial_audit,
)


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AOI = ROOT / "data" / "aoi" / "frango_assado_area_audit.geojson"


def test_official_geometry_has_independent_positive_area_estimates() -> None:
    result = geometry_area_diagnostic(json.loads(OFFICIAL_AOI.read_text(encoding="utf-8")))

    assert result["geometry_type"] == "Polygon"
    assert result["valid"] is True
    assert result["self_intersects"] is False
    assert result["coordinates_plausible"] is True
    assert result["geodesic_area_m2"] > 0
    assert result["projected_area_m2"] > 0
    assert result["projected_crs"] == "EPSG:32723"
    assert result["relative_difference_pct"] < 0.1


def test_partial_pixels_differ_from_full_nominal_pixel_accounting() -> None:
    geometry = box(5.0, 0.0, 15.0, 10.0)
    mask = np.array([[True, True]], dtype=bool)
    records = pixel_intersection_records(
        geometry,
        transform_affine=Affine(10, 0, 0, 0, 10, 0),
        shape_rows_columns=(1, 2),
        center_inside_mask=mask,
        quality_valid_mask=mask,
    )

    assert len(records) == 2
    assert sum(row["pixel_nominal_area_m2"] for row in records) == pytest.approx(200.0)
    assert sum(row["intersection_area_m2"] for row in records) == pytest.approx(100.0)
    assert [row["intersection_fraction"] for row in records] == pytest.approx([0.5, 0.5])


def test_narrow_polygon_can_intersect_pixels_without_containing_their_centers() -> None:
    geometry = box(9.5, 0.0, 10.5, 10.0)
    outside_centers = np.array([[False, False]], dtype=bool)
    records = pixel_intersection_records(
        geometry,
        transform_affine=Affine(10, 0, 0, 0, 10, 0),
        shape_rows_columns=(1, 2),
        center_inside_mask=outside_centers,
        quality_valid_mask=outside_centers,
    )

    assert len(records) == 2
    assert sum(row["intersection_area_m2"] for row in records) == pytest.approx(10.0)
    assert all(row["center_inside_aoi"] is False for row in records)


def test_spatial_audit_json_is_deterministic(tmp_path: Path) -> None:
    payload = {
        "geometry": {"geodesic_area_m2": 4418.75},
        "pixel_intersections": [mapping(box(0, 0, 1, 1))],
        "warnings": [],
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_spatial_audit(payload, first)
    write_spatial_audit(payload, second)

    assert first.read_bytes() == second.read_bytes()
