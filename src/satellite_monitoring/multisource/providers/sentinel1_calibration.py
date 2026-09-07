"""Calibracao radiometrica sigma0 de Sentinel-1 Level-1 na grade GRD nativa."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import numpy as np

MAX_CALIBRATION_XML_BYTES = 5_000_000
CALIBRATION_REQUEST_TIMEOUT_SECONDS = 30
CALIBRATION_MAX_ATTEMPTS = 3
TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class Sentinel1CalibrationError(RuntimeError):
    """Indica metadado invalido ou correspondencia insegura entre LUT e raster."""


class Sentinel1CalibrationUnavailable(Sentinel1CalibrationError):
    """Indica que o item nao expoe os insumos necessarios para calibracao."""


@dataclass(frozen=True)
class Sentinel1CalibrationVector:
    """Um vetor sigmaNought aplicavel a uma linha da imagem nativa."""

    line: float
    pixels: np.ndarray
    sigma_nought: np.ndarray


@dataclass(frozen=True)
class Sentinel1CalibrationLut:
    """Vetores sigmaNought ordenados em azimute para interpolacao bilinear."""

    polarization: str
    product_type: str
    vectors: tuple[Sentinel1CalibrationVector, ...]

    @property
    def lines(self) -> np.ndarray:
        return np.asarray([vector.line for vector in self.vectors], dtype=np.float64)


@dataclass(frozen=True)
class Sigma0Metrics:
    """Estatisticas sigma0 calculadas somente sobre pixels calibrados validos."""

    vv_sigma0_median_linear: float | None = None
    vv_sigma0_mean_linear: float | None = None
    vv_sigma0_std_linear: float | None = None
    vh_sigma0_median_linear: float | None = None
    vh_sigma0_mean_linear: float | None = None
    vh_sigma0_std_linear: float | None = None
    vv_sigma0_median_db: float | None = None
    vh_sigma0_median_db: float | None = None
    vh_vv_sigma0_ratio_median: float | None = None
    vh_minus_vv_db_median: float | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def _required_text(element: ElementTree.Element, name: str) -> str:
    child = _direct_child(element, name)
    if child is None or not child.text or not child.text.strip():
        raise Sentinel1CalibrationError(f"missing_{name}")
    return child.text.strip()


def _parse_array(
    element: ElementTree.Element,
    name: str,
    *,
    dtype: type[np.float64] | type[np.int64],
) -> np.ndarray:
    child = _direct_child(element, name)
    if child is None or not child.text:
        raise Sentinel1CalibrationError(f"missing_{name}")
    try:
        values = np.fromstring(child.text, sep=" ", dtype=dtype)
        expected_count = int(child.attrib["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Sentinel1CalibrationError(f"invalid_{name}") from exc
    if values.size == 0 or values.size != expected_count:
        raise Sentinel1CalibrationError(f"invalid_{name}_count")
    return values


def parse_calibration_lut(xml_document: bytes | str) -> Sentinel1CalibrationLut:
    """Le calibrationVectorList e valida line/pixel/sigmaNought oficiais."""
    try:
        root = ElementTree.fromstring(xml_document)
    except ElementTree.ParseError as exc:
        raise Sentinel1CalibrationError("invalid_calibration_xml") from exc

    ads_header = next(
        (element for element in root.iter() if _local_name(element.tag) == "adsHeader"),
        None,
    )
    if ads_header is None:
        raise Sentinel1CalibrationError("missing_adsHeader")
    polarization = _required_text(ads_header, "polarisation").upper()
    product_type = _required_text(ads_header, "productType").upper()
    if polarization not in {"VV", "VH"}:
        raise Sentinel1CalibrationError("unsupported_calibration_polarization")
    if product_type != "GRD":
        raise Sentinel1CalibrationError("unsupported_calibration_product_type")

    vector_list = next(
        (element for element in root.iter() if _local_name(element.tag) == "calibrationVectorList"),
        None,
    )
    if vector_list is None:
        raise Sentinel1CalibrationError("missing_calibrationVectorList")
    vector_elements = [
        element
        for element in vector_list
        if _local_name(element.tag) == "calibrationVector"
    ]
    try:
        expected_count = int(vector_list.attrib["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Sentinel1CalibrationError("invalid_calibrationVectorList_count") from exc
    if len(vector_elements) < 2 or len(vector_elements) != expected_count:
        raise Sentinel1CalibrationError("invalid_calibrationVectorList_count")

    vectors: list[Sentinel1CalibrationVector] = []
    for element in vector_elements:
        try:
            line = float(_required_text(element, "line"))
        except ValueError as exc:
            raise Sentinel1CalibrationError("invalid_line") from exc
        pixels = _parse_array(element, "pixel", dtype=np.int64).astype(np.float64)
        sigma_nought = _parse_array(element, "sigmaNought", dtype=np.float64)
        if pixels.size != sigma_nought.size:
            raise Sentinel1CalibrationError("pixel_sigmaNought_count_mismatch")
        if not np.isfinite(line) or not np.all(np.isfinite(sigma_nought)):
            raise Sentinel1CalibrationError("nonfinite_calibration_lut")
        if np.any(sigma_nought <= 0):
            raise Sentinel1CalibrationError("nonpositive_sigmaNought_lut")
        if np.any(np.diff(pixels) <= 0):
            raise Sentinel1CalibrationError("nonmonotonic_pixel_lut")
        vectors.append(
            Sentinel1CalibrationVector(
                line=line,
                pixels=pixels,
                sigma_nought=sigma_nought,
            )
        )

    vectors.sort(key=lambda vector: vector.line)
    if np.any(np.diff([vector.line for vector in vectors]) <= 0):
        raise Sentinel1CalibrationError("nonmonotonic_line_lut")
    return Sentinel1CalibrationLut(
        polarization=polarization,
        product_type=product_type,
        vectors=tuple(vectors),
    )


@lru_cache(maxsize=128)
def load_calibration_lut(href: str) -> Sentinel1CalibrationLut:
    """Baixa e memoriza somente o pequeno XML de calibracao referenciado no STAC."""
    document: bytes | None = None
    for attempt in range(CALIBRATION_MAX_ATTEMPTS):
        try:
            request = Request(
                href, headers={"User-Agent": "CCR-Sentinel1Calibration/1.0"}
            )
            with urlopen(  # noqa: S310 - URL vem do item STAC assinado
                request, timeout=CALIBRATION_REQUEST_TIMEOUT_SECONDS
            ) as response:
                document = response.read(MAX_CALIBRATION_XML_BYTES + 1)
            break
        except Exception as exc:
            transient = isinstance(
                exc,
                (TimeoutError, ConnectionError, socket.timeout, URLError),
            ) and not isinstance(exc, HTTPError)
            if isinstance(exc, HTTPError):
                transient = exc.code in TRANSIENT_HTTP_STATUS_CODES
            if not transient or attempt + 1 >= CALIBRATION_MAX_ATTEMPTS:
                raise Sentinel1CalibrationUnavailable(
                    "calibration_asset_unavailable"
                ) from exc
            time.sleep(0.25 * (2**attempt))
    assert document is not None
    if len(document) > MAX_CALIBRATION_XML_BYTES:
        raise Sentinel1CalibrationError("calibration_xml_too_large")
    return parse_calibration_lut(document)


def interpolate_sigma_nought_lut(
    lut: Sentinel1CalibrationLut,
    lines: np.ndarray,
    samples: np.ndarray,
) -> np.ndarray:
    """Interpola sigmaNought primeiro em range e depois em azimute."""
    target_lines = np.asarray(lines, dtype=np.float64)
    target_samples = np.asarray(samples, dtype=np.float64)
    if target_lines.shape != target_samples.shape:
        raise Sentinel1CalibrationError("line_sample_shape_mismatch")
    if not np.all(np.isfinite(target_lines)) or not np.all(np.isfinite(target_samples)):
        raise Sentinel1CalibrationError("nonfinite_line_sample_coordinates")

    lut_lines = lut.lines
    if np.any(target_lines < lut_lines[0]) or np.any(target_lines > lut_lines[-1]):
        raise Sentinel1CalibrationError("line_outside_calibration_lut")

    flat_lines = target_lines.ravel()
    flat_samples = target_samples.ravel()
    upper_indices = np.searchsorted(lut_lines, flat_lines, side="right")
    upper_indices = np.clip(upper_indices, 1, len(lut_lines) - 1)
    lower_indices = upper_indices - 1
    lower_values = np.empty_like(flat_lines)
    upper_values = np.empty_like(flat_lines)

    for lower_index, upper_index in set(zip(lower_indices, upper_indices, strict=True)):
        selection = (lower_indices == lower_index) & (upper_indices == upper_index)
        selected_samples = flat_samples[selection]
        lower_vector = lut.vectors[int(lower_index)]
        upper_vector = lut.vectors[int(upper_index)]
        if (
            np.any(selected_samples < lower_vector.pixels[0])
            or np.any(selected_samples > lower_vector.pixels[-1])
            or np.any(selected_samples < upper_vector.pixels[0])
            or np.any(selected_samples > upper_vector.pixels[-1])
        ):
            raise Sentinel1CalibrationError("sample_outside_calibration_lut")
        lower_values[selection] = np.interp(
            selected_samples, lower_vector.pixels, lower_vector.sigma_nought
        )
        upper_values[selection] = np.interp(
            selected_samples, upper_vector.pixels, upper_vector.sigma_nought
        )

    line_denominator = lut_lines[upper_indices] - lut_lines[lower_indices]
    line_weight = (flat_lines - lut_lines[lower_indices]) / line_denominator
    interpolated = lower_values + line_weight * (upper_values - lower_values)
    if np.any(~np.isfinite(interpolated)) or np.any(interpolated <= 0):
        raise Sentinel1CalibrationError("invalid_interpolated_sigmaNought")
    return interpolated.reshape(target_lines.shape)


def calibrate_sigma0(
    dn: np.ma.MaskedArray,
    lut: Sentinel1CalibrationLut,
    *,
    line_offset: int = 0,
    sample_offset: int = 0,
) -> np.ma.MaskedArray:
    """Aplica sigma0 = DN^2 / A_sigma^2 usando line/sample da grade nativa."""
    dn_array = np.ma.asarray(dn)
    rows, columns = np.indices(dn_array.shape, dtype=np.float64)
    lines = rows + float(line_offset)
    samples = columns + float(sample_offset)
    calibration = interpolate_sigma_nought_lut(lut, lines, samples)
    raw = np.asarray(dn_array.data, dtype=np.float64)
    valid = (
        ~np.ma.getmaskarray(dn_array)
        & np.isfinite(raw)
        & (raw > 0)
        & np.isfinite(calibration)
        & (calibration > 0)
    )
    sigma0 = np.zeros(raw.shape, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        sigma0[valid] = np.square(raw[valid] / calibration[valid])
    valid &= np.isfinite(sigma0) & (sigma0 > 0)
    return np.ma.array(sigma0, mask=~valid)


def sigma0_linear_to_db(sigma0: np.ma.MaskedArray) -> np.ma.MaskedArray:
    """Converte somente sigma0 linear estritamente positivo e finito para dB."""
    linear = np.ma.asarray(sigma0)
    values = np.asarray(linear.data, dtype=np.float64)
    valid = ~np.ma.getmaskarray(linear) & np.isfinite(values) & (values > 0)
    db = np.zeros(values.shape, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        db[valid] = 10.0 * np.log10(values[valid])
    valid &= np.isfinite(db)
    return np.ma.array(db, mask=~valid)


def _values(array: np.ma.MaskedArray, inside_aoi: np.ndarray) -> np.ndarray:
    values = np.asarray(array.data, dtype=np.float64)
    valid = inside_aoi & ~np.ma.getmaskarray(array) & np.isfinite(values) & (values > 0)
    return values[valid]


def _statistics(values: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if values.size == 0:
        return None, None, None
    return float(np.median(values)), float(np.mean(values)), float(np.std(values))


def calculate_sigma0_metrics(
    vv_sigma0: np.ma.MaskedArray | None,
    vh_sigma0: np.ma.MaskedArray | None,
    *,
    inside_aoi: np.ndarray,
) -> Sigma0Metrics:
    """Resume sigma0; razao e diferenca usam pares de pixels co-validos."""
    vv_values = _values(vv_sigma0, inside_aoi) if vv_sigma0 is not None else np.array([])
    vh_values = _values(vh_sigma0, inside_aoi) if vh_sigma0 is not None else np.array([])
    vv_median, vv_mean, vv_std = _statistics(vv_values)
    vh_median, vh_mean, vh_std = _statistics(vh_values)

    vv_db_values = 10.0 * np.log10(vv_values) if vv_values.size else np.array([])
    vh_db_values = 10.0 * np.log10(vh_values) if vh_values.size else np.array([])
    ratio_median = None
    difference_median = None
    if vv_sigma0 is not None and vh_sigma0 is not None:
        vv = np.asarray(vv_sigma0.data, dtype=np.float64)
        vh = np.asarray(vh_sigma0.data, dtype=np.float64)
        paired = (
            inside_aoi
            & ~np.ma.getmaskarray(vv_sigma0)
            & ~np.ma.getmaskarray(vh_sigma0)
            & np.isfinite(vv)
            & np.isfinite(vh)
            & (vv > 0)
            & (vh > 0)
        )
        if np.any(paired):
            ratios = vh[paired] / vv[paired]
            differences = 10.0 * np.log10(vh[paired]) - 10.0 * np.log10(vv[paired])
            finite_ratios = ratios[np.isfinite(ratios) & (ratios > 0)]
            finite_differences = differences[np.isfinite(differences)]
            if finite_ratios.size:
                ratio_median = float(np.median(finite_ratios))
            if finite_differences.size:
                difference_median = float(np.median(finite_differences))

    return Sigma0Metrics(
        vv_sigma0_median_linear=vv_median,
        vv_sigma0_mean_linear=vv_mean,
        vv_sigma0_std_linear=vv_std,
        vh_sigma0_median_linear=vh_median,
        vh_sigma0_mean_linear=vh_mean,
        vh_sigma0_std_linear=vh_std,
        vv_sigma0_median_db=(float(np.median(vv_db_values)) if vv_db_values.size else None),
        vh_sigma0_median_db=(float(np.median(vh_db_values)) if vh_db_values.size else None),
        vh_vv_sigma0_ratio_median=ratio_median,
        vh_minus_vv_db_median=difference_median,
    )
