"""Probe diagnostico de candidate granules NASA CMR para uma AOI GeoJSON."""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform, unary_union

CMR_GRANULES_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_SAMPLE_SIZE = 5
DEFAULT_MAX_GRANULES = 3
DEFAULT_NEAR_DISTANCE_METERS = 50.0
ATL08QL_DISCOVERY_DAYS = 120
BEAMS = ("gt1l", "gt1r", "gt2l", "gt2r", "gt3l", "gt3r")
OPERATIONAL_TIMEZONE = "America/Sao_Paulo"
COVERAGE_LEVEL = "granule_metadata_candidate"
LIMITATION = (
    "CMR granule intersection does not prove that a GEDI footprint or ATL08 "
    "along-track vegetation segment falls inside the AOI."
)
VERTICAL_EVIDENCE_LIMITATION = (
    "ICESat-2 ATL08/ATL08QL terrain and vegetation heights are auxiliary vertical "
    "evidence and must not be interpreted as roadside grass height."
)
SAMPLE_LIMITATION = (
    "Inspecting at most three recent granules is a diagnostic sample and does not "
    "prove absence of relevant segments across the complete product archive."
)


@dataclass(frozen=True)
class Product:
    key: str
    short_name: str
    version: str
    product_role: str
    preliminary: bool


@dataclass(frozen=True)
class SpatialQuery:
    query_type: str
    coordinates: tuple[float, ...]
    bbox: tuple[float, float, float, float]
    fallback_reason: str | None = None


PRODUCTS = (
    Product("gedi_l2a", "GEDI02_A", "002", "final_science", False),
    Product("icesat2_atl08", "ATL08", "007", "final_science", False),
    Product(
        "icesat2_atl08ql",
        "ATL08QL",
        "007",
        "expedited_preliminary",
        True,
    ),
)

HttpGet = Callable[[str, float], tuple[bytes, Mapping[str, str]]]


def _validate_position(position: object) -> tuple[float, float]:
    if not isinstance(position, list) or len(position) < 2:
        raise ValueError("GeoJSON coordinates must use [longitude, latitude].")
    longitude, latitude = position[:2]
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        raise ValueError("GeoJSON coordinates must be numeric.")
    try:
        lon = float(longitude)
        lat = float(latitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("GeoJSON coordinates must be numeric.") from exc
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError("GeoJSON coordinates must be finite.")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError("GeoJSON coordinates are outside longitude/latitude bounds.")
    return lon, lat


def _polygon_exteriors(geometry: object) -> list[list[object]]:
    if not isinstance(geometry, dict):
        raise ValueError("GeoJSON geometry must be an object.")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("Polygon coordinates are empty.")
        return [coordinates[0]]
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("MultiPolygon coordinates are empty.")
        exteriors = []
        for polygon in coordinates:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("MultiPolygon contains an empty polygon.")
            exteriors.append(polygon[0])
        return exteriors
    raise ValueError("The probe accepts only Polygon or MultiPolygon geometries.")


def _document_exteriors(document: object) -> list[list[object]]:
    if not isinstance(document, dict):
        raise ValueError("GeoJSON root must be an object.")
    document_type = document.get("type")
    if document_type in {"Polygon", "MultiPolygon"}:
        return _polygon_exteriors(document)
    if document_type == "Feature":
        return _polygon_exteriors(document.get("geometry"))
    if document_type == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list) or not features:
            raise ValueError("FeatureCollection cannot be empty.")
        exteriors: list[list[object]] = []
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("FeatureCollection contains an invalid feature.")
            exteriors.extend(_polygon_exteriors(feature.get("geometry")))
        return exteriors
    raise ValueError("Unsupported GeoJSON type for NASA coverage probe.")


def _normalize_ring(raw_ring: list[object]) -> list[tuple[float, float]]:
    ring = [_validate_position(position) for position in raw_ring]
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(set(ring)) < 3:
        raise ValueError("Polygon exterior must contain at least three unique positions.")
    signed_area = sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring))
    )
    if signed_area == 0:
        raise ValueError("Polygon exterior has zero area.")
    if signed_area < 0:
        ring.reverse()
    return [*ring, ring[0]]


def extract_aoi_spatial_query(document: object) -> SpatialQuery:
    """Extrai Polygon real; usa bbox apenas para geometrias com varias partes."""
    rings = [_normalize_ring(ring) for ring in _document_exteriors(document)]
    positions = [position for ring in rings for position in ring]
    longitudes = [position[0] for position in positions]
    latitudes = [position[1] for position in positions]
    bbox = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    if len(rings) == 1:
        polygon = tuple(value for position in rings[0] for value in position)
        return SpatialQuery("polygon", polygon, bbox)
    return SpatialQuery(
        "bounding_box",
        bbox,
        bbox,
        "CMR polygon query supports one exterior; multipart AOI used its derived bbox.",
    )


def _load_geojson_document(path: str | Path) -> object:
    geometry_path = Path(path)
    try:
        return json.loads(geometry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"GeoJSON file not found: {geometry_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in GeoJSON file: {geometry_path}") from exc


def load_aoi(path: str | Path) -> SpatialQuery:
    return extract_aoi_spatial_query(_load_geojson_document(path))


def load_aoi_geometry(path: str | Path):
    """Carrega a mesma geometria da consulta como Polygon/MultiPolygon Shapely."""
    rings = [_normalize_ring(ring) for ring in _document_exteriors(
        _load_geojson_document(path)
    )]
    geometry = unary_union([Polygon(ring) for ring in rings])
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("AOI geometry must be non-empty and valid.")
    return geometry


def build_cmr_query(
    product: Product,
    spatial: SpatialQuery,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> str:
    if (start_date is None) != (end_date is None):
        raise ValueError("Start and end dates must be provided together.")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("Start date cannot be after end date.")
    parameters = {
        "short_name": product.short_name,
        "version": product.version,
        "page_size": str(sample_size),
        "sort_key": "-start_date",
        spatial.query_type: ",".join(f"{value:.12g}" for value in spatial.coordinates),
    }
    if start_date is not None and end_date is not None:
        parameters["temporal"] = (
            f"{start_date.isoformat()}T00:00:00Z,"
            f"{end_date.isoformat()}T23:59:59Z"
        )
    return f"{CMR_GRANULES_URL}?{urlencode(parameters)}"


def _default_http_get(url: str, timeout: float) -> tuple[bytes, Mapping[str, str]]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "motiva-faixa-verde-nasa-coverage-probe/0.1",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read(), dict(response.headers.items())


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.lower()
    return next((value for key, value in headers.items() if key.lower() == expected), None)


def _sample_entry(entry: object) -> dict[str, object]:
    if not isinstance(entry, dict):
        raise ValueError("CMR granule entry must be an object.")
    links = [
        {
            key: link[key]
            for key in ("href", "rel", "title")
            if key in link
        }
        for link in entry.get("links", [])[:3]
        if isinstance(link, dict) and link.get("href")
    ]
    sample: dict[str, object] = {
        "granule_id": entry.get("producer_granule_id") or entry.get("title"),
        "title": entry.get("title"),
        "concept_id": entry.get("id"),
        "beginning_datetime": entry.get("time_start"),
        "ending_datetime": entry.get("time_end"),
        "provider": entry.get("data_center"),
    }
    if links:
        sample["links"] = links
        download_url = next(
            (
                str(link["href"])
                for link in entry.get("links", [])
                if isinstance(link, dict)
                and str(link.get("href", "")).lower().endswith(".h5")
                and str(link.get("href", "")).startswith("https://")
            ),
            None,
        )
        if download_url:
            sample["download_url"] = download_url
    spatial: dict[str, object] = {}
    if entry.get("boxes"):
        spatial["boxes"] = entry["boxes"]
    polygons = entry.get("polygons")
    if isinstance(polygons, list) and polygons:
        spatial["polygon_count"] = len(polygons)
    if spatial:
        sample["spatial"] = spatial
    return sample


def parse_candidate_granules(
    payload: bytes,
    headers: Mapping[str, str],
) -> dict[str, object]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CMR returned invalid JSON.") from exc
    entries = document.get("feed", {}).get("entry") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ValueError("CMR response does not contain a granule feed.")
    raw_hits = _header(headers, "CMR-Hits")
    try:
        candidate_count = int(raw_hits) if raw_hits is not None else len(entries)
    except ValueError as exc:
        raise ValueError("CMR-Hits is not an integer.") from exc
    return {
        "candidate_status": "candidates_found" if candidate_count else "no_candidates",
        "candidate_granule_count": candidate_count,
        "sample": [_sample_entry(entry) for entry in entries],
    }


def _safe_query_error(exc: Exception) -> dict[str, object]:
    if isinstance(exc, HTTPError):
        message = f"CMR returned HTTP {exc.code}."
    elif isinstance(exc, (URLError, TimeoutError)):
        message = "CMR request could not be completed."
    elif isinstance(exc, ValueError):
        message = "CMR returned an invalid response."
    else:
        message = "CMR query failed."
    return {
        "candidate_status": "query_error",
        "candidate_granule_count": None,
        "sample": [],
        "error_type": type(exc).__name__,
        "message": message,
    }


def _query_product(
    product: Product,
    spatial: SpatialQuery,
    *,
    start_date: date | None,
    end_date: date | None,
    timeout: float,
    http_get: HttpGet,
) -> dict[str, object]:
    url = build_cmr_query(
        product,
        spatial,
        start_date=start_date,
        end_date=end_date,
    )
    try:
        payload, headers = http_get(url, timeout)
        return parse_candidate_granules(payload, headers)
    except Exception as exc:
        return _safe_query_error(exc)


def _technical_decision(
    operational: Mapping[str, object],
    historical: Mapping[str, object],
) -> tuple[str, str]:
    counts = (
        operational.get("candidate_granule_count"),
        historical.get("candidate_granule_count"),
    )
    if any(isinstance(count, int) and count > 0 for count in counts):
        return "candidates_found", "GO_TO_FOOTPRINT_VALIDATION"
    if historical.get("candidate_status") == "no_candidates":
        return "no_candidates", "NO_CANDIDATES_FOR_TEST_AOI"
    return "query_error", "UNABLE_TO_VERIFY"


def probe_coverage(
    geometry_file: str | Path,
    start_date: date,
    end_date: date,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_get: HttpGet = _default_http_get,
) -> dict[str, object]:
    spatial = load_aoi(geometry_file)
    sources: dict[str, object] = {}
    for product in PRODUCTS:
        operational = _query_product(
            product,
            spatial,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
            http_get=http_get,
        )
        if product.short_name == "ATL08QL":
            diagnostic_start = end_date - timedelta(days=ATL08QL_DISCOVERY_DAYS - 1)
            secondary_name = "diagnostic_recent_period"
            secondary = {
                "start_date": diagnostic_start.isoformat(),
                "end_date": end_date.isoformat(),
                **_query_product(
                    product,
                    spatial,
                    start_date=diagnostic_start,
                    end_date=end_date,
                    timeout=timeout,
                    http_get=http_get,
                ),
            }
        else:
            secondary_name = "historical"
            secondary = _query_product(
                product,
                spatial,
                start_date=None,
                end_date=None,
                timeout=timeout,
                http_get=http_get,
            )
        candidate_status, decision = _technical_decision(operational, secondary)
        sources[product.key] = {
            "short_name": product.short_name,
            "version": product.version,
            "product_role": product.product_role,
            "preliminary": product.preliminary,
            "coverage_level": COVERAGE_LEVEL,
            "candidate_status": candidate_status,
            "operational_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                **operational,
            },
            secondary_name: secondary,
            "technical_decision": decision,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": {
            "source": str(geometry_file),
            "bbox": list(spatial.bbox),
            "spatial_query": spatial.query_type,
            "fallback_reason": spatial.fallback_reason,
        },
        "sources": sources,
        "limitations": [LIMITATION],
    }


def calculate_age_days(observed_at: datetime, generated_at: datetime) -> int:
    """Retorna idade inteira em dias, sem permitir idade negativa."""
    observed = observed_at.astimezone(timezone.utc)
    generated = generated_at.astimezone(timezone.utc)
    return max(0, (generated.date() - observed.date()).days)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _product_from_granule_id(granule_id: str) -> Product:
    if granule_id.upper().startswith("ATL08QL_"):
        return PRODUCTS[2]
    if granule_id.upper().startswith("ATL08_"):
        return PRODUCTS[1]
    raise ValueError("Only ATL08 and ATL08QL granules are supported.")


def _observation_key(granule_id: str) -> str | None:
    match = re.match(
        r"ATL08(?:QL)?_(\d{14})_(\d{8})_007_\d{2}(?:\.h5)?$",
        Path(granule_id).name,
        flags=re.IGNORECASE,
    )
    return f"{match.group(1)}_{match.group(2)}" if match else None


def _read_numeric_dataset(dataset) -> np.ndarray:
    values = np.asarray(dataset[...], dtype=float)
    for attribute_name in ("_FillValue", "fillvalue"):
        fill_value = dataset.attrs.get(attribute_name)
        if fill_value is not None:
            values[np.isclose(values, float(np.asarray(fill_value).flat[0]))] = np.nan
    values[np.abs(values) > 1e30] = np.nan
    return values


def _rate_matrix(values: np.ndarray) -> np.ndarray:
    if values.ndim == 1:
        return values.reshape(-1, 1)
    if values.ndim != 2:
        raise ValueError("ICESat-2 geolocation dataset must be one- or two-dimensional.")
    if values.shape[1] == 5:
        return values
    if values.shape[0] == 5:
        return values.T
    return values


def _align_matrix(values: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray:
    if values is None:
        return np.full(shape, np.nan)
    matrix = _rate_matrix(values)
    if matrix.shape == shape:
        return matrix
    if matrix.T.shape == shape:
        return matrix.T
    if matrix.shape == (shape[0], 1):
        return np.repeat(matrix, shape[1], axis=1)
    if matrix.size == shape[0] * shape[1]:
        return matrix.reshape(shape)
    raise ValueError("ICESat-2 datasets use incompatible segment dimensions.")


def _optional_dataset(group, relative_path: str) -> np.ndarray | None:
    dataset = group.get(relative_path)
    return _read_numeric_dataset(dataset) if dataset is not None else None


def _quality_for_segment(
    terrain_height: float | None,
    vegetation_height: float | None,
    raw_flags: Mapping[str, int | float | None],
) -> tuple[str, list[str]]:
    """Aplica apenas rejeicoes documentaveis; nao cria score de qualidade."""
    reasons: list[str] = []
    if terrain_height is None and vegetation_height is None:
        reasons.append("no_valid_vertical_metric")
    if raw_flags.get("terrain_flg") == 1:
        reasons.append("terrain_deviation_above_threshold")
    if raw_flags.get("msw_flag") in {3, 4, 5}:
        reasons.append("low_altitude_scattering_or_blowing_snow")
    if reasons:
        return "quality_rejected", reasons
    known_flags = any(value is not None for value in raw_flags.values())
    return ("usable_candidate" if known_flags else "unknown_quality"), []


def _finite_or_none(value: float) -> float | None:
    return round(float(value), 6) if math.isfinite(float(value)) else None


def _projected_aoi(geometry):
    centroid = geometry.centroid
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={centroid.y} +lon_0={centroid.x} "
        "+datum=WGS84 +units=m +no_defs"
    )
    transformer = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    return transform(transformer.transform, geometry), transformer


def classify_spatial_relation(
    longitude: float,
    latitude: float,
    projected_geometry,
    transformer: Transformer,
    near_distance_meters: float,
) -> tuple[str, float]:
    x, y = transformer.transform(longitude, latitude)
    point = Point(x, y)
    if projected_geometry.covers(point):
        return "inside_aoi", 0.0
    distance = float(projected_geometry.distance(point))
    if distance <= near_distance_meters:
        return "near_aoi", round(distance, 3)
    return "outside_relevance", round(distance, 3)


def diagnostic_distance_class(distance_meters: float | None) -> str | None:
    if distance_meters is None:
        return None
    if distance_meters <= 50:
        return "VERY_CLOSE"
    if distance_meters <= 200:
        return "CLOSE"
    if distance_meters <= 1000:
        return "MODERATE_DISTANCE"
    return "DISTANT"


def _keep_nearest(
    nearest_segments: list[dict[str, object]],
    segment: dict[str, object],
    *,
    limit: int = 5,
) -> None:
    nearest_segments.append(segment)
    nearest_segments.sort(key=lambda item: (
        float(item["distance_to_aoi_m"]),
        str(item.get("granule_id", "")),
        str(item.get("beam", "")),
        float(item.get("latitude", 0)),
        float(item.get("longitude", 0)),
    ))
    del nearest_segments[limit:]


def _beam_segment_arrays(
    beam_group,
) -> tuple[dict[str, np.ndarray], int, list[str], str | None, str]:
    land_segments = beam_group.get("land_segments")
    if land_segments is None:
        raise ValueError("Beam does not contain land_segments.")

    latitude_20 = _optional_dataset(land_segments, "latitude_20m")
    longitude_20 = _optional_dataset(land_segments, "longitude_20m")
    terrain_20 = _optional_dataset(land_segments, "terrain/h_te_best_fit_20m")
    vegetation_20 = _optional_dataset(land_segments, "canopy/h_canopy_20m")
    use_20m = all(
        values is not None
        for values in (latitude_20, longitude_20, terrain_20, vegetation_20)
    )
    if use_20m:
        latitude = _rate_matrix(latitude_20)
        longitude = _align_matrix(longitude_20, latitude.shape)
        support = 20
        paths = [
            "latitude_20m",
            "longitude_20m",
            "terrain/h_te_best_fit_20m",
            "canopy/h_canopy_20m",
        ]
        terrain_raw = terrain_20
        vegetation_raw = vegetation_20
        fallback_reason = None
    else:
        latitude_raw = _optional_dataset(land_segments, "latitude")
        longitude_raw = _optional_dataset(land_segments, "longitude")
        if latitude_raw is None or longitude_raw is None:
            raise ValueError("Beam does not contain supported geolocation datasets.")
        latitude = _rate_matrix(latitude_raw)
        longitude = _align_matrix(longitude_raw, latitude.shape)
        support = 100
        paths = ["latitude", "longitude"]
        terrain_path = "terrain/h_te_best_fit"
        vegetation_path = "canopy/h_canopy"
        terrain_raw = _optional_dataset(land_segments, terrain_path)
        vegetation_raw = _optional_dataset(land_segments, vegetation_path)
        if terrain_raw is not None:
            paths.append(terrain_path)
        if vegetation_raw is not None:
            paths.append(vegetation_path)
        fallback_reason = "20m_datasets_unavailable"
    arrays = {
        "latitude": latitude,
        "longitude": longitude,
        "terrain_height": _align_matrix(terrain_raw, latitude.shape),
        "vegetation_height": _align_matrix(vegetation_raw, latitude.shape),
    }
    has_native_20m_flag = False
    for flag_name in (
        "terrain_flg",
        "cloud_flag_atm",
        "msw_flag",
        "night_flag",
        "urban_flag",
    ):
        flag_values = _optional_dataset(land_segments, flag_name)
        arrays[flag_name] = _align_matrix(flag_values, latitude.shape)
        if flag_values is not None:
            paths.append(flag_name)
            has_native_20m_flag = has_native_20m_flag or (
                support == 20 and _rate_matrix(flag_values).shape == latitude.shape
            )
    if support == 100:
        quality_strategy = "native_100m_flags"
    elif has_native_20m_flag:
        quality_strategy = "native_20m_flags_when_available_parent_100m_otherwise"
    else:
        quality_strategy = "parent_100m_flags_applied_to_20m_children"
    return arrays, support, paths, fallback_reason, quality_strategy


def parse_icesat2_hdf5(
    path: str | Path,
    *,
    geometry,
    observed_at: datetime,
    generated_at: datetime,
    near_distance_meters: float = DEFAULT_NEAR_DISTANCE_METERS,
) -> dict[str, object]:
    """Extrai apenas segmentos ATL08/ATL08QL espacialmente relevantes."""
    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError("h5py is required for ICESat-2 segment validation.") from exc

    granule_path = Path(path)
    product = _product_from_granule_id(granule_path.name)
    projected_geometry, transformer = _projected_aoi(geometry)
    relevant_segments: list[dict[str, object]] = []
    nearest_segments: list[dict[str, object]] = []
    beam_schemas: dict[str, object] = {}
    segment_total = 0
    inside_count = 0
    near_count = 0

    with h5py.File(granule_path, "r") as handle:
        for beam in BEAMS:
            if beam not in handle:
                continue
            try:
                (
                    arrays,
                    spatial_support,
                    datasets_used,
                    fallback_reason,
                    quality_flag_strategy,
                ) = _beam_segment_arrays(handle[beam])
            except ValueError as exc:
                beam_schemas[beam] = {"status": "unsupported_schema", "message": str(exc)}
                continue
            beam_schemas[beam] = {
                "status": "parsed",
                "spatial_support_m": spatial_support,
                "fallback_reason": fallback_reason,
                "quality_flag_strategy": quality_flag_strategy,
                "datasets_used": datasets_used,
            }
            rows, columns = arrays["latitude"].shape
            for row in range(rows):
                for column in range(columns):
                    latitude = float(arrays["latitude"][row, column])
                    longitude = float(arrays["longitude"][row, column])
                    if not (
                        math.isfinite(latitude)
                        and math.isfinite(longitude)
                        and -90 <= latitude <= 90
                        and -180 <= longitude <= 180
                    ):
                        continue
                    segment_total += 1
                    relation, distance = classify_spatial_relation(
                        longitude,
                        latitude,
                        projected_geometry,
                        transformer,
                        near_distance_meters,
                    )
                    is_nearest_candidate = (
                        len(nearest_segments) < 5
                        or distance <= float(
                            nearest_segments[-1]["distance_to_aoi_m"]
                        )
                    )
                    if relation == "outside_relevance" and not is_nearest_candidate:
                        continue
                    terrain_height = _finite_or_none(
                        arrays["terrain_height"][row, column]
                    )
                    vegetation_height = _finite_or_none(
                        arrays["vegetation_height"][row, column]
                    )
                    raw_flags = {
                        flag_name: _finite_or_none(arrays[flag_name][row, column])
                        for flag_name in (
                            "terrain_flg",
                            "cloud_flag_atm",
                            "msw_flag",
                            "night_flag",
                            "urban_flag",
                        )
                    }
                    quality_status, quality_reasons = _quality_for_segment(
                        terrain_height,
                        vegetation_height,
                        raw_flags,
                    )
                    nearest_segment = {
                        "granule_id": granule_path.name,
                        "product": product.short_name,
                        "beam": beam,
                        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                        "latitude": round(latitude, 8),
                        "longitude": round(longitude, 8),
                        "distance_to_aoi_m": distance,
                        "spatial_support_m": spatial_support,
                        "terrain_height_m": terrain_height,
                        "vegetation_height_m": vegetation_height,
                        "quality_status": quality_status,
                    }
                    if is_nearest_candidate:
                        _keep_nearest(nearest_segments, nearest_segment)
                    if relation == "outside_relevance":
                        continue
                    inside_count += relation == "inside_aoi"
                    near_count += relation == "near_aoi"
                    relevant_segments.append({
                        "product": product.short_name,
                        "product_role": product.product_role,
                        "preliminary": product.preliminary,
                        "granule_id": granule_path.name,
                        "beam": beam,
                        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
                        "age_days": calculate_age_days(observed_at, generated_at),
                        "latitude": round(latitude, 8),
                        "longitude": round(longitude, 8),
                        "spatial_relation": relation,
                        "distance_to_aoi_m": distance,
                        "spatial_support_m": spatial_support,
                        "fallback_reason": fallback_reason,
                        "terrain_height_m": terrain_height,
                        "vegetation_height_m": vegetation_height,
                        "quality_status": quality_status,
                        "quality": raw_flags,
                        "quality_reasons": quality_reasons,
                        "superseded_by_final": False,
                    })
    usable = sum(
        segment["quality_status"] == "usable_candidate"
        for segment in relevant_segments
    )
    return {
        "granule_id": granule_path.name,
        "product": product.short_name,
        "product_role": product.product_role,
        "preliminary": product.preliminary,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "segments_total": segment_total,
        "segments_inside_aoi": inside_count,
        "segments_near_aoi": near_count,
        "segments_outside_relevance": segment_total - inside_count - near_count,
        "minimum_distance_to_aoi_m": (
            nearest_segments[0]["distance_to_aoi_m"] if nearest_segments else None
        ),
        "nearest_segments": nearest_segments,
        "usable_segments": usable,
        "relevant_segments": relevant_segments,
        "schema": {"beams": beam_schemas},
    }


def _earthdata_credential_strategy() -> str | None:
    token = bool(os.environ.get("EARTHDATA_TOKEN"))
    user_and_password = bool(
        os.environ.get("EARTHDATA_USERNAME")
        and os.environ.get("EARTHDATA_PASSWORD")
    )
    if token or user_and_password:
        return "environment"
    if (Path.home() / ".netrc").exists() or (Path.home() / "_netrc").exists():
        return "netrc"
    return None


def _authenticate_earthdata():
    strategy = _earthdata_credential_strategy()
    if strategy is None:
        return {"status": "auth_required", "strategy": None}, None
    try:
        import earthaccess

        authentication = earthaccess.login(strategy=strategy)
        if not getattr(authentication, "authenticated", False):
            return {"status": "auth_required", "strategy": strategy}, None
        return {"status": "authenticated", "strategy": strategy}, earthaccess
    except Exception as exc:
        return {
            "status": "authentication_error",
            "strategy": strategy,
            "error_type": type(exc).__name__,
            "message": "Earthdata authentication failed.",
        }, None


def _candidate_sample(source: Mapping[str, object]) -> tuple[int | None, list[dict]]:
    period_name = (
        "diagnostic_recent_period"
        if source.get("short_name") == "ATL08QL"
        else "historical"
    )
    period = source.get(period_name, {})
    if not isinstance(period, Mapping):
        return None, []
    count = period.get("candidate_granule_count")
    sample = period.get("sample", [])
    candidates = [entry for entry in sample if isinstance(entry, dict)]
    candidates.sort(
        key=lambda entry: str(entry.get("beginning_datetime") or ""),
        reverse=True,
    )
    return count if isinstance(count, int) else None, candidates


def _empty_validation(
    source: Mapping[str, object],
    authentication_status: str,
) -> dict[str, object]:
    candidate_count, _ = _candidate_sample(source)
    if candidate_count == 0:
        decision = "NO_RELEVANT_SEGMENTS"
    elif authentication_status == "auth_required":
        decision = "AUTH_REQUIRED"
    else:
        decision = "UNABLE_TO_VERIFY"
    return {
        "product_role": source.get("product_role"),
        "preliminary": source.get("preliminary"),
        "candidate_granules": candidate_count,
        "granules_inspected": 0,
        "segments_total": 0,
        "segments_inside_aoi": 0,
        "segments_near_aoi": 0,
        "segments_outside_relevance": 0,
        "minimum_distance_to_aoi_m": None,
        "diagnostic_distance_class": None,
        "nearest_segments": [],
        "usable_segments": 0,
        "latest_usable_observation": None,
        "granules": [],
        "decision": decision,
    }


def _summarize_product_validation(
    source: Mapping[str, object],
    granules: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    candidate_count, _ = _candidate_sample(source)
    relevant_segments = [
        segment
        for granule in granules
        for segment in granule.get("relevant_segments", [])
        if isinstance(segment, Mapping)
    ]
    usable_segments = [
        segment
        for segment in relevant_segments
        if segment.get("quality_status") == "usable_candidate"
        and not segment.get("superseded_by_final")
    ]
    latest = max(
        (str(segment.get("observed_at")) for segment in usable_segments),
        default=None,
    )
    nearest_segments: list[dict[str, object]] = []
    for granule in granules:
        for segment in granule.get("nearest_segments", []):
            if isinstance(segment, dict):
                _keep_nearest(nearest_segments, dict(segment))
    minimum_distance = (
        float(nearest_segments[0]["distance_to_aoi_m"])
        if nearest_segments
        else None
    )
    serialized_granules = [
        {key: value for key, value in granule.items() if key != "nearest_segments"}
        for granule in granules
    ]
    if usable_segments:
        decision = "GO_TO_PROVIDER_IMPLEMENTATION"
    elif granules:
        decision = "NO_RELEVANT_SEGMENTS_IN_SAMPLE"
    elif candidate_count == 0:
        decision = "NO_RELEVANT_SEGMENTS"
    else:
        decision = "UNABLE_TO_VERIFY"
    return {
        "product_role": source.get("product_role"),
        "preliminary": source.get("preliminary"),
        "candidate_granules": candidate_count,
        "granules_inspected": len(granules),
        "segments_total": sum(int(item.get("segments_total", 0)) for item in granules),
        "segments_inside_aoi": sum(
            int(item.get("segments_inside_aoi", 0)) for item in granules
        ),
        "segments_near_aoi": sum(
            int(item.get("segments_near_aoi", 0)) for item in granules
        ),
        "segments_outside_relevance": sum(
            int(item.get("segments_outside_relevance", 0)) for item in granules
        ),
        "minimum_distance_to_aoi_m": minimum_distance,
        "diagnostic_distance_class": diagnostic_distance_class(minimum_distance),
        "nearest_segments": nearest_segments,
        "usable_segments": len(usable_segments),
        "latest_usable_observation": latest,
        "granules": serialized_granules,
        "decision": decision,
    }


def validate_icesat2_segments(
    discovery_report: Mapping[str, object],
    geometry_file: str | Path,
    *,
    max_granules: int = DEFAULT_MAX_GRANULES,
    near_distance_meters: float = DEFAULT_NEAR_DISTANCE_METERS,
    generated_at: datetime | None = None,
    authentication: tuple[Mapping[str, object], object | None] | None = None,
) -> dict[str, object]:
    """Baixa uma amostra autenticada e valida segmentos reais sem acoplar providers."""
    generated = generated_at or datetime.now(timezone.utc)
    sources = discovery_report.get("sources", {})
    if not isinstance(sources, Mapping):
        raise ValueError("Discovery report does not contain sources.")
    auth_status, earthaccess_module = authentication or _authenticate_earthdata()
    status = str(auth_status.get("status"))
    result: dict[str, object] = {
        "coverage_level": "actual_icesat2_segment_validation",
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "authentication": dict(auth_status),
        "near_distance_meters": near_distance_meters,
        "max_granules": max_granules,
        "atl08": _empty_validation(sources.get("icesat2_atl08", {}), status),
        "atl08ql": _empty_validation(sources.get("icesat2_atl08ql", {}), status),
        "download_bytes": 0,
        "quality_rule": {
            "usable_candidate": (
                "At least one finite terrain/vegetation metric, recognized quality "
                "flags, terrain_flg != 1, and msw_flag not in 3,4,5."
            ),
            "quality_rejected": (
                "No finite vertical metric, terrain deviation above threshold, or "
                "low-altitude scattering/blowing-snow flag."
            ),
            "unknown_quality": "Finite metric without recognized quality flags.",
        },
        "limitations": [VERTICAL_EVIDENCE_LIMITATION, SAMPLE_LIMITATION],
    }
    if earthaccess_module is None:
        return result

    geometry = load_aoi_geometry(geometry_file)
    parsed_by_product: dict[str, list[dict[str, object]]] = {
        "atl08": [],
        "atl08ql": [],
    }
    with tempfile.TemporaryDirectory(prefix="motiva-icesat2-probe-") as temporary:
        temporary_path = Path(temporary)
        for result_key, source_key in (
            ("atl08", "icesat2_atl08"),
            ("atl08ql", "icesat2_atl08ql"),
        ):
            source = sources.get(source_key, {})
            _, candidates = _candidate_sample(source)
            candidates = [
                candidate
                for candidate in candidates[:max_granules]
                if candidate.get("download_url")
            ]
            if not candidates:
                continue
            candidate_by_name = {
                Path(str(candidate["download_url"])).name: candidate
                for candidate in candidates
            }
            for candidate in candidates:
                try:
                    downloaded = earthaccess_module.download(
                        [str(candidate["download_url"])],
                        local_path=temporary_path,
                        threads=1,
                        show_progress=False,
                    )
                except Exception as exc:
                    parsed_by_product[result_key].append({
                        "granule_id": candidate.get("granule_id"),
                        "segments_total": 0,
                        "segments_inside_aoi": 0,
                        "segments_near_aoi": 0,
                        "segments_outside_relevance": 0,
                        "minimum_distance_to_aoi_m": None,
                        "nearest_segments": [],
                        "usable_segments": 0,
                        "relevant_segments": [],
                        "error_type": type(exc).__name__,
                        "message": "Earthdata download failed.",
                    })
                    continue
                if not downloaded:
                    continue
                downloaded_path = downloaded[0]
                path = Path(downloaded_path)
                if not path.exists():
                    continue
                result["download_bytes"] = int(result["download_bytes"]) + path.stat().st_size
                metadata = candidate_by_name.get(path.name, {})
                observed_at = _parse_datetime(metadata.get("beginning_datetime"))
                if observed_at is None:
                    continue
                try:
                    parsed_by_product[result_key].append(parse_icesat2_hdf5(
                        path,
                        geometry=geometry,
                        observed_at=observed_at,
                        generated_at=generated,
                        near_distance_meters=near_distance_meters,
                    ))
                except Exception as exc:
                    parsed_by_product[result_key].append({
                        "granule_id": path.name,
                        "segments_total": 0,
                        "segments_inside_aoi": 0,
                        "segments_near_aoi": 0,
                        "segments_outside_relevance": 0,
                        "minimum_distance_to_aoi_m": None,
                        "nearest_segments": [],
                        "usable_segments": 0,
                        "relevant_segments": [],
                        "error_type": type(exc).__name__,
                        "message": "ICESat-2 HDF5 parsing failed.",
                    })
                if parsed_by_product[result_key][-1].get("usable_segments", 0):
                    break

    final_keys = {
        key
        for granule in parsed_by_product["atl08"]
        if (key := _observation_key(str(granule.get("granule_id", ""))))
    }
    for granule in parsed_by_product["atl08ql"]:
        observation_key = _observation_key(str(granule.get("granule_id", "")))
        if observation_key in final_keys:
            for segment in granule.get("relevant_segments", []):
                segment["superseded_by_final"] = True

    result["atl08"] = _summarize_product_validation(
        sources.get("icesat2_atl08", {}),
        parsed_by_product["atl08"],
    )
    result["atl08ql"] = _summarize_product_validation(
        sources.get("icesat2_atl08ql", {}),
        parsed_by_product["atl08ql"],
    )
    return result


def _one_calendar_month_before(value: date) -> date:
    previous_month = 12 if value.month == 1 else value.month - 1
    previous_year = value.year - 1 if value.month == 1 else value.year
    day = min(value.day, calendar.monthrange(previous_year, previous_month)[1])
    return date(previous_year, previous_month, day)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use date format YYYY-MM-DD.") from exc


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe NASA CMR candidate granules for a GeoJSON AOI."
    )
    parser.add_argument("--geometry-file", required=True, type=Path)
    parser.add_argument("--start-date", type=_parse_date)
    parser.add_argument("--end-date", type=_parse_date)
    parser.add_argument(
        "--max-granules",
        type=int,
        default=DEFAULT_MAX_GRANULES,
        help="Maximum recent ATL08 and ATL08QL granules inspected per product.",
    )
    parser.add_argument(
        "--near-distance-meters",
        type=float,
        default=DEFAULT_NEAR_DISTANCE_METERS,
        help="Diagnostic distance around the AOI used to classify near segments.",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if (arguments.start_date is None) != (arguments.end_date is None):
        parser.error("--start-date and --end-date must be provided together")
    if arguments.max_granules < 1:
        parser.error("--max-granules must be at least 1")
    if arguments.near_distance_meters < 0:
        parser.error("--near-distance-meters cannot be negative")
    return arguments


def main() -> int:
    arguments = _arguments()
    end_date = arguments.end_date or datetime.now(
        ZoneInfo(OPERATIONAL_TIMEZONE)
    ).date()
    start_date = arguments.start_date or _one_calendar_month_before(end_date)
    if start_date > end_date:
        raise SystemExit("start date cannot be after end date")
    report = probe_coverage(
        arguments.geometry_file,
        start_date,
        end_date,
    )
    report["icesat2_segment_validation"] = validate_icesat2_segments(
        report,
        arguments.geometry_file,
        max_granules=arguments.max_granules,
        near_distance_meters=arguments.near_distance_meters,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
