"""Geometry and safety tests for AUTO-03C roadside AOIs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely.geometry import LineString, shape, mapping
from shapely.ops import transform

from src.satellite_monitoring.road_geometry import (
    LocalGeoJsonRoadGeometryProvider,
    resolve_canonical_road_section,
)
from src.satellite_monitoring.roadside_aoi import RoadsideAoiConfig, build_roadside_aoi


FORWARD = Transformer.from_crs("EPSG:4326", "EPSG:32723", always_xy=True)
REVERSE = Transformer.from_crs("EPSG:32723", "EPSG:4326", always_xy=True)
ORIGIN_X, ORIGIN_Y = FORWARD.transform(-47.0, -23.0)


def _line(points: list[tuple[float, float]]) -> LineString:
    return transform(
        REVERSE.transform,
        LineString([(ORIGIN_X + x, ORIGIN_Y + y) for x, y in points]),
    )


def _feature(
    axis_id: str,
    points: list[tuple[float, float]],
    *,
    road_id: str | None = "SP-001",
    status: str = "valid",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "feature_id": axis_id,
            "road_id": road_id,
            "road_ref": road_id or "SP-AMB",
            "road_name": axis_id,
            "concession_id": "test",
            "geometry_status": status,
            "source": "TEST",
        },
        "geometry": mapping(_line(points)),
    }


def _provider(tmp_path: Path, features: list[dict]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "roads.geojson"
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return LocalGeoJsonRoadGeometryProvider(path)


def _build(
    tmp_path: Path,
    features: list[dict],
    *,
    point: tuple[float, float] = (100, 5),
    config: RoadsideAoiConfig | None = None,
):
    provider = _provider(tmp_path, features)
    longitude, latitude = REVERSE.transform(ORIGIN_X + point[0], ORIGIN_Y + point[1])
    resolution = resolve_canonical_road_section(
        provider,
        longitude=longitude,
        latitude=latitude,
        segment_length_m=300,
    )
    assert resolution.status == "road_section_resolved"
    return build_roadside_aoi(resolution, provider, config=config), resolution


def _metric(geometry: dict):
    return transform(FORWARD.transform, shape(geometry))


def test_straight_section_builds_two_valid_sides_and_both(tmp_path: Path) -> None:
    result, _ = _build(tmp_path, [_feature("main", [(0, 0), (500, 0)])])

    assert result.status == "roadside_ready"
    assert result.side_a_geometry is not None
    assert result.side_b_geometry is not None
    assert result.analyzed_geometry is not None
    side_a = _metric(result.side_a_geometry)
    side_b = _metric(result.side_b_geometry)
    both = _metric(result.analyzed_geometry)
    assert side_a.is_valid and side_b.is_valid and both.is_valid
    assert side_a.intersection(side_b).area == pytest.approx(0.0, abs=1e-5)
    assert both.area == pytest.approx(side_a.area + side_b.area, rel=1e-6)
    assert result.side_a_effective_width_m == pytest.approx(40.0, abs=0.1)
    assert result.side_b_effective_width_m == pytest.approx(40.0, abs=0.1)
    assert result.area_m2 == pytest.approx(24_000.0, rel=0.01)


def test_roadway_exclusion_removes_central_road_region(tmp_path: Path) -> None:
    result, resolution = _build(tmp_path, [_feature("main", [(0, 0), (500, 0)])])
    assert result.analyzed_geometry is not None
    analyzed = _metric(result.analyzed_geometry)
    centerline = _metric(resolution.canonical_segment_geometry)
    excluded_roadway = centerline.buffer(24.99, cap_style=2)
    assert analyzed.intersection(excluded_roadway).area == pytest.approx(0.0, abs=1e-4)
    assert result.roadway_exclusion_geometry is not None
    assert result.roadway_exclusion_overlap_m2 == pytest.approx(0.0, abs=1e-6)
    assert result.minimum_centerline_distance_m == pytest.approx(25.0, abs=0.1)


def test_diagonal_section_has_no_endpoint_overlay_slivers(tmp_path: Path) -> None:
    result, resolution = _build(
        tmp_path,
        [_feature("diagonal", [(0, 0), (500, 460)])],
        point=(100, 92),
    )

    analyzed = _metric(result.analyzed_geometry)
    centerline = _metric(resolution.canonical_segment_geometry)
    exclusion = _metric(result.roadway_exclusion_geometry)

    assert result.status == "roadside_ready"
    assert analyzed.distance(centerline) == pytest.approx(25.0, abs=1e-4)
    assert analyzed.intersection(exclusion).area == pytest.approx(0.0, abs=1e-6)
    assert not analyzed.intersects(centerline.boundary)


def test_curved_section_produces_valid_roadside_geometry(tmp_path: Path) -> None:
    result, _ = _build(
        tmp_path,
        [_feature("curve", [(0, 0), (80, 0), (150, 40), (260, 80), (400, 80)])],
    )
    assert result.status == "roadside_ready"
    assert result.analyzed_geometry is not None
    assert shape(result.analyzed_geometry).is_valid


def test_short_section_can_pass_and_very_short_section_is_rejected(tmp_path: Path) -> None:
    passing, _ = _build(
        tmp_path / "passing",
        [_feature("short-pass", [(0, 0), (100, 0)])],
        point=(90, 2),
    )
    rejected, _ = _build(
        tmp_path / "rejected",
        [_feature("short-fail", [(0, 0), (60, 0)])],
        point=(50, 2),
    )
    assert passing.status == "roadside_ready"
    assert passing.road is not None and passing.road["short_section"] is True
    assert rejected.status == "roadside_too_small"
    assert rejected.area_m2 == pytest.approx(4_800.0, rel=0.01)


def test_width_gate_rejects_narrow_lateral_configuration(tmp_path: Path) -> None:
    result, _ = _build(
        tmp_path,
        [_feature("main", [(0, 0), (500, 0)])],
        config=RoadsideAoiConfig(
            roadway_exclusion_m=15,
            lateral_width_m=15,
            min_area_m2=5_000,
            min_width_m=20,
        ),
    )
    assert result.status == "roadside_too_small"
    assert result.side_a_effective_width_m == pytest.approx(15.0, abs=0.1)


def test_reliable_crossing_axis_is_excluded(tmp_path: Path) -> None:
    result, _ = _build(
        tmp_path,
        [
            _feature("main", [(0, 0), (500, 0)], road_id="SP-001"),
            _feature("crossing", [(150, -100), (150, 100)], road_id="SP-002"),
        ],
        point=(25, 2),
    )
    assert result.status == "roadside_ready"
    assert result.additional_axes_excluded == ("crossing",)
    analyzed = _metric(result.analyzed_geometry)
    crossing = LineString(
        [(ORIGIN_X + 150, ORIGIN_Y - 100), (ORIGIN_X + 150, ORIGIN_Y + 100)]
    )
    assert analyzed.intersection(crossing.buffer(14.99)).area == pytest.approx(0.0, abs=1e-4)


def test_parallel_axis_is_excluded_then_width_gate_fails(tmp_path: Path) -> None:
    result, _ = _build(
        tmp_path,
        [
            _feature("main", [(0, 0), (500, 0)], road_id="SP-001"),
            _feature("parallel", [(0, 25), (500, 25)], road_id="SP-002"),
        ],
        point=(100, -2),
    )
    assert result.status == "roadside_too_small"
    assert result.additional_axes_excluded == ("parallel",)


def test_unreliable_additional_axis_rejects_geometry(tmp_path: Path) -> None:
    result, _ = _build(
        tmp_path,
        [
            _feature("main", [(0, 0), (500, 0)]),
            _feature(
                "unknown-crossing",
                [(150, -100), (150, 100)],
                road_id=None,
                status="AMBIGUOUS_SUBSEGMENT_MATCH",
            ),
        ],
        point=(25, 2),
    )
    assert result.status == "roadside_geometry_invalid"
    assert result.reason == "additional_road_axis_unreliable"


def test_profile_key_changes_with_geometry_parameters(tmp_path: Path) -> None:
    features = [_feature("main", [(0, 0), (500, 0)])]
    default, _ = _build(tmp_path / "default", features)
    wider, _ = _build(
        tmp_path / "wider",
        features,
        config=RoadsideAoiConfig(
            roadway_exclusion_m=15,
            lateral_width_m=35,
            min_area_m2=5_000,
            min_width_m=20,
        ),
    )
    larger_exclusion, _ = _build(
        tmp_path / "larger-exclusion",
        features,
        config=RoadsideAoiConfig(
            roadway_exclusion_m=20,
            lateral_width_m=30,
            min_area_m2=5_000,
            min_width_m=20,
        ),
    )
    assert default.spatial_key != wider.spatial_key
    assert default.spatial_key != larger_exclusion.spatial_key
    assert ":side:both:" in default.spatial_key
