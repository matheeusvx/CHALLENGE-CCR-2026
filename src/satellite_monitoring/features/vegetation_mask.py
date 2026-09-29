"""Mascara conservadora exclusiva do estimador experimental de altura."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from ..indices import calculate_ndvi
from ..raster_processing import (
    RasterProcessingError,
    RasterSceneData,
    SCL_CLASS_NAMES,
    physical_reflectance_arrays,
)

# O quality mask operacional aceita SCL 2, 4, 5, 6 e 7. Para altura, somente
# a classe explicitamente identificada como vegetacao e elegivel.
HEIGHT_MASK_ALLOWED_SCL_CLASSES = (4,)
HEIGHT_MASK_REJECTED_SCL_CLASSES = (0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11)
DEFAULT_HEIGHT_MIN_NDVI = 0.15
DEFAULT_MIN_VEGETATION_FRACTION = 0.25
DEFAULT_MIN_HEIGHT_VALID_PIXELS = 3


@dataclass(frozen=True)
class HeightMaskConfig:
    min_ndvi: float = DEFAULT_HEIGHT_MIN_NDVI
    min_vegetation_fraction: float = DEFAULT_MIN_VEGETATION_FRACTION
    min_valid_pixels: int = DEFAULT_MIN_HEIGHT_VALID_PIXELS
    allowed_scl_classes: tuple[int, ...] = HEIGHT_MASK_ALLOWED_SCL_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "allowed_scl_classes": list(self.allowed_scl_classes),
            "rejected_scl_classes": list(HEIGHT_MASK_REJECTED_SCL_CLASSES),
            "ndvi_role": "non_vegetation_purity_filter_not_height_proxy",
        }


@dataclass(frozen=True)
class HeightMaskResult:
    valid_mask: np.ndarray
    vegetation_fraction: float
    height_valid_pixel_count: int
    height_total_pixel_count: int
    mixed_pixel_risk: str
    purity_gate_passed: bool
    purity_gate_reasons: tuple[str, ...]


def mixed_pixel_risk(
    vegetation_fraction: float,
    valid_pixel_count: int,
    total_pixel_count: int,
) -> str:
    """Diagnostico deterministico de risco, nao classificacao fisica de mistura."""
    if (
        total_pixel_count <= 0
        or valid_pixel_count < DEFAULT_MIN_HEIGHT_VALID_PIXELS
        or vegetation_fraction < DEFAULT_MIN_VEGETATION_FRACTION
    ):
        return "high"
    if valid_pixel_count < 10 or total_pixel_count < 10 or vegetation_fraction < 0.60:
        return "medium"
    return "low"


def build_height_valid_mask(
    red_reflectance: np.ndarray,
    nir_reflectance: np.ndarray,
    *,
    quality_valid_mask: np.ndarray,
    inside_aoi_mask: np.ndarray,
    scl_values: np.ndarray,
    scl_valid_mask: np.ndarray | None = None,
    config: HeightMaskConfig = HeightMaskConfig(),
) -> HeightMaskResult:
    """Combina AOI, validade radiometrica, SCL vegetacao e NDVI de pureza."""
    shape = np.asarray(red_reflectance).shape
    arrays = (nir_reflectance, quality_valid_mask, inside_aoi_mask, scl_values)
    if any(np.asarray(value).shape != shape for value in arrays):
        raise ValueError("Height-mask inputs must share the same shape.")
    if not 0.0 <= config.min_ndvi <= 1.0:
        raise ValueError("Height-mask min_ndvi must be between zero and one.")
    if not 0.0 <= config.min_vegetation_fraction <= 1.0:
        raise ValueError("Minimum vegetation fraction must be between zero and one.")
    if config.min_valid_pixels <= 0:
        raise ValueError("Minimum height-valid pixels must be positive.")

    inside = np.asarray(inside_aoi_mask, dtype=bool)
    quality = np.asarray(quality_valid_mask, dtype=bool)
    scl_valid = (
        np.ones(shape, dtype=bool)
        if scl_valid_mask is None
        else np.asarray(scl_valid_mask, dtype=bool)
    )
    if scl_valid.shape != shape:
        raise ValueError("SCL validity mask must share the spectral shape.")

    ndvi, ndvi_valid = calculate_ndvi(red_reflectance, nir_reflectance)
    valid = inside & quality & scl_valid & ndvi_valid
    valid &= np.isin(np.asarray(scl_values), config.allowed_scl_classes)
    valid &= ndvi >= config.min_ndvi

    total = int(np.count_nonzero(inside))
    count = int(np.count_nonzero(valid))
    fraction = float(count / total) if total > 0 else 0.0
    risk = mixed_pixel_risk(fraction, count, total)
    reasons: list[str] = []
    if total <= 0:
        reasons.append("no_spatially_eligible_pixels")
    if count < config.min_valid_pixels:
        reasons.append("insufficient_height_valid_pixels")
    if fraction < config.min_vegetation_fraction:
        reasons.append("insufficient_vegetation_fraction")
    if risk == "high":
        reasons.append("high_mixed_pixel_risk")
    return HeightMaskResult(
        valid_mask=valid,
        vegetation_fraction=min(1.0, max(0.0, fraction)),
        height_valid_pixel_count=count,
        height_total_pixel_count=total,
        mixed_pixel_risk=risk,
        purity_gate_passed=not reasons,
        purity_gate_reasons=tuple(dict.fromkeys(reasons)),
    )


def diagnose_height_mask_pixels(
    red_reflectance: np.ndarray,
    nir_reflectance: np.ndarray,
    *,
    quality_valid_mask: np.ndarray,
    radiometric_valid_mask: np.ndarray,
    inside_aoi_mask: np.ndarray,
    scl_values: np.ndarray,
    scl_valid_mask: np.ndarray,
    inside_aoi_fraction: np.ndarray | None = None,
    config: HeightMaskConfig = HeightMaskConfig(),
) -> list[dict[str, Any]]:
    """Explica, sem alterar regras, cada pixel espacialmente elegível."""
    result = build_height_valid_mask(
        red_reflectance,
        nir_reflectance,
        quality_valid_mask=quality_valid_mask,
        inside_aoi_mask=inside_aoi_mask,
        scl_values=scl_values,
        scl_valid_mask=scl_valid_mask,
        config=config,
    )
    red = np.asarray(red_reflectance, dtype=float)
    nir = np.asarray(nir_reflectance, dtype=float)
    inside = np.asarray(inside_aoi_mask, dtype=bool)
    quality = np.asarray(quality_valid_mask, dtype=bool)
    radiometric = np.asarray(radiometric_valid_mask, dtype=bool)
    scl = np.asarray(scl_values)
    scl_valid = np.asarray(scl_valid_mask, dtype=bool)
    fractions = (
        np.asarray(inside_aoi_fraction, dtype=float)
        if inside_aoi_fraction is not None
        else None
    )
    ndvi, ndvi_valid = calculate_ndvi(red, nir)
    rows: list[dict[str, Any]] = []
    for row_index, column_index in np.argwhere(inside):
        reasons: list[str] = []
        finite = bool(
            np.isfinite(red[row_index, column_index])
            and np.isfinite(nir[row_index, column_index])
            and np.isfinite(ndvi[row_index, column_index])
        )
        if not finite:
            reasons.append("NON_FINITE")
        if not radiometric[row_index, column_index]:
            reasons.append("INVALID_RADIOMETRY")
        scl_allowed = bool(
            scl_valid[row_index, column_index]
            and scl[row_index, column_index] in config.allowed_scl_classes
        )
        if not scl_allowed:
            reasons.append("SCL_REJECTED")
        if (
            ndvi_valid[row_index, column_index]
            and ndvi[row_index, column_index] < config.min_ndvi
        ):
            reasons.append("NDVI_BELOW_MIN")
        if not quality[row_index, column_index] and not reasons:
            reasons.append("INVALID_RADIOMETRY")
        height_valid = bool(result.valid_mask[row_index, column_index])
        rows.append(
            {
                "pixel_id": f"r{int(row_index)}_c{int(column_index)}",
                "row": int(row_index),
                "column": int(column_index),
                "inside_aoi_fraction": float(fractions[row_index, column_index])
                if fractions is not None
                else None,
                "SCL": int(scl[row_index, column_index])
                if scl_valid[row_index, column_index]
                else None,
                "SCL_name": SCL_CLASS_NAMES.get(int(scl[row_index, column_index]))
                if scl_valid[row_index, column_index]
                else None,
                "RED": float(red[row_index, column_index])
                if np.isfinite(red[row_index, column_index])
                else None,
                "NIR": float(nir[row_index, column_index])
                if np.isfinite(nir[row_index, column_index])
                else None,
                "NDVI": float(ndvi[row_index, column_index])
                if np.isfinite(ndvi[row_index, column_index])
                else None,
                "radiometric_valid": bool(radiometric[row_index, column_index]),
                "vegetation_condition": bool(
                    scl_allowed
                    and ndvi_valid[row_index, column_index]
                    and ndvi[row_index, column_index] >= config.min_ndvi
                ),
                "height_valid": height_valid,
                "rejection_reason": [] if height_valid else list(dict.fromkeys(reasons)),
            }
        )
    return rows


def extract_height_features(
    item: Any,
    raster_data: RasterSceneData,
    *,
    config: HeightMaskConfig = HeightMaskConfig(),
) -> dict[str, Any]:
    """Fonte unica de RED/NIR/NDVI, height mask e diagnosticos de pureza."""
    if raster_data.inside_aoi_mask is None:
        raise RasterProcessingError("Mascara espacial da AOI indisponivel para altura.")
    if raster_data.scl_values is None or raster_data.scl_valid_mask is None:
        raise RasterProcessingError("SCL alinhado indisponivel para height_valid_mask.")

    red, nir, scaling = physical_reflectance_arrays(item, raster_data)
    mask_result = build_height_valid_mask(
        red,
        nir,
        quality_valid_mask=raster_data.valid_mask,
        inside_aoi_mask=raster_data.inside_aoi_mask,
        scl_values=raster_data.scl_values,
        scl_valid_mask=raster_data.scl_valid_mask,
        config=config,
    )
    result: dict[str, Any] = {
        "red_median_reflectance": None,
        "nir_median_reflectance": None,
        "ndvi_median": None,
        "vegetation_fraction": mask_result.vegetation_fraction,
        "height_valid_pixel_count": mask_result.height_valid_pixel_count,
        "height_total_pixel_count": mask_result.height_total_pixel_count,
        "mixed_pixel_risk": mask_result.mixed_pixel_risk,
        "height_purity_gate_passed": mask_result.purity_gate_passed,
        "height_purity_gate_reasons": list(mask_result.purity_gate_reasons),
        "reflectance_scale_source": scaling.source,
        "reflectance_scale": scaling.scale,
        "reflectance_offset": scaling.offset,
        "height_mask_configuration": config.to_dict(),
    }
    if mask_result.height_valid_pixel_count:
        mask = mask_result.valid_mask
        ndvi, _ = calculate_ndvi(red, nir, mask)
        result.update(
            {
                "red_median_reflectance": float(np.median(red[mask])),
                "nir_median_reflectance": float(np.median(nir[mask])),
                "ndvi_median": float(np.median(ndvi[mask])),
            }
        )
    return result
