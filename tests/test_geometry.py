"""Testes da configuracao e da area de interesse geodesica."""

from datetime import date

import pytest
from pyproj import Geod
from shapely.geometry import shape

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.geometry import create_aoi_geojson, validate_location


@pytest.mark.parametrize(
    ("latitude", "longitude", "radius"),
    [(91, 0, 100), (-91, 0, 100), (0, 181, 100), (0, -181, 100), (0, 0, 0)],
)
def test_rejects_invalid_location(latitude: float, longitude: float, radius: float) -> None:
    with pytest.raises(ValueError):
        validate_location(latitude, longitude, radius)


def test_creates_valid_geodesic_aoi_in_wgs84() -> None:
    latitude = -23.5505
    longitude = -46.6333
    radius = 100.0
    aoi = create_aoi_geojson(latitude, longitude, radius)

    polygon = shape(aoi)
    assert aoi["type"] == "Polygon"
    assert polygon.is_valid
    assert polygon.contains(shape({"type": "Point", "coordinates": [longitude, latitude]}))

    first_longitude, first_latitude = aoi["coordinates"][0][0]
    _, _, distance = Geod(ellps="WGS84").inv(
        longitude,
        latitude,
        first_longitude,
        first_latitude,
    )
    assert distance == pytest.approx(radius, abs=0.01)


def test_config_rejects_reversed_dates_and_invalid_limits() -> None:
    with pytest.raises(ValueError, match="data inicial"):
        MonitoringConfig(0, 0, 100, date(2026, 2, 1), date(2026, 1, 1))

    with pytest.raises(ValueError, match="nuvens"):
        MonitoringConfig(0, 0, 100, date(2026, 1, 1), date(2026, 2, 1), 101)

    with pytest.raises(ValueError, match="cenas"):
        MonitoringConfig(0, 0, 100, date(2026, 1, 1), date(2026, 2, 1), 20, 0)

    with pytest.raises(ValueError, match="pixels validos"):
        MonitoringConfig(
            0,
            0,
            100,
            date(2026, 1, 1),
            date(2026, 2, 1),
            min_valid_pixel_percentage=101,
        )

    with pytest.raises(ValueError, match="observacoes"):
        MonitoringConfig(
            0,
            0,
            100,
            date(2026, 1, 1),
            date(2026, 2, 1),
            min_observations=0,
        )
