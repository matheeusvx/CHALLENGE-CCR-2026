"""Testes do calculo e das estatisticas de NDVI."""

import numpy as np
import pytest

from src.satellite_monitoring.indices import (
    InsufficientValidPixelsError,
    analyze_ndvi,
    calculate_ndvi,
    summarize_ndvi,
)


def test_calculates_ndvi_and_excludes_zero_denominator() -> None:
    red = np.array([[1.0, 1.0, -1.0]])
    nir = np.array([[3.0, 1.0, 1.0]])

    ndvi, valid = calculate_ndvi(red, nir)

    assert ndvi[0, 0] == pytest.approx(0.5)
    assert ndvi[0, 1] == pytest.approx(0.0)
    assert np.isnan(ndvi[0, 2])
    assert valid.tolist() == [[True, True, False]]


def test_excludes_nodata_mask_and_uses_only_valid_pixels() -> None:
    red = np.ma.array([[1.0, 2.0], [3.0, 4.0]], mask=[[False, True], [False, False]])
    nir = np.ma.array([[3.0, 6.0], [3.0, 12.0]], mask=[[False, True], [False, False]])
    quality_mask = np.array([[True, True], [False, True]])

    _, statistics = analyze_ndvi(red, nir, quality_mask, total_pixel_count=4)

    assert statistics.valid_pixel_count == 2
    assert statistics.valid_pixel_percentage == pytest.approx(50.0)
    assert statistics.mean == pytest.approx(0.5)
    assert statistics.median == pytest.approx(0.5)
    assert statistics.minimum == pytest.approx(0.5)
    assert statistics.maximum == pytest.approx(0.5)
    assert statistics.std == pytest.approx(0.0)


def test_raises_when_there_are_not_enough_valid_pixels() -> None:
    ndvi = np.array([[np.nan]])
    valid = np.array([[False]])

    with pytest.raises(InsufficientValidPixelsError, match="insuficientes"):
        summarize_ndvi(ndvi, valid, total_pixel_count=1)
