"""Probe diagnostico de candidate granules NASA CMR para uma AOI GeoJSON."""

from __future__ import annotations

import argparse
import calendar
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CMR_GRANULES_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_SAMPLE_SIZE = 5
OPERATIONAL_TIMEZONE = "America/Sao_Paulo"
COVERAGE_LEVEL = "granule_metadata_candidate"
LIMITATION = (
    "CMR granule intersection does not prove that a GEDI footprint or ATL08 "
    "along-track vegetation segment falls inside the AOI."
)


@dataclass(frozen=True)
class Product:
    key: str
    short_name: str
    version: str


@dataclass(frozen=True)
class SpatialQuery:
    query_type: str
    coordinates: tuple[float, ...]
    bbox: tuple[float, float, float, float]
    fallback_reason: str | None = None


PRODUCTS = (
    Product("gedi_l2a", "GEDI02_A", "002"),
    Product("icesat2_atl08", "ATL08", "007"),
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


def load_aoi(path: str | Path) -> SpatialQuery:
    geometry_path = Path(path)
    try:
        document = json.loads(geometry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"GeoJSON file not found: {geometry_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in GeoJSON file: {geometry_path}") from exc
    return extract_aoi_spatial_query(document)


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
        historical = _query_product(
            product,
            spatial,
            start_date=None,
            end_date=None,
            timeout=timeout,
            http_get=http_get,
        )
        candidate_status, decision = _technical_decision(operational, historical)
        sources[product.key] = {
            "short_name": product.short_name,
            "version": product.version,
            "coverage_level": COVERAGE_LEVEL,
            "candidate_status": candidate_status,
            "operational_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                **operational,
            },
            "historical": historical,
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
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if (arguments.start_date is None) != (arguments.end_date is None):
        parser.error("--start-date and --end-date must be provided together")
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
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
