"""Testes dos modos circular e GeoJSON da area de interesse."""

import json
import math
from datetime import date
from pathlib import Path

import pytest
from pyproj import Geod
from shapely.geometry import shape

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.geometry import (
    calculate_geometry_metadata,
    create_aoi_geojson,
    extract_polygon_geometry,
    load_geojson_file,
    resolve_aoi,
    validate_location,
)

START_DATE = date(2026, 5, 1)
END_DATE = date(2026, 8, 4)
POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-47.0000, -23.0000],
            [-46.9990, -23.0000],
            [-46.9990, -22.9990],
            [-47.0000, -22.9990],
            [-47.0000, -23.0000],
        ]
    ],
}
SECOND_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-46.9980, -23.0000],
            [-46.9970, -23.0000],
            [-46.9970, -22.9990],
            [-46.9980, -22.9990],
            [-46.9980, -23.0000],
        ]
    ],
}


def _write_geojson(tmp_path: Path, document: object, name: str = "aoi.geojson") -> Path:
    path = tmp_path / name
    if isinstance(document, str):
        path.write_text(document, encoding="utf-8")
    else:
        path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _file_config(path: Path) -> MonitoringConfig:
    return MonitoringConfig(
        start_date=START_DATE,
        end_date=END_DATE,
        geometry_file=path,
    )


@pytest.mark.parametrize(
    ("latitude", "longitude", "radius"),
    [(91, 0, 100), (-91, 0, 100), (0, 181, 100), (0, -181, 100), (0, 0, 0)],
)
def test_rejects_invalid_location(latitude: float, longitude: float, radius: float) -> None:
    with pytest.raises(ValueError):
        validate_location(latitude, longitude, radius)


def test_preserves_existing_circle_mode_and_calculates_metadata() -> None:
    config = MonitoringConfig(-23.5505, -46.6333, 100, START_DATE, END_DATE)
    resolved = resolve_aoi(config)
    polygon = shape(resolved.geometry)

    assert resolved.geometry["type"] == "Polygon"
    assert polygon.is_valid
    assert polygon.contains(shape({"type": "Point", "coordinates": [-46.6333, -23.5505]}))
    assert resolved.metadata["source"] == "circle"
    assert resolved.metadata["radius_meters"] == 100
    assert resolved.metadata["feature_count"] == 1
    assert resolved.metadata["area_square_meters"] == pytest.approx(math.pi * 100**2, rel=0.01)

    first_longitude, first_latitude = resolved.geometry["coordinates"][0][0]
    _, _, distance = Geod(ellps="WGS84").inv(
        -46.6333,
        -23.5505,
        first_longitude,
        first_latitude,
    )
    assert distance == pytest.approx(100, abs=0.01)


def test_accepts_polygon_geometry_file(tmp_path: Path) -> None:
    path = _write_geojson(tmp_path, POLYGON)
    resolved = resolve_aoi(_file_config(path))

    assert resolved.geometry["type"] == "Polygon"
    assert resolved.metadata["source"] == "geojson_file"
    assert resolved.metadata["file"] == str(path)
    assert resolved.metadata["valid"] is True
    assert resolved.output_geojson["source_geojson"] == POLYGON


def test_accepts_valid_multipolygon(tmp_path: Path) -> None:
    document = {
        "type": "MultiPolygon",
        "coordinates": [POLYGON["coordinates"], SECOND_POLYGON["coordinates"]],
    }
    resolved = resolve_aoi(_file_config(_write_geojson(tmp_path, document)))
    assert resolved.geometry["type"] == "MultiPolygon"
    assert resolved.metadata["feature_count"] == 1


def test_accepts_feature_and_preserves_properties(tmp_path: Path) -> None:
    document = {
        "type": "Feature",
        "properties": {"name": "faixa lateral"},
        "geometry": POLYGON,
    }
    resolved = resolve_aoi(_file_config(_write_geojson(tmp_path, document)))
    assert resolved.geometry["type"] == "Polygon"
    assert resolved.output_geojson["source_geojson"]["properties"]["name"] == "faixa lateral"


def test_feature_collection_unites_multiple_polygons(tmp_path: Path) -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": 1}, "geometry": POLYGON},
            {"type": "Feature", "properties": {"id": 2}, "geometry": SECOND_POLYGON},
        ],
    }
    resolved = resolve_aoi(_file_config(_write_geojson(tmp_path, document)))

    assert resolved.geometry["type"] == "MultiPolygon"
    assert resolved.metadata["feature_count"] == 2
    assert len(resolved.output_geojson["source_geojson"]["features"]) == 2


def test_feature_collection_rejects_any_non_polygon_feature() -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": POLYGON},
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-47.0, -23.0], [-46.9, -22.9]],
                },
            },
        ],
    }

    with pytest.raises(ValueError, match="Feature 2"):
        extract_polygon_geometry(document)


def test_adjacent_polygons_are_united_into_one_polygon() -> None:
    adjacent = {
        "type": "Polygon",
        "coordinates": [
            [
                [-46.9990, -23.0000],
                [-46.9980, -23.0000],
                [-46.9980, -22.9990],
                [-46.9990, -22.9990],
                [-46.9990, -23.0000],
            ]
        ],
    }
    document = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": POLYGON},
            {"type": "Feature", "properties": {}, "geometry": adjacent},
        ],
    }
    geometry, feature_count = extract_polygon_geometry(document)
    assert geometry.geom_type == "Polygon"
    assert feature_count == 2


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="nao encontrado"):
        load_geojson_file(tmp_path / "missing.geojson")


def test_rejects_invalid_json(tmp_path: Path) -> None:
    path = _write_geojson(tmp_path, "{invalid-json")
    with pytest.raises(ValueError, match="JSON invalido"):
        load_geojson_file(path)


def test_rejects_empty_feature_collection() -> None:
    with pytest.raises(ValueError, match="nao pode ser vazia"):
        extract_polygon_geometry({"type": "FeatureCollection", "features": []})


@pytest.mark.parametrize("geometry_type", ["Point", "LineString", "MultiLineString", "GeometryCollection"])
def test_rejects_non_polygon_geometry(geometry_type: str) -> None:
    coordinates_by_type = {
        "Point": [-47.0, -23.0],
        "LineString": [[-47.0, -23.0], [-46.9, -22.9]],
        "MultiLineString": [[[-47.0, -23.0], [-46.9, -22.9]]],
        "GeometryCollection": None,
    }
    document = {"type": geometry_type}
    if geometry_type == "GeometryCollection":
        document["geometries"] = []
    else:
        document["coordinates"] = coordinates_by_type[geometry_type]

    with pytest.raises(ValueError, match="nao suportado"):
        extract_polygon_geometry(document)


def test_rejects_self_intersecting_polygon() -> None:
    invalid = {
        "type": "Polygon",
        "coordinates": [[[-47, -23], [-46.9, -22.9], [-47, -22.9], [-46.9, -23], [-47, -23]]],
    }
    with pytest.raises(ValueError, match="invalida"):
        extract_polygon_geometry(invalid)


@pytest.mark.parametrize(
    "coordinate",
    [[181.0, 0.0], [-181.0, 0.0], [0.0, 91.0], [0.0, -91.0]],
)
def test_rejects_coordinates_outside_wgs84_limits(coordinate: list[float]) -> None:
    invalid = {
        "type": "Polygon",
        "coordinates": [[[0, 0], coordinate, [1, 1], [0, 0]]],
    }
    with pytest.raises(ValueError, match="fora dos limites"):
        extract_polygon_geometry(invalid)


def test_rejects_explicit_non_wgs84_crs(tmp_path: Path) -> None:
    document = {
        **POLYGON,
        "crs": {"type": "name", "properties": {"name": "EPSG:31983"}},
    }
    path = _write_geojson(tmp_path, document)
    with pytest.raises(ValueError, match="EPSG:4326"):
        load_geojson_file(path)


def test_calculates_polygon_area_in_square_meters() -> None:
    geometry, _ = extract_polygon_geometry(
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]]],
        }
    )
    metadata = calculate_geometry_metadata(
        geometry,
        source="geojson_file",
        feature_count=1,
    )
    assert 12_000 < metadata["area_square_meters"] < 13_000
    assert metadata["bounding_box"] == [0.0, 0.0, 0.001, 0.001]


def test_rejects_geometry_file_combined_with_circle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nunca os dois"):
        MonitoringConfig(
            -23.0,
            -47.0,
            100,
            START_DATE,
            END_DATE,
            geometry_file=tmp_path / "aoi.geojson",
        )


def test_rejects_complete_absence_of_geometry() -> None:
    with pytest.raises(ValueError, match="Informe geometry-file"):
        MonitoringConfig(start_date=START_DATE, end_date=END_DATE)


def test_rejects_incomplete_circle_mode() -> None:
    with pytest.raises(ValueError, match="exige latitude"):
        MonitoringConfig(latitude=-23.0, start_date=START_DATE, end_date=END_DATE)


def test_config_rejects_reversed_dates_and_invalid_limits() -> None:
    with pytest.raises(ValueError, match="data inicial"):
        MonitoringConfig(0, 0, 100, date(2026, 2, 1), date(2026, 1, 1))

    with pytest.raises(ValueError, match="nuvens"):
        MonitoringConfig(0, 0, 100, START_DATE, END_DATE, 101)

    with pytest.raises(ValueError, match="cenas"):
        MonitoringConfig(0, 0, 100, START_DATE, END_DATE, 20, 0)

    with pytest.raises(ValueError, match="pixels validos"):
        MonitoringConfig(
            0,
            0,
            100,
            START_DATE,
            END_DATE,
            min_valid_pixel_percentage=101,
        )

    with pytest.raises(ValueError, match="observacoes"):
        MonitoringConfig(0, 0, 100, START_DATE, END_DATE, min_observations=0)


def test_create_aoi_geojson_remains_available() -> None:
    assert create_aoi_geojson(-23.0, -47.0, 100)["type"] == "Polygon"
