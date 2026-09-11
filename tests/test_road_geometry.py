"""Offline tests for the AUTO-03B road provider, snap, and canonical sections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from src.satellite_monitoring.road_geometry import (
    LocalGeoJsonRoadGeometryProvider,
    RoadGeometryUnavailableError,
    resolve_canonical_road_section,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_ROADS = ROOT / "data" / "roads" / "processed" / "motiva-sp-roads-state.geojson"
FORWARD = Transformer.from_crs("EPSG:4326", "EPSG:32723", always_xy=True)
REVERSE = Transformer.from_crs("EPSG:32723", "EPSG:4326", always_xy=True)
ORIGIN_X, ORIGIN_Y = FORWARD.transform(-47.0, -23.0)


def _wgs_line(points: list[tuple[float, float]]) -> LineString:
    metric = LineString([(ORIGIN_X + x, ORIGIN_Y + y) for x, y in points])
    return transform(REVERSE.transform, metric)


def _wgs_point(x: float, y: float) -> tuple[float, float]:
    return REVERSE.transform(ORIGIN_X + x, ORIGIN_Y + y)


def _feature(
    axis_id: str,
    points: list[tuple[float, float]],
    *,
    road_id: str | None = "SP-001",
    geometry_status: str = "valid",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "feature_id": axis_id,
            "road_id": road_id,
            "road_ref": road_id or "SP-AMB",
            "road_name": f"Road {road_id or 'ambiguous'}",
            "concession_id": "test",
            "geometry_status": geometry_status,
            "source": "TEST",
        },
        "geometry": mapping(_wgs_line(points)),
    }


def _dataset(path: Path, features: list[dict]) -> Path:
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return path


def _resolve(
    provider: LocalGeoJsonRoadGeometryProvider,
    x: float,
    y: float,
    **changes,
):
    longitude, latitude = _wgs_point(x, y)
    values = {
        "max_distance_m": 50.0,
        "ambiguity_tolerance_m": 10.0,
        "segment_length_m": 200.0,
    }
    values.update(changes)
    return resolve_canonical_road_section(
        provider, longitude=longitude, latitude=latitude, **values
    )


def test_real_dataset_has_expected_eligible_and_ambiguous_features() -> None:
    provider = LocalGeoJsonRoadGeometryProvider(REAL_ROADS)

    assert provider.dataset_identity.feature_count == 42
    assert provider.dataset_identity.valid_feature_count == 33
    assert sum(axis.eligible for axis in provider.axes) == 33
    assert sum(not axis.eligible for axis in provider.axes) == 9
    assert all(
        axis.geometry_status == "AMBIGUOUS_SUBSEGMENT_MATCH"
        for axis in provider.axes
        if not axis.eligible
    )


def test_missing_and_corrupt_datasets_are_controlled(tmp_path: Path) -> None:
    missing = LocalGeoJsonRoadGeometryProvider(tmp_path / "missing.geojson")
    corrupt_path = tmp_path / "corrupt.geojson"
    corrupt_path.write_text("{broken", encoding="utf-8")
    corrupt = LocalGeoJsonRoadGeometryProvider(corrupt_path)

    with pytest.raises(RoadGeometryUnavailableError):
        _ = missing.dataset_identity
    with pytest.raises(RoadGeometryUnavailableError):
        _ = corrupt.dataset_identity
    assert _resolve(missing, 0, 0).status == "road_geometry_unavailable"
    assert _resolve(corrupt, 0, 0).status == "road_geometry_unavailable"


def test_duplicate_axis_identity_makes_dataset_unavailable(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(
            tmp_path / "duplicate.geojson",
            [
                _feature("same-axis", [(0, 0), (1000, 0)]),
                _feature("same-axis", [(0, 30), (1000, 30)]),
            ],
        )
    )

    assert _resolve(provider, 250, 2).status == "road_geometry_unavailable"


def test_fingerprint_is_deterministic_and_index_is_reused(tmp_path: Path) -> None:
    path = _dataset(tmp_path / "roads.geojson", [_feature("axis-a", [(0, 0), (1000, 0)])])
    first = LocalGeoJsonRoadGeometryProvider(path)
    second = LocalGeoJsonRoadGeometryProvider(path)

    assert first.dataset_identity.fingerprint == second.dataset_identity.fingerprint
    _resolve(first, 250, 5)
    _resolve(first, 270, 5)
    assert first.index_build_count == 1


def test_unique_nearby_axis_snaps_with_metric_distance_and_audit(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(tmp_path / "roads.geojson", [_feature("axis-a", [(0, 0), (1000, 0)])])
    )
    result = _resolve(provider, 250, 8)

    assert result.status == "road_section_resolved"
    assert result.road is not None
    assert result.road["axis_id"] == "axis-a"
    assert result.road["snap_distance_m"] == pytest.approx(8.0, abs=0.05)
    assert result.road["orientation_basis"] == "unavailable"
    assert result.road["axis_model"] == "unknown"


def test_snap_boundary_at_50m_is_inclusive_and_outside_is_not_found(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(tmp_path / "roads.geojson", [_feature("axis-a", [(0, 0), (1000, 0)])])
    )

    assert _resolve(provider, 250, 50).status == "road_section_resolved"
    assert _resolve(provider, 250, 50.2).status == "road_not_found"


def test_two_plausible_axes_fail_closed_as_ambiguous(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(
            tmp_path / "roads.geojson",
            [
                _feature("axis-a", [(0, 0), (1000, 0)], road_id="SP-001"),
                _feature("axis-b", [(0, 10), (1000, 10)], road_id="SP-002"),
            ],
        )
    )

    result = _resolve(provider, 250, 4)
    assert result.status == "road_ambiguous"
    assert len(result.candidate_audit) == 2


def test_nearest_axis_without_reliable_identity_is_never_selected(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(
            tmp_path / "roads.geojson",
            [
                _feature(
                    "axis-ambiguous",
                    [(0, 0), (1000, 0)],
                    road_id=None,
                    geometry_status="AMBIGUOUS_SUBSEGMENT_MATCH",
                ),
                _feature("axis-valid", [(0, 30), (1000, 30)]),
            ],
        )
    )

    result = _resolve(provider, 250, 2)
    assert result.status == "road_geometry_unreliable"
    assert result.road is None


def test_micro_pan_keeps_section_and_longitudinal_pan_changes_it(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(tmp_path / "roads.geojson", [_feature("axis-a", [(0, 0), (1000, 0)])])
    )

    first = _resolve(provider, 250, 5)
    micro_pan = _resolve(provider, 270, 5)
    next_section = _resolve(provider, 410, 5)
    assert first.road_section_key == micro_pan.road_section_key
    assert first.road is not None and first.road["section_index"] == 1
    assert next_section.road is not None and next_section.road["section_index"] == 2
    assert next_section.road_section_key != first.road_section_key


def test_section_boundary_is_deterministic_and_segment_is_curved(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(
            tmp_path / "roads.geojson",
            [_feature("axis-curve", [(0, 0), (200, 0), (400, 100), (700, 100)])],
        )
    )

    boundary = _resolve(provider, 200, 0)
    repeated = _resolve(provider, 200, 0)
    assert boundary.road_section_key == repeated.road_section_key
    assert boundary.road is not None and boundary.road["section_index"] == 1
    assert boundary.canonical_segment_geometry is not None
    assert len(boundary.canonical_segment_geometry["coordinates"]) >= 2


def test_short_final_fragment_is_reported(tmp_path: Path) -> None:
    provider = LocalGeoJsonRoadGeometryProvider(
        _dataset(tmp_path / "roads.geojson", [_feature("axis-short", [(0, 0), (250, 0)])])
    )

    result = _resolve(provider, 240, 2)
    assert result.road is not None
    assert result.road["section_index"] == 1
    assert result.road["short_section"] is True
    assert result.road["section_length_m"] == pytest.approx(50.0, abs=0.1)


def test_dataset_and_segment_profile_change_road_identity(tmp_path: Path) -> None:
    first_path = _dataset(
        tmp_path / "roads-a.geojson", [_feature("axis-a", [(0, 0), (1000, 0)])]
    )
    second_feature = _feature("axis-a", [(0, 0), (1000, 0)])
    second_feature["properties"]["road_name"] = "Changed metadata"
    second_path = _dataset(tmp_path / "roads-b.geojson", [second_feature])

    first = _resolve(LocalGeoJsonRoadGeometryProvider(first_path), 250, 5)
    changed_dataset = _resolve(LocalGeoJsonRoadGeometryProvider(second_path), 250, 5)
    changed_length = _resolve(
        LocalGeoJsonRoadGeometryProvider(first_path),
        250,
        5,
        segment_length_m=100.0,
    )
    assert first.road_section_key != changed_dataset.road_section_key
    assert first.road_section_key != changed_length.road_section_key
    assert first.road_section_key.startswith("roadside:v1:")
