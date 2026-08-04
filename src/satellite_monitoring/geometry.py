"""Criacao geodesica da area de interesse em EPSG:4326."""

from __future__ import annotations

from typing import Any

from pyproj import Geod
from shapely.geometry import Polygon, mapping


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
    """Cria um buffer geodesico circular e retorna GeoJSON em EPSG:4326.

    Cada vertice e calculado no elipsoide WGS84 a partir de uma distancia
    em metros. Assim, o raio nunca e aproximado diretamente em graus.
    """
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
    polygon = Polygon(coordinates)
    if not polygon.is_valid:
        raise ValueError("Nao foi possivel criar uma area de interesse valida.")

    return mapping(polygon)
