"""Leitura recortada e alinhada das bandas necessarias ao NDVI."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Any
import urllib.request
from xml.etree import ElementTree

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, shape

# SCL: nodata, saturado/defeituoso, sombra, nuvem media/alta, cirrus e neve.
SCL_EXCLUDED_CLASSES = {0, 1, 3, 8, 9, 10, 11}
SCL_CLASS_NAMES = {
    0: "nodata",
    1: "saturated_or_defective",
    2: "dark_area",
    3: "cloud_shadow",
    4: "vegetation",
    5: "non_vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud_medium_probability",
    9: "cloud_high_probability",
    10: "cirrus",
    11: "snow_or_ice",
}


class RasterProcessingError(RuntimeError):
    """Indica que uma cena nao pode ser recortada ou preparada."""


@dataclass(frozen=True)
class ReflectanceScaling:
    source: str
    scale: float
    offset: float


@dataclass(frozen=True)
class RasterSceneData:
    red: np.ndarray
    nir: np.ndarray
    valid_mask: np.ndarray
    total_pixel_count: int
    aoi_coverage_percentage: float
    partial_raster_coverage: bool
    red_asset: str
    nir_asset: str
    scl_asset: str | None
    scl_class_percentages: dict[str, float]
    quality_messages: list[str]
    red_raw: np.ndarray | None = None
    nir_raw: np.ndarray | None = None
    inside_aoi_mask: np.ndarray | None = None
    scl_values: np.ndarray | None = None
    scl_valid_mask: np.ndarray | None = None
    spatial_transform: Any | None = None
    spatial_aoi_geometry: dict[str, Any] | None = None
    spatial_crs: str | None = None
    spatial_crs_is_projected: bool | None = None


def calculate_effective_analysis_area(
    raster_data: RasterSceneData,
    accepted_pixel_mask: np.ndarray,
) -> float:
    """Soma AOI ∩ pixels aceitos sem alterar o peso das metricas raster."""
    return calculate_mask_intersection_area(
        transform=raster_data.spatial_transform,
        geometry_document=raster_data.spatial_aoi_geometry,
        crs_is_projected=raster_data.spatial_crs_is_projected,
        accepted_pixel_mask=accepted_pixel_mask,
    )


def calculate_mask_intersection_area(
    *,
    transform: Any,
    geometry_document: dict[str, Any] | None,
    crs_is_projected: bool | None,
    accepted_pixel_mask: np.ndarray,
) -> float:
    """Calcula a intersecao exata para uma grade e mascara ja processadas."""
    if (
        transform is None
        or geometry_document is None
        or crs_is_projected is not True
    ):
        raise RasterProcessingError(
            "Metadados metricos da grade indisponiveis para contabilidade espacial."
        )
    accepted = np.asarray(accepted_pixel_mask, dtype=bool)
    if accepted.ndim != 2:
        raise ValueError("A mascara aceita deve ser bidimensional.")

    aoi = shape(geometry_document)
    area = 0.0
    for row, column in np.argwhere(accepted):
        corners = [
            transform * (int(column), int(row)),
            transform * (int(column) + 1, int(row)),
            transform * (int(column) + 1, int(row) + 1),
            transform * (int(column), int(row) + 1),
        ]
        area += float(aoi.intersection(Polygon(corners)).area)
    return area


def calculate_scl_class_percentages(
    scl: np.ma.MaskedArray,
    inside_aoi: np.ndarray,
) -> dict[str, float]:
    """Calcula a composicao SCL dentro da AOI sem inferir tipo de vegetacao."""
    total = int(np.count_nonzero(inside_aoi))
    if total == 0:
        return {name: 0.0 for name in SCL_CLASS_NAMES.values()}

    values = np.asarray(scl.data)
    masked = np.ma.getmaskarray(scl)
    percentages: dict[str, float] = {}
    for class_value, name in SCL_CLASS_NAMES.items():
        class_mask = inside_aoi & ~masked & (values == class_value)
        count = int(np.count_nonzero(class_mask))
        if class_value == 0:
            count += int(np.count_nonzero(inside_aoi & masked))
        percentages[name] = count / total * 100.0
    return percentages


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


def apply_reflectance_scaling(
    values: np.ndarray, *, scale: float, offset: float
) -> np.ndarray:
    """Converte DN para reflectancia sem modificar os arrays operacionais."""
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Reflectance scale must be finite and positive.")
    if not math.isfinite(offset):
        raise ValueError("Reflectance offset must be finite.")
    return np.asarray(values, dtype=float) * scale + offset


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@lru_cache(maxsize=64)
def _product_metadata_scaling(metadata_href: str) -> ReflectanceScaling:
    try:
        xml_data = urllib.request.urlopen(metadata_href, timeout=30).read()
        root = ElementTree.fromstring(xml_data)
        quantification_values = [
            float((node.text or "").strip())
            for node in root.iter()
            if _local_xml_name(node.tag) == "BOA_QUANTIFICATION_VALUE"
            and (node.text or "").strip()
        ]
        offsets = [
            float((node.text or "").strip())
            for node in root.iter()
            if _local_xml_name(node.tag) == "BOA_ADD_OFFSET"
            and (node.text or "").strip()
        ]
    except Exception as exc:
        raise RasterProcessingError(
            "Nao foi possivel ler os metadados fisicos de reflectancia: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if len(quantification_values) != 1 or quantification_values[0] <= 0:
        raise RasterProcessingError("BOA_QUANTIFICATION_VALUE ausente ou invalido.")
    if not offsets or len(set(offsets)) != 1:
        raise RasterProcessingError(
            "BOA_ADD_OFFSET ausente ou diferente entre bandas; escala insegura."
        )
    quantification = quantification_values[0]
    return ReflectanceScaling(
        source="product_metadata_BOA_QUANTIFICATION_VALUE_and_BOA_ADD_OFFSET",
        scale=1.0 / quantification,
        offset=offsets[0] / quantification,
    )


def resolve_physical_reflectance_scaling(
    item: Any,
    *,
    asset_key: str | None = None,
    dataset_scale: float = 1.0,
    dataset_offset: float = 0.0,
) -> ReflectanceScaling:
    """Resolve scale/offset explicitos sem presumir cegamente DN/10000."""
    key = asset_key or find_asset_key(item, {"red"}, ("red", "B04"))
    if key is None:
        raise RasterProcessingError("Asset espectral ausente para resolver reflectancia.")
    raster_bands = item.assets[key].extra_fields.get("raster:bands", [])
    if isinstance(raster_bands, dict):
        raster_bands = [raster_bands]
    if raster_bands and "scale" in raster_bands[0] and "offset" in raster_bands[0]:
        return ReflectanceScaling(
            source="asset_raster_bands",
            scale=float(raster_bands[0]["scale"]),
            offset=float(raster_bands[0]["offset"]),
        )
    if dataset_scale != 1.0 or dataset_offset != 0.0:
        return ReflectanceScaling(
            source="geotiff_dataset_scale_offset",
            scale=float(dataset_scale),
            offset=float(dataset_offset),
        )
    metadata_asset = item.assets.get("product-metadata")
    if metadata_asset is None:
        raise RasterProcessingError(
            "Scale/offset ausentes no asset/GeoTIFF e product-metadata indisponivel."
        )
    return _product_metadata_scaling(str(metadata_asset.href))


def physical_reflectance_arrays(
    item: Any, raster_data: RasterSceneData
) -> tuple[np.ndarray, np.ndarray, ReflectanceScaling]:
    """Converte os arrays raw RED/NIR pela fonte radiometrica canonica."""
    if raster_data.red_raw is None or raster_data.nir_raw is None:
        raise RasterProcessingError("Arrays raw RED/NIR nao foram preservados.")
    scaling = resolve_physical_reflectance_scaling(
        item, asset_key=raster_data.red_asset
    )
    red = apply_reflectance_scaling(
        raster_data.red_raw, scale=scaling.scale, offset=scaling.offset
    )
    nir = apply_reflectance_scaling(
        raster_data.nir_raw, scale=scaling.scale, offset=scaling.offset
    )
    return red, nir, scaling


def physical_reflectance_medians(
    item: Any, raster_data: RasterSceneData
) -> dict[str, float | str]:
    """Extrai RED/NIR fisicos sobre a mesma mascara valida do NDVI existente."""
    red, nir, scaling = physical_reflectance_arrays(item, raster_data)
    mask = np.asarray(raster_data.valid_mask, dtype=bool)
    mask &= np.isfinite(red) & np.isfinite(nir)
    if not np.any(mask):
        raise RasterProcessingError("Nenhum pixel valido para features de altura.")
    return {
        "red_median_reflectance": float(np.median(red[mask])),
        "nir_median_reflectance": float(np.median(nir[mask])),
        "reflectance_scale_source": scaling.source,
        "reflectance_scale": scaling.scale,
        "reflectance_offset": scaling.offset,
    }


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
    scl_class_percentages: dict[str, float] = {}

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
            spatial_crs = str(reference.crs)
            spatial_crs_is_projected = bool(reference.crs.is_projected)
            inside_aoi = geometry_mask(
                [aoi_in_reference_crs],
                out_shape=red_raw.shape,
                transform=output_transform,
                invert=True,
            )
            covered_pixel_count = int(np.count_nonzero(inside_aoi))
            aoi_coverage_percentage = (
                min(100.0, covered_pixel_count / total_pixel_count * 100.0)
                if total_pixel_count > 0
                else 0.0
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

            scl_values_for_height: np.ndarray | None = None
            scl_valid_for_height: np.ndarray | None = None
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
                scl_values_for_height = scl_values.copy()
                scl_valid_for_height = ~np.ma.getmaskarray(scl)
                scl_class_percentages = calculate_scl_class_percentages(scl, inside_aoi)
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

    except WindowError as exc:
        raise RasterProcessingError("A area de interesse nao intercepta o raster da cena.") from exc
    except UnicodeError as exc:
        raise RasterProcessingError(
            f"Falha de codificacao ao acessar o raster remoto: {exc}"
        ) from exc
    except ValueError as exc:
        raise RasterProcessingError(f"Falha ao preparar o recorte raster: {exc}") from exc
    except rasterio.errors.RasterioError as exc:
        raise RasterProcessingError(f"Falha ao acessar os assets remotos: {exc}") from exc

    if total_pixel_count == 0:
        raise RasterProcessingError("A area de interesse nao contem pixels na grade da cena.")

    return RasterSceneData(
        red=red,
        nir=nir,
        valid_mask=valid_mask,
        total_pixel_count=total_pixel_count,
        aoi_coverage_percentage=aoi_coverage_percentage,
        partial_raster_coverage=partial_raster_coverage,
        red_asset=red_key,
        nir_asset=nir_key,
        scl_asset=scl_key,
        scl_class_percentages=scl_class_percentages,
        quality_messages=quality_messages,
        red_raw=np.asarray(red_raw.data, dtype=np.float32),
        nir_raw=np.asarray(nir_raw.data, dtype=np.float32),
        inside_aoi_mask=np.asarray(inside_aoi, dtype=bool),
        scl_values=scl_values_for_height,
        scl_valid_mask=scl_valid_for_height,
        spatial_transform=output_transform,
        spatial_aoi_geometry=dict(aoi_in_reference_crs),
        spatial_crs=spatial_crs,
        spatial_crs_is_projected=spatial_crs_is_projected,
    )
