"""Validacao diagnostica de footprints reais GEDI02_A V002."""

from __future__ import annotations

import argparse
import calendar
import json
import math
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.probe_nasa_coverage import (
    COVERAGE_LEVEL,
    DEFAULT_MAX_GRANULES,
    DEFAULT_NEAR_DISTANCE_METERS,
    DEFAULT_TIMEOUT_SECONDS,
    LIMITATION,
    OPERATIONAL_TIMEZONE,
    PRODUCTS,
    _authenticate_earthdata,
    _candidate_sample,
    _default_http_get,
    _keep_nearest,
    _parse_datetime,
    _projected_aoi,
    _query_product,
    _technical_decision,
    diagnostic_distance_class,
    load_aoi,
    load_aoi_geometry,
)

GEDI_EPOCH = datetime(2018, 1, 1, tzinfo=timezone.utc)
GEDI_MAX_GRANULES = min(3, DEFAULT_MAX_GRANULES)
GEDI_RH_INDICES = (25, 50, 75, 90, 95, 98, 100)
GEDI_VERTICAL_LIMITATION = (
    "GEDI RH metrics and elevation differences are auxiliary structural evidence "
    "and must not be interpreted as roadside grass height."
)
GEDI_SAMPLE_LIMITATION = (
    "This validation inspects at most three recent historical granules and cannot "
    "establish absence of GEDI coverage across the complete archive."
)


def probe_gedi_discovery(
    geometry_file: str | Path,
    start_date: date,
    end_date: date,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Executa somente o discovery GEDI02_A operacional e historico."""
    spatial = load_aoi(geometry_file)
    product = PRODUCTS[0]
    operational = _query_product(
        product,
        spatial,
        start_date=start_date,
        end_date=end_date,
        timeout=timeout,
        http_get=_default_http_get,
    )
    historical = _query_product(
        product,
        spatial,
        start_date=None,
        end_date=None,
        timeout=timeout,
        http_get=_default_http_get,
    )
    candidate_status, decision = _technical_decision(operational, historical)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": {
            "source": str(geometry_file),
            "bbox": list(spatial.bbox),
            "spatial_query": spatial.query_type,
            "fallback_reason": spatial.fallback_reason,
        },
        "sources": {
            "gedi_l2a": {
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
                "historical": historical,
                "technical_decision": decision,
            }
        },
        "limitations": [LIMITATION],
    }


def _numeric_array(group, path: str) -> np.ndarray | None:
    dataset = group.get(path)
    if dataset is None or not hasattr(dataset, "shape"):
        return None
    values = np.asarray(dataset[...], dtype=float)
    for attribute_name in ("_FillValue", "fillvalue"):
        fill_value = dataset.attrs.get(attribute_name)
        if fill_value is not None:
            fill = float(np.asarray(fill_value).flat[0])
            values[np.isclose(values, fill)] = np.nan
    values[np.abs(values) > 1e30] = np.nan
    return values


def _numeric_vector(group, path: str, count: int | None = None) -> np.ndarray | None:
    values = _numeric_array(group, path)
    if values is None:
        return None
    values = np.squeeze(values)
    if values.ndim == 0:
        values = values.reshape(1)
    if values.ndim != 1 or (count is not None and values.size != count):
        return None
    return values


def _shot_vector(group, count: int) -> np.ndarray | None:
    dataset = group.get("shot_number")
    if dataset is None or not hasattr(dataset, "shape"):
        return None
    values = np.squeeze(np.asarray(dataset[...]))
    if values.ndim == 0:
        values = values.reshape(1)
    if values.ndim != 1 or values.size != count:
        return None
    return values


def _rh_matrix(group, count: int) -> np.ndarray | None:
    values = _numeric_array(group, "rh")
    if values is None:
        return None
    if values.ndim == 1 and count == 1:
        return values.reshape(1, -1)
    if values.ndim != 2:
        return None
    if values.shape[0] == count:
        return values
    if values.shape[1] == count:
        return values.T
    return None


def _gedi_beam_arrays(beam_group) -> tuple[dict[str, np.ndarray | None], dict[str, object]]:
    coordinate_options = (
        ("lat_lowestmode", "lon_lowestmode"),
        ("lat_highestreturn", "lon_highestreturn"),
    )
    latitude = longitude = None
    coordinate_paths: tuple[str, str] | None = None
    for latitude_path, longitude_path in coordinate_options:
        candidate_latitude = _numeric_vector(beam_group, latitude_path)
        if candidate_latitude is None:
            continue
        candidate_longitude = _numeric_vector(
            beam_group, longitude_path, candidate_latitude.size
        )
        if candidate_longitude is not None:
            latitude = candidate_latitude
            longitude = candidate_longitude
            coordinate_paths = (latitude_path, longitude_path)
            break
    if latitude is None or longitude is None or coordinate_paths is None:
        raise ValueError("Beam does not contain aligned GEDI footprint coordinates.")

    count = latitude.size
    paths = [*coordinate_paths]
    arrays: dict[str, np.ndarray | None] = {
        "latitude": latitude,
        "longitude": longitude,
        "shot_number": _shot_vector(beam_group, count),
    }
    for name in (
        "delta_time",
        "quality_flag",
        "degrade_flag",
        "sensitivity",
        "elev_lowestmode",
        "elev_highestreturn",
    ):
        arrays[name] = _numeric_vector(beam_group, name, count)
    arrays["rh"] = _rh_matrix(beam_group, count)
    for name in (
        "shot_number",
        "delta_time",
        "quality_flag",
        "degrade_flag",
        "sensitivity",
        "elev_lowestmode",
        "elev_highestreturn",
        "rh",
    ):
        if arrays[name] is not None:
            paths.append(name)
    return arrays, {
        "status": "parsed",
        "footprint_count_in_dataset": int(count),
        "coordinate_datasets": list(coordinate_paths),
        "datasets_used": paths,
        "rh_indices_available": (
            [index for index in GEDI_RH_INDICES if index < arrays["rh"].shape[1]]
            if arrays["rh"] is not None
            else []
        ),
    }


def _finite_value(values: np.ndarray | None, index: int) -> float | None:
    if values is None:
        return None
    value = float(values[index])
    return round(value, 6) if math.isfinite(value) else None


def _flag_value(values: np.ndarray | None, index: int) -> int | float | None:
    value = _finite_value(values, index)
    if value is None:
        return None
    return int(value) if value.is_integer() else value


def _shot_value(values: np.ndarray | None, index: int) -> int | str | None:
    if values is None:
        return None
    value = values[index]
    if isinstance(value, (bytes, np.bytes_)):
        decoded = value.decode("utf-8", errors="replace")
        return decoded or None
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return integer if integer >= 0 else None


def _observed_at(delta_time: float | None, fallback: datetime | None) -> str | None:
    if delta_time is not None and delta_time >= 0:
        try:
            return (GEDI_EPOCH + timedelta(seconds=delta_time)).isoformat()
        except OverflowError:
            pass
    if fallback is None:
        return None
    return fallback.astimezone(timezone.utc).isoformat()


def classify_gedi_quality(
    quality_flag: int | float | None,
    degrade_flag: int | float | None,
) -> tuple[str, list[str]]:
    """Classifica somente os dois flags GEDI documentados, sem score sintetico."""
    if quality_flag == 1 and degrade_flag == 0:
        return "usable_candidate", []
    reasons: list[str] = []
    if quality_flag == 0:
        reasons.append("quality_flag_not_valid")
    if degrade_flag is not None and degrade_flag > 0:
        reasons.append("degrade_flag_indicates_degraded_state")
    if reasons:
        return "quality_rejected", reasons
    return "unknown_quality", ["quality_flags_missing_or_unrecognized"]


def _projected_distances(
    longitudes: np.ndarray,
    latitudes: np.ndarray,
    projected_geometry,
    transformer,
) -> tuple[np.ndarray, np.ndarray]:
    x_values, y_values = transformer.transform(longitudes, latitudes)
    try:
        import shapely

        points = shapely.points(x_values, y_values)
        inside = np.asarray(shapely.covers(projected_geometry, points), dtype=bool)
        distances = np.asarray(shapely.distance(projected_geometry, points), dtype=float)
        distances[inside] = 0.0
        return inside, distances
    except (AttributeError, TypeError):
        from shapely.geometry import Point

        inside_values = []
        distance_values = []
        for x_value, y_value in zip(x_values, y_values):
            point = Point(float(x_value), float(y_value))
            is_inside = projected_geometry.covers(point)
            inside_values.append(is_inside)
            distance_values.append(0.0 if is_inside else projected_geometry.distance(point))
        return np.asarray(inside_values), np.asarray(distance_values, dtype=float)


def _footprint_record(
    arrays: Mapping[str, np.ndarray | None],
    index: int,
    *,
    granule_id: str,
    beam: str,
    fallback_observed_at: datetime | None,
    latitude: float,
    longitude: float,
    distance: float,
    spatial_relation: str,
) -> dict[str, object]:
    delta_time = _finite_value(arrays["delta_time"], index)
    quality_flag = _flag_value(arrays["quality_flag"], index)
    degrade_flag = _flag_value(arrays["degrade_flag"], index)
    quality_status, quality_reasons = classify_gedi_quality(
        quality_flag, degrade_flag
    )
    record: dict[str, object] = {
        "granule_id": granule_id,
        "beam": beam,
        "shot_number": _shot_value(arrays["shot_number"], index),
        "observed_at": _observed_at(delta_time, fallback_observed_at),
        "delta_time": delta_time,
        "latitude": round(latitude, 8),
        "longitude": round(longitude, 8),
        "spatial_relation": spatial_relation,
        "distance_to_aoi_m": round(distance, 3),
        "quality_flag": quality_flag,
        "degrade_flag": degrade_flag,
        "sensitivity": _finite_value(arrays["sensitivity"], index),
        "elev_lowestmode": _finite_value(arrays["elev_lowestmode"], index),
        "elev_highestreturn": _finite_value(arrays["elev_highestreturn"], index),
        "quality_status": quality_status,
        "quality_reasons": quality_reasons,
    }
    rh = arrays["rh"]
    for rh_index in GEDI_RH_INDICES:
        if rh is not None and rh_index < rh.shape[1]:
            record[f"rh{rh_index}"] = _finite_value(rh[index], rh_index)
    return record


def parse_gedi_hdf5(
    path: str | Path,
    *,
    geometry,
    granule_observed_at: datetime | None = None,
    near_distance_meters: float = DEFAULT_NEAR_DISTANCE_METERS,
) -> dict[str, object]:
    """Le beams presentes e calcula a distancia de todo footprint com coordenada valida."""
    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError("h5py is required for GEDI footprint validation.") from exc

    granule_path = Path(path)
    projected_geometry, transformer = _projected_aoi(geometry)
    nearest: list[dict[str, object]] = []
    relevant: list[dict[str, object]] = []
    schemas: dict[str, object] = {}
    footprint_total = inside_total = near_total = 0
    observation_times: list[str] = []

    with h5py.File(granule_path, "r") as handle:
        beam_names = sorted(
            name
            for name in handle.keys()
            if name.upper().startswith("BEAM") and hasattr(handle[name], "keys")
        )
        for beam in beam_names:
            try:
                arrays, schema = _gedi_beam_arrays(handle[beam])
            except ValueError as exc:
                schemas[beam] = {"status": "unsupported_schema", "message": str(exc)}
                continue
            schemas[beam] = schema
            latitudes = arrays["latitude"]
            longitudes = arrays["longitude"]
            assert latitudes is not None and longitudes is not None
            valid = (
                np.isfinite(latitudes)
                & np.isfinite(longitudes)
                & (latitudes >= -90)
                & (latitudes <= 90)
                & (longitudes >= -180)
                & (longitudes <= 180)
            )
            valid_indices = np.flatnonzero(valid)
            if not valid_indices.size:
                continue
            valid_latitudes = latitudes[valid_indices]
            valid_longitudes = longitudes[valid_indices]
            inside, distances = _projected_distances(
                valid_longitudes,
                valid_latitudes,
                projected_geometry,
                transformer,
            )
            footprint_total += int(valid_indices.size)
            inside_total += int(np.count_nonzero(inside))
            near_mask = (~inside) & (distances <= near_distance_meters)
            near_total += int(np.count_nonzero(near_mask))
            delta_times = arrays["delta_time"]
            if delta_times is not None:
                valid_delta_times = delta_times[valid_indices]
                valid_delta_times = valid_delta_times[
                    np.isfinite(valid_delta_times) & (valid_delta_times >= 0)
                ]
                if valid_delta_times.size:
                    observation_times.extend(
                        value
                        for value in (
                            _observed_at(float(np.min(valid_delta_times)), None),
                            _observed_at(float(np.max(valid_delta_times)), None),
                        )
                        if value is not None
                    )
            elif granule_observed_at is not None:
                observation_times.append(
                    granule_observed_at.astimezone(timezone.utc).isoformat()
                )
            nearest_positions = np.argsort(distances, kind="stable")[:5]
            relevant_positions = np.flatnonzero(inside | near_mask)
            selected_positions = sorted(
                set(nearest_positions.tolist()) | set(relevant_positions.tolist())
            )
            nearest_set = set(nearest_positions.tolist())
            relevant_set = set(relevant_positions.tolist())
            for position in selected_positions:
                source_index = int(valid_indices[position])
                relation = (
                    "inside_aoi"
                    if inside[position]
                    else "near_aoi"
                    if near_mask[position]
                    else "outside_relevance"
                )
                record = _footprint_record(
                    arrays,
                    source_index,
                    granule_id=granule_path.name,
                    beam=beam,
                    fallback_observed_at=granule_observed_at,
                    latitude=float(valid_latitudes[position]),
                    longitude=float(valid_longitudes[position]),
                    distance=float(distances[position]),
                    spatial_relation=relation,
                )
                if position in nearest_set:
                    _keep_nearest(nearest, dict(record))
                if position in relevant_set:
                    relevant.append(record)

    usable = sum(
        footprint["quality_status"] == "usable_candidate" for footprint in relevant
    )
    return {
        "parse_status": "parsed",
        "granule_id": granule_path.name,
        "footprints_total": footprint_total,
        "footprints_inside_aoi": inside_total,
        "footprints_near_aoi": near_total,
        "footprints_within_50m": inside_total + near_total,
        "footprints_outside_relevance": footprint_total - inside_total - near_total,
        "usable_footprints": usable,
        "minimum_distance_to_aoi_m": (
            nearest[0]["distance_to_aoi_m"] if nearest else None
        ),
        "observed_at_start": min(observation_times, default=None),
        "observed_at_end": max(observation_times, default=None),
        "nearest_footprints": nearest,
        "relevant_footprints": relevant,
        "schema": {"beams": schemas},
    }


def _empty_validation(source: Mapping[str, object], auth_status: str) -> dict[str, object]:
    candidate_count, _ = _candidate_sample(source)
    decision = (
        "AUTH_REQUIRED"
        if auth_status == "auth_required" and candidate_count != 0
        else "NO_RELEVANT_FOOTPRINTS_IN_SAMPLE"
        if candidate_count == 0
        else "UNABLE_TO_VERIFY"
    )
    return {
        "candidate_granules": candidate_count,
        "granules_attempted": 0,
        "granules_inspected": 0,
        "footprints_total": 0,
        "footprints_inside_aoi": 0,
        "footprints_near_aoi": 0,
        "footprints_within_50m": 0,
        "footprints_outside_relevance": 0,
        "usable_footprints": 0,
        "minimum_distance_to_aoi_m": None,
        "diagnostic_distance_class": None,
        "nearest_footprints": [],
        "observation_dates": [],
        "granules": [],
        "decision": decision,
    }


def summarize_gedi_validation(
    source: Mapping[str, object],
    granules: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    candidate_count, _ = _candidate_sample(source)
    parsed = [item for item in granules if item.get("parse_status") == "parsed"]
    nearest: list[dict[str, object]] = []
    relevant: list[Mapping[str, object]] = []
    dates: set[str] = set()
    for granule in parsed:
        for footprint in granule.get("nearest_footprints", []):
            if isinstance(footprint, dict):
                _keep_nearest(nearest, dict(footprint))
        relevant.extend(
            item
            for item in granule.get("relevant_footprints", [])
            if isinstance(item, Mapping)
        )
        for key in ("observed_at_start", "observed_at_end"):
            value = granule.get(key)
            if isinstance(value, str) and value:
                dates.add(value[:10])
    usable = sum(item.get("quality_status") == "usable_candidate" for item in relevant)
    minimum_distance = (
        float(nearest[0]["distance_to_aoi_m"]) if nearest else None
    )
    if usable:
        decision = "GO_TO_PROVIDER_IMPLEMENTATION"
    elif parsed:
        decision = "NO_RELEVANT_FOOTPRINTS_IN_SAMPLE"
    else:
        decision = "UNABLE_TO_VERIFY"
    serialized = [
        {key: value for key, value in item.items() if key != "nearest_footprints"}
        for item in granules
    ]
    return {
        "candidate_granules": candidate_count,
        "granules_attempted": len(granules),
        "granules_inspected": len(parsed),
        "footprints_total": sum(int(item.get("footprints_total", 0)) for item in parsed),
        "footprints_inside_aoi": sum(
            int(item.get("footprints_inside_aoi", 0)) for item in parsed
        ),
        "footprints_near_aoi": sum(
            int(item.get("footprints_near_aoi", 0)) for item in parsed
        ),
        "footprints_within_50m": sum(
            int(item.get("footprints_inside_aoi", 0))
            + int(item.get("footprints_near_aoi", 0))
            for item in parsed
        ),
        "footprints_outside_relevance": sum(
            int(item.get("footprints_outside_relevance", 0)) for item in parsed
        ),
        "usable_footprints": usable,
        "minimum_distance_to_aoi_m": minimum_distance,
        "diagnostic_distance_class": diagnostic_distance_class(minimum_distance),
        "nearest_footprints": nearest,
        "observation_dates": sorted(dates),
        "granules": serialized,
        "decision": decision,
    }


def validate_gedi_footprints(
    discovery_report: Mapping[str, object],
    geometry_file: str | Path,
    *,
    max_granules: int = GEDI_MAX_GRANULES,
    near_distance_meters: float = DEFAULT_NEAR_DISTANCE_METERS,
    generated_at: datetime | None = None,
    authentication: tuple[Mapping[str, object], object | None] | None = None,
) -> dict[str, object]:
    """Baixa e valida no maximo tres granules GEDI historicos recentes."""
    if not 1 <= max_granules <= GEDI_MAX_GRANULES:
        raise ValueError(f"max_granules must be between 1 and {GEDI_MAX_GRANULES}.")
    sources = discovery_report.get("sources", {})
    if not isinstance(sources, Mapping):
        raise ValueError("Discovery report does not contain sources.")
    source = sources.get("gedi_l2a", {})
    if not isinstance(source, Mapping):
        source = {}
    auth, earthaccess_module = authentication or _authenticate_earthdata()
    status = str(auth.get("status"))
    result: dict[str, object] = {
        "coverage_level": "actual_gedi_footprint_validation",
        "generated_at": (generated_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(),
        "authentication": dict(auth),
        "product": "GEDI02_A",
        "version": "002",
        "near_distance_meters": near_distance_meters,
        "max_granules": max_granules,
        "download_bytes": 0,
        "quality_rule": {
            "usable_candidate": "quality_flag == 1 and degrade_flag == 0.",
            "quality_rejected": "quality_flag == 0 or degrade_flag > 0.",
            "unknown_quality": "A required flag is missing or has an unrecognized value.",
        },
        "limitations": [GEDI_VERTICAL_LIMITATION, GEDI_SAMPLE_LIMITATION],
        "gedi": _empty_validation(source, status),
    }
    if earthaccess_module is None:
        return result

    geometry = load_aoi_geometry(geometry_file)
    _, candidates = _candidate_sample(source)
    candidates = [
        candidate
        for candidate in candidates[:max_granules]
        if candidate.get("download_url")
    ]
    granules: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="motiva-gedi-probe-") as temporary:
        temporary_path = Path(temporary)
        for candidate in candidates:
            granule_id = str(candidate.get("granule_id") or "unknown_granule")
            try:
                downloaded = earthaccess_module.download(
                    [str(candidate["download_url"])],
                    local_path=temporary_path,
                    threads=1,
                    show_progress=False,
                )
                if not downloaded or not Path(downloaded[0]).exists():
                    raise FileNotFoundError("Earthdata did not return a local file.")
                path = Path(downloaded[0])
                size = path.stat().st_size
                result["download_bytes"] = int(result["download_bytes"]) + size
            except Exception as exc:
                granules.append({
                    "parse_status": "download_error",
                    "granule_id": granule_id,
                    "download_bytes": 0,
                    "error_type": type(exc).__name__,
                    "message": "Earthdata download failed.",
                })
                continue
            try:
                parsed = parse_gedi_hdf5(
                    path,
                    geometry=geometry,
                    granule_observed_at=_parse_datetime(
                        candidate.get("beginning_datetime")
                    ),
                    near_distance_meters=near_distance_meters,
                )
                parsed["download_bytes"] = size
                granules.append(parsed)
            except Exception as exc:
                granules.append({
                    "parse_status": "parsing_error",
                    "granule_id": path.name,
                    "download_bytes": size,
                    "error_type": type(exc).__name__,
                    "message": "GEDI HDF5 parsing failed.",
                })
    result["gedi"] = summarize_gedi_validation(source, granules)
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
    parser = argparse.ArgumentParser(description="Validate real GEDI02_A V002 footprints.")
    parser.add_argument("--geometry-file", required=True, type=Path)
    parser.add_argument("--start-date", type=_parse_date)
    parser.add_argument("--end-date", type=_parse_date)
    parser.add_argument("--max-granules", type=int, default=GEDI_MAX_GRANULES)
    parser.add_argument(
        "--near-distance-meters", type=float, default=DEFAULT_NEAR_DISTANCE_METERS
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if (arguments.start_date is None) != (arguments.end_date is None):
        parser.error("--start-date and --end-date must be provided together")
    if not 1 <= arguments.max_granules <= GEDI_MAX_GRANULES:
        parser.error(f"--max-granules must be between 1 and {GEDI_MAX_GRANULES}")
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
    report = probe_gedi_discovery(arguments.geometry_file, start_date, end_date)
    report["gedi_footprint_validation"] = validate_gedi_footprints(
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
