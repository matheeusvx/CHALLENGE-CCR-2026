"""Testes unitarios das estatisticas SCL, sem acesso a COG remoto."""

import numpy as np
import pytest

from src.satellite_monitoring.raster_processing import calculate_scl_class_percentages


def test_scl_percentages_include_masked_pixels_as_nodata() -> None:
    scl = np.ma.array(
        [[4, 5], [8, 0]],
        mask=[[False, False], [False, True]],
    )
    percentages = calculate_scl_class_percentages(
        scl,
        np.ones((2, 2), dtype=bool),
    )
    assert percentages["vegetation"] == pytest.approx(25.0)
    assert percentages["non_vegetated"] == pytest.approx(25.0)
    assert percentages["cloud_medium_probability"] == pytest.approx(25.0)
    assert percentages["nodata"] == pytest.approx(25.0)
    assert sum(percentages.values()) == pytest.approx(100.0)


def test_scl_percentages_respect_the_aoi_mask() -> None:
    scl = np.ma.array([[4, 9], [6, 11]])
    inside_aoi = np.array([[True, False], [True, False]])
    percentages = calculate_scl_class_percentages(scl, inside_aoi)
    assert percentages["vegetation"] == pytest.approx(50.0)
    assert percentages["water"] == pytest.approx(50.0)
    assert percentages["cloud_high_probability"] == 0.0
    assert percentages["snow_or_ice"] == 0.0
