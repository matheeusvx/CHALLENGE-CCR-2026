"""Criacao, leitura e validacao de areas de interesse em EPSG:4326."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import orient, unary_union
from shapely.validation import explain_validity

if TYPE_CHECKING:
    from .config import MonitoringConfig

ALLOWED_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}


@dataclass(frozen=True)
class ResolvedAOI:
    """Geometria efetiva, documento de saida e metadados da AOI."""

    geometry: dict[str, Any]
    output_geojson: dict[str, Any]
    metadata: dict[str, Any]


def validate_location(latitude: float, longitude: float, radius_meters: float) -> None:
    """Valida coordenadas WGS84 e um raio expresso em metros."""
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude deve estar entre -90 e 90 graus.")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude deve estar entre -180 e 180 graus.")
    if radius_meters <= 0:
        raise ValueError("O raio deve ser maior que zero.")


def create_aoi_geojson(
    latitude: float,
    longitude: float,
    radius_meters: float,
    vertices: int = 72,
) -> dict[str, Any]:
    """Cria um buffer geodesico circular e retorna GeoJSON em EPSG:4326."""
    validate_location(latitude, longitude, radius_meters)
    if vertices < 16:
        raise ValueError("A area de interesse deve ter pelo menos 16 vertices.")

    geod = Geod(ellps="WGS84")
    coordinates: list[tuple[float, float]] = []
    for azimuth in (index * 360.0 / vertices for index in range(vertices)):
        point_longitude, point_latitude, _ = geod.fwd(
            longitude,
            latitude,
            azimuth,
            radius_meters,
        )
        coordinates.append((point_longitude, point_latitude))

    coordinates.append(coordinates[0])
    polygon = validate_polygon_geometry(Polygon(coordinates))
    return mapping(polygon)


def _validate_declared_crs(document: dict[str, Any]) -> None:
    crs = document.get("crs")
    if crs is None:
        return

    try:
        crs_name = str(crs["properties"]["name"]).strip().upper()
    except (KeyError, TypeError) as exc:
        raise ValueError("Declaracao CRS invalida; informe EPSG:4326 ou remova o campo crs.") from exc

    allowed_names = {
        "EPSG:4326",
        "URN:OGC:DEF:CRS:EPSG::4326",
        "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
        "OGC:CRS84",
        "CRS84",
        "HTTP://WWW.OPENGIS.NET/DEF/CRS/EPSG/0/4326",
        "HTTP://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
    }
    if crs_name not in allowed_names:
        raise ValueError(
            f"CRS nao suportado: {crs_name}. O arquivo deve estar em EPSG:4326."
        )


def load_geojson_file(path: str | Path) -> dict[str, Any]:
    """Le um documento GeoJSON local e valida JSON e CRS declarado."""
    geometry_path = Path(path)
    if not geometry_path.exists():
        raise FileNotFoundError(f"Arquivo GeoJSON nao encontrado: {geometry_path}")
    if not geometry_path.is_file():
        raise ValueError(f"O caminho GeoJSON nao aponta para um arquivo: {geometry_path}")

    try:
        document = json.loads(geometry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalido no arquivo GeoJSON: {geometry_path}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"O arquivo GeoJSON deve usar codificacao UTF-8: {geometry_path}") from exc

    if not isinstance(document, dict):
        raise ValueError("A raiz do GeoJSON deve ser um objeto JSON.")
    _validate_declared_crs(document)
    return document


def _iter_polygons(geometry: BaseGeometry) -> Iterable[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms


def _validate_coordinate(longitude: Any, latitude: Any) -> None:
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        raise ValueError("Coordenadas GeoJSON devem ser numeros [longitude, latitude].")
    try:
        longitude_value = float(longitude)
        latitude_value = float(latitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("Coordenadas GeoJSON devem ser numeros [longitude, latitude].") from exc

    if not math.isfinite(longitude_value) or not math.isfinite(latitude_value):
        raise ValueError("Coordenadas GeoJSON devem ser numeros finitos.")
    if not -180 <= longitude_value <= 180:
        raise ValueError(f"Longitude fora dos limites [-180, 180]: {longitude_value}")
    if not -90 <= latitude_value <= 90:
        raise ValueError(f"Latitude fora dos limites [-90, 90]: {latitude_value}")


def validate_polygon_geometry(geometry: BaseGeometry) -> BaseGeometry:
    """Valida tipo, conteudo, coordenadas e topologia de Polygon/MultiPolygon."""
    if geometry.geom_type not in ALLOWED_GEOMETRY_TYPES:
        raise ValueError(
            f"Tipo de geometria nao suportado: {geometry.geom_type}. "
            "Use Polygon ou MultiPolygon."
        )
    if geometry.is_empty:
        raise ValueError("A geometria GeoJSON nao pode ser vazia.")

    for polygon in _iter_polygons(geometry):
        rings = [polygon.exterior, *polygon.interiors]
        for ring in rings:
            for coordinate in ring.coords:
                if len(coordinate) != 2:
                    raise ValueError("Use coordenadas bidimensionais [longitude, latitude].")
                _validate_coordinate(coordinate[0], coordinate[1])

    if not geometry.is_valid:
        raise ValueError(f"Geometria GeoJSON invalida: {explain_validity(geometry)}")
    if geometry.area <= 0:
        raise ValueError("A geometria GeoJSON deve possuir area maior que zero.")
    return geometry


def _shape_polygon(document: dict[str, Any]) -> BaseGeometry:
    geometry_type = document.get("type")
    if geometry_type not in ALLOWED_GEOMETRY_TYPES:
        raise ValueError(
            f"Tipo de geometria nao suportado: {geometry_type}. Use Polygon ou MultiPolygon."
        )
    try:
        geometry = shape(document)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Coordenadas GeoJSON invalidas ou incompletas.") from exc
    return validate_polygon_geometry(geometry)


def extract_polygon_geometry(document: dict[str, Any]) -> tuple[BaseGeometry, int]:
    """Extrai e une Polygon/MultiPolygon de Geometry, Feature ou FeatureCollection."""
    document_type = document.get("type")
    if document_type in ALLOWED_GEOMETRY_TYPES:
        return _shape_polygon(document), 1

    if document_type == "Feature":
        geometry_document = document.get("geometry")
        if not isinstance(geometry_document, dict):
            raise ValueError("Feature GeoJSON deve conter uma geometria Polygon ou MultiPolygon.")
        return _shape_polygon(geometry_document), 1

    if document_type == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list) or not features:
            raise ValueError("FeatureCollection GeoJSON nao pode ser vazia.")

        geometries: list[BaseGeometry] = []
        for index, feature in enumerate(features, start=1):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError(f"Item {index} da FeatureCollection nao e uma Feature valida.")
            geometry_document = feature.get("geometry")
            if not isinstance(geometry_document, dict):
                raise ValueError(f"Feature {index} nao possui uma geometria valida.")
            try:
                geometries.append(_shape_polygon(geometry_document))
            except ValueError as exc:
                raise ValueError(f"Geometria invalida na Feature {index}: {exc}") from exc

        union = validate_polygon_geometry(unary_union(geometries))
        return union, len(features)

    raise ValueError(
        f"Tipo GeoJSON nao suportado: {document_type}. "
        "Use Polygon, MultiPolygon, Feature ou FeatureCollection."
    )


def _geodesic_area_square_meters(geometry: BaseGeometry) -> float:
    geod = Geod(ellps="WGS84")
    area = 0.0
    for polygon in _iter_polygons(geometry):
        oriented = orient(polygon, sign=1.0)
        polygon_area, _ = geod.geometry_area_perimeter(oriented)
        area += abs(polygon_area)
    return float(area)


def calculate_geometry_metadata(
    geometry: BaseGeometry,
    *,
    source: str,
    feature_count: int,
    geometry_file: str | Path | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_meters: float | None = None,
) -> dict[str, Any]:
    """Calcula metadados espaciais, incluindo area geodesica em metros quadrados."""
    validated = validate_polygon_geometry(geometry)
    centroid = validated.centroid
    metadata: dict[str, Any] = {
        "source": source,
        "geometry_source": source,
        "geometry_type": validated.geom_type,
        "file": str(geometry_file) if geometry_file is not None else None,
        "bounding_box": [float(value) for value in validated.bounds],
        "centroid": {
            "longitude": float(centroid.x),
            "latitude": float(centroid.y),
        },
        "centroid_longitude": float(centroid.x),
        "centroid_latitude": float(centroid.y),
        "area_square_meters": _geodesic_area_square_meters(validated),
        "feature_count": feature_count,
        "valid": True,
    }
    if source == "circle":
        metadata.update(
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius_meters": radius_meters,
            }
        )
    return metadata


def resolve_aoi(config: MonitoringConfig) -> ResolvedAOI:
    """Resolve o modo configurado para a geometria unica usada em todo o pipeline."""
    if config.geometry_file is not None:
        original_document = load_geojson_file(config.geometry_file)
        geometry, feature_count = extract_polygon_geometry(original_document)
        geometry_mapping = mapping(geometry)
        metadata = calculate_geometry_metadata(
            geometry,
            source="geojson_file",
            feature_count=feature_count,
            geometry_file=config.geometry_file,
        )
        output_geojson = {
            "type": "Feature",
            "properties": {"geometry_source": "geojson_file"},
            "geometry": geometry_mapping,
            "source_geojson": original_document,
        }
        return ResolvedAOI(geometry_mapping, output_geojson, metadata)

    assert config.latitude is not None
    assert config.longitude is not None
    assert config.radius_meters is not None
    geometry_mapping = create_aoi_geojson(
        config.latitude,
        config.longitude,
        config.radius_meters,
    )
    geometry = validate_polygon_geometry(shape(geometry_mapping))
    metadata = calculate_geometry_metadata(
        geometry,
        source="circle",
        feature_count=1,
        latitude=config.latitude,
        longitude=config.longitude,
        radius_meters=config.radius_meters,
    )
    output_geojson = {
        "type": "Feature",
        "properties": {
            "geometry_source": "circle",
            "latitude": config.latitude,
            "longitude": config.longitude,
            "radius_meters": config.radius_meters,
        },
        "geometry": geometry_mapping,
    }
    return ResolvedAOI(geometry_mapping, output_geojson, metadata)
