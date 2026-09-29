"""Calculo e estatisticas do indice de vegetacao NDVI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


class InsufficientValidPixelsError(ValueError):
    """Indica que nao restaram pixels suficientes para uma estatistica."""


@dataclass(frozen=True)
class NDVIStatistics:
    mean: float
    median: float
    std: float
    minimum: float
    maximum: float
    valid_pixel_count: int
    valid_pixel_percentage: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_ndvi(
    red: np.ndarray,
    nir: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Calcula NDVI somente onde as duas bandas e o denominador sao validos."""
    if red.shape != nir.shape:
        raise ValueError("As bandas vermelha e NIR devem ter as mesmas dimensoes.")

    red_mask = np.ma.getmaskarray(red)
    nir_mask = np.ma.getmaskarray(nir)
    red_values = np.asarray(np.ma.getdata(red), dtype=np.float64)
    nir_values = np.asarray(np.ma.getdata(nir), dtype=np.float64)
    denominator = nir_values + red_values

    valid = ~red_mask & ~nir_mask
    valid &= np.isfinite(red_values) & np.isfinite(nir_values)
    valid &= np.isfinite(denominator) & (denominator != 0)
    if valid_mask is not None:
        if valid_mask.shape != red.shape:
            raise ValueError("A mascara de validade deve ter as mesmas dimensoes das bandas.")
        valid &= np.asarray(valid_mask, dtype=bool)

    ndvi = np.full(red.shape, np.nan, dtype=np.float64)
    np.divide(
        nir_values - red_values,
        denominator,
        out=ndvi,
        where=valid,
    )
    valid &= np.isfinite(ndvi)
    return ndvi, valid


def summarize_ndvi(
    ndvi: np.ndarray,
    valid_mask: np.ndarray,
    total_pixel_count: int,
    min_valid_pixels: int = 1,
) -> NDVIStatistics:
    """Resume o NDVI usando exclusivamente os pixels marcados como validos."""
    if ndvi.shape != valid_mask.shape:
        raise ValueError("NDVI e mascara de validade devem ter as mesmas dimensoes.")
    if total_pixel_count <= 0:
        raise ValueError("A quantidade total de pixels deve ser maior que zero.")
    if min_valid_pixels <= 0:
        raise ValueError("O minimo de pixels validos deve ser maior que zero.")

    values = np.asarray(ndvi, dtype=np.float64)[np.asarray(valid_mask, dtype=bool)]
    values = values[np.isfinite(values)]
    if values.size < min_valid_pixels:
        raise InsufficientValidPixelsError(
            f"Pixels validos insuficientes: {values.size}; minimo exigido: {min_valid_pixels}."
        )

    return NDVIStatistics(
        mean=float(np.mean(values)),
        median=float(np.median(values)),
        std=float(np.std(values)),
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        valid_pixel_count=int(values.size),
        valid_pixel_percentage=float(values.size / total_pixel_count * 100.0),
    )


def analyze_ndvi(
    red: np.ndarray,
    nir: np.ndarray,
    valid_mask: np.ndarray,
    total_pixel_count: int,
    min_valid_pixels: int = 1,
) -> tuple[np.ndarray, NDVIStatistics]:
    """Calcula a matriz NDVI e suas estatisticas para uma cena."""
    ndvi, ndvi_valid = calculate_ndvi(red, nir, valid_mask)
    statistics = summarize_ndvi(
        ndvi,
        ndvi_valid,
        total_pixel_count=total_pixel_count,
        min_valid_pixels=min_valid_pixels,
    )
    return ndvi, statistics
