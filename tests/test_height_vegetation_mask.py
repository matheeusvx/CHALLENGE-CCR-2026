"""Testes isolados da mascara exclusiva do estimador de altura."""

from __future__ import annotations

import numpy as np
import pytest

from src.satellite_monitoring.features.vegetation_mask import (
    HeightMaskConfig,
    build_height_valid_mask,
    mixed_pixel_risk,
)


def _mask(
    scl: np.ndarray,
    *,
    red: np.ndarray | None = None,
    nir: np.ndarray | None = None,
    quality: np.ndarray | None = None,
    inside: np.ndarray | None = None,
    scl_valid: np.ndarray | None = None,
):
    shape = scl.shape
    return build_height_valid_mask(
        red if red is not None else np.full(shape, 0.1),
        nir if nir is not None else np.full(shape, 0.4),
        quality_valid_mask=quality
        if quality is not None
        else np.ones(shape, dtype=bool),
        inside_aoi_mask=inside
        if inside is not None
        else np.ones(shape, dtype=bool),
        scl_values=scl,
        scl_valid_mask=scl_valid,
    )


def test_clear_vegetation_pixel_is_accepted() -> None:
    result = _mask(np.asarray([[4]]))
    assert result.valid_mask.tolist() == [[True]]
    assert result.vegetation_fraction == 1.0


@pytest.mark.parametrize("scl_class", [5, 6])
def test_water_and_non_vegetated_scl_are_rejected(scl_class: int) -> None:
    result = _mask(np.asarray([[scl_class]]))
    assert result.valid_mask.tolist() == [[False]]
    assert result.height_valid_pixel_count == 0


def test_low_ndvi_and_invalid_or_nodata_pixels_are_rejected() -> None:
    result = _mask(
        np.asarray([[4, 4, 4]]),
        red=np.asarray([[0.2, 0.1, 0.1]]),
        nir=np.asarray([[0.21, 0.4, 0.4]]),
        quality=np.asarray([[True, False, True]]),
        scl_valid=np.asarray([[True, True, False]]),
    )
    assert result.valid_mask.tolist() == [[False, False, False]]


def test_vegetation_fraction_uses_all_spatially_eligible_aoi_pixels() -> None:
    result = _mask(np.asarray([[4, 4], [5, 6]]))
    assert result.height_valid_pixel_count == 2
    assert result.height_total_pixel_count == 4
    assert result.vegetation_fraction == pytest.approx(0.5)


def test_zero_spatial_pixels_is_safe_and_high_risk() -> None:
    result = _mask(
        np.asarray([[4]]),
        inside=np.asarray([[False]]),
    )
    assert result.vegetation_fraction == 0.0
    assert result.height_total_pixel_count == 0
    assert result.mixed_pixel_risk == "high"
    assert result.purity_gate_passed is False


@pytest.mark.parametrize(
    ("fraction", "valid", "total", "expected"),
    [
        (0.2, 20, 100, "high"),
        (0.5, 20, 40, "medium"),
        (0.8, 80, 100, "low"),
        (1.0, 2, 2, "high"),
    ],
)
def test_mixed_pixel_risk_is_deterministic(
    fraction: float, valid: int, total: int, expected: str
) -> None:
    assert mixed_pixel_risk(fraction, valid, total) == expected
    assert mixed_pixel_risk(fraction, valid, total) == expected


def test_internal_ndvi_threshold_is_configurable() -> None:
    red = np.asarray([[0.2]])
    nir = np.asarray([[0.3]])  # NDVI = 0.2
    common = {
        "quality_valid_mask": np.asarray([[True]]),
        "inside_aoi_mask": np.asarray([[True]]),
        "scl_values": np.asarray([[4]]),
    }
    accepted = build_height_valid_mask(
        red, nir, config=HeightMaskConfig(min_ndvi=0.15), **common
    )
    rejected = build_height_valid_mask(
        red, nir, config=HeightMaskConfig(min_ndvi=0.25), **common
    )
    assert accepted.height_valid_pixel_count == 1
    assert rejected.height_valid_pixel_count == 0
