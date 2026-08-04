"""Leitura recortada e alinhada das bandas necessarias ao NDVI."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom

# SCL: nodata, saturado/defeituoso, sombra, nuvem media/alta, cirrus e neve.
SCL_EXCLUDED_CLASSES = {0, 1, 3, 8, 9, 10, 11}


class RasterProcessingError(RuntimeError):
    """Indica que uma cena nao pode ser recortada ou preparada."""


@dataclass(frozen=True)
class RasterSceneData:
    red: np.ndarray
    nir: np.ndarray
    valid_mask: np.ndarray
    total_pixel_count: int
    partial_raster_coverage: bool
    red_asset: str
    nir_asset: str
    scl_asset: str | None
    quality_messages: list[str]


def _asset_common_names(asset: Any) -> set[str]:
    bands = asset.extra_fields.get("eo:bands", [])
    if isinstance(bands, dict):
        bands = [bands]
    return {
        str(band.get("common_name", "")).lower()
        for band in bands
        if isinstance(band, dict) and band.get("common_name")
    }


def find_asset_key(
    item: Any,
    common_names: set[str],
    fallback_keys: tuple[str, ...],
) -> str | None:
    """Encontra um asset por eo:bands/common_name e depois por chave conhecida."""
    expected_names = {name.lower() for name in common_names}
    for key, asset in item.assets.items():
        if _asset_common_names(asset) & expected_names:
            return key

    keys_by_lowercase = {key.lower(): key for key in item.assets}
    for fallback in fallback_keys:
        key = keys_by_lowercase.get(fallback.lower())
        if key is not None:
            return key
    return None


def _scale_and_offset(asset: Any) -> tuple[float, float, float | None]:
    raster_bands = asset.extra_fields.get("raster:bands", [])
    if isinstance(raster_bands, dict):
        raster_bands = [raster_bands]
    band = raster_bands[0] if raster_bands else {}
    return (
        float(band.get("scale", 1.0)),
        float(band.get("offset", 0.0)),
        band.get("nodata"),
    )


def _prepare_reflectance(data: np.ma.MaskedArray, asset: Any) -> tuple[np.ndarray, np.ndarray]:
    raw_values = np.asarray(data.data, dtype=np.float32)
    scale, offset, metadata_nodata = _scale_and_offset(asset)
    values = raw_values * scale + offset

    valid = ~np.ma.getmaskarray(data)
    valid &= np.isfinite(values)
    if metadata_nodata is not None:
        valid &= raw_values != float(metadata_nodata)
    else:
        # Sentinel-2 L2A usa zero como NO_DATA nos assets espectrais.
        valid &= raw_values != 0
    return values, valid


def _read_aligned_band(
    stack: ExitStack,
    href: str,
    reference: Any,
    window: Any,
    resampling: Resampling,
) -> np.ma.MaskedArray:
    source = stack.enter_context(rasterio.open(href))
    vrt = stack.enter_context(
        WarpedVRT(
            source,
            crs=reference.crs,
            transform=reference.transform,
            width=reference.width,
            height=reference.height,
            resampling=resampling,
        )
    )
    return vrt.read(1, window=window, masked=True)


def read_scene_bands(item: Any, aoi_geojson: dict[str, Any]) -> RasterSceneData:
    """Le vermelho, NIR e SCL de um COG remoto apenas na janela da AOI."""
    red_key = find_asset_key(item, {"red"}, ("red", "B04"))
    nir_key = find_asset_key(item, {"nir", "nir08"}, ("nir", "B08"))
    scl_key = find_asset_key(item, {"scl"}, ("SCL", "scl"))

    if red_key is None or nir_key is None:
        raise RasterProcessingError(
            "A cena nao possui assets identificaveis para vermelho e NIR. "
            f"Assets disponiveis: {', '.join(sorted(item.assets))}"
        )

    red_asset = item.assets[red_key]
    nir_asset = item.assets[nir_key]
    quality_messages: list[str] = []

    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }

    try:
        with rasterio.Env(**env_options), ExitStack() as stack:
            reference = stack.enter_context(rasterio.open(red_asset.href))
            aoi_in_reference_crs = transform_geom(
                "EPSG:4326",
                reference.crs,
                aoi_geojson,
                precision=15,
            )
            full_aoi_window = geometry_window(
                reference,
                [aoi_in_reference_crs],
                boundless=True,
            )
            full_aoi_shape = (int(full_aoi_window.height), int(full_aoi_window.width))
            full_aoi_mask = geometry_mask(
                [aoi_in_reference_crs],
                out_shape=full_aoi_shape,
                transform=reference.window_transform(full_aoi_window),
                invert=True,
            )
            total_pixel_count = int(np.count_nonzero(full_aoi_mask))
            partial_raster_coverage = (
                full_aoi_window.col_off < 0
                or full_aoi_window.row_off < 0
                or full_aoi_window.col_off + full_aoi_window.width > reference.width
                or full_aoi_window.row_off + full_aoi_window.height > reference.height
            )
            window = geometry_window(reference, [aoi_in_reference_crs])

            red_raw = reference.read(1, window=window, masked=True)
            output_transform = reference.window_transform(window)
            inside_aoi = geometry_mask(
                [aoi_in_reference_crs],
                out_shape=red_raw.shape,
                transform=output_transform,
                invert=True,
            )

            nir_raw = _read_aligned_band(
                stack,
                nir_asset.href,
                reference,
                window,
                Resampling.bilinear,
            )
            red, red_valid = _prepare_reflectance(red_raw, red_asset)
            nir, nir_valid = _prepare_reflectance(nir_raw, nir_asset)
            valid_mask = inside_aoi & red_valid & nir_valid

            if scl_key is not None:
                scl_asset = item.assets[scl_key]
                scl = _read_aligned_band(
                    stack,
                    scl_asset.href,
                    reference,
                    window,
                    Resampling.nearest,
                )
                scl_values = np.asarray(scl.data)
                valid_mask &= ~np.ma.getmaskarray(scl)
                valid_mask &= ~np.isin(scl_values, list(SCL_EXCLUDED_CLASSES))
                quality_messages.append(
                    "Mascara SCL aplicada; classes removidas: 0, 1, 3, 8, 9, 10 e 11."
                )
            else:
                quality_messages.append(
                    "Asset SCL ausente; NDVI calculado sem mascara local de nuvem, "
                    "sombra, cirrus e neve."
                )

    except (WindowError, ValueError) as exc:
        raise RasterProcessingError("A area de interesse nao intercepta o raster da cena.") from exc
    except rasterio.errors.RasterioError as exc:
        raise RasterProcessingError(f"Falha ao acessar os assets remotos: {exc}") from exc

    if total_pixel_count == 0:
        raise RasterProcessingError("A area de interesse nao contem pixels na grade da cena.")

    return RasterSceneData(
        red=red,
        nir=nir,
        valid_mask=valid_mask,
        total_pixel_count=total_pixel_count,
        partial_raster_coverage=partial_raster_coverage,
        red_asset=red_key,
        nir_asset=nir_key,
        scl_asset=scl_key,
        quality_messages=quality_messages,
    )
