"""Construcao offline de ground truth de campo enriquecido por Sentinel-2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from pyproj import Geod
from shapely.geometry import mapping

from ..datasets.field_schema import (
    MEASUREMENT_QUALITY_VALUES,
    FieldObservation,
)
from ..datasets.training_scenes import query_training_scenes
from ..geometry import extract_polygon_geometry, load_geojson_file
from .sentinel2_ablation import (
    HeightMaskRejectedError,
    read_multiband_height_features,
)
from .sentinel2_temporal import (
    TEMPORAL_LOOKBACK_DAYS,
    TemporalObservation,
    build_temporal_feature_stages,
    parse_scene_date,
    strictly_historical_records,
)

CAUSAL_SCENE_LOOKBACK_DAYS = 60
WAITING_FOR_FIELD_AOI_GEOJSON = "WAITING_FOR_FIELD_AOI_GEOJSON"

STATIC_FEATURE_COLUMNS = (
    "red_reflectance",
    "nir_reflectance",
    "ndvi",
    "red_edge_1_reflectance",
    "red_edge_2_reflectance",
    "red_edge_3_reflectance",
    "narrow_nir_reflectance",
    "swir1_reflectance",
    "swir2_reflectance",
    "ndre",
    "ndii",
)
TEMPORAL_FEATURE_COLUMNS = (
    "delta_ndvi",
    "delta_ndre",
    "delta_ndii",
    "delta_swir1",
    "delta_swir2",
    "ndvi_slope_30d",
    "ndvi_slope_60d",
    "ndre_slope_30d",
    "ndre_slope_60d",
    "ndii_slope_30d",
    "ndii_slope_60d",
    "recent_ndvi_drop",
    "days_since_recent_ndvi_drop",
    "ndvi_recovery_since_drop",
    "recent_ndre_drop",
)
MODEL_FEATURE_COLUMNS = STATIC_FEATURE_COLUMNS + TEMPORAL_FEATURE_COLUMNS

CSV_COLUMNS = (
    "sample_id",
    "observed_at",
    "road",
    "km",
    "side",
    "location_name",
    "latitude",
    "longitude",
    "geometry_id",
    "geometry_geojson",
    "area_m2",
    "measured_height_cm",
    "height_p50_cm",
    "height_p90_cm",
    "height_max_cm",
    "real_class_30cm",
    "boundary_case",
    "qualitative_condition",
    "measurement_quality",
    "field_measurement_status",
    "training_eligible",
    "external_validation",
    "source",
    "video_reference",
    "sentinel_item_id",
    "scene_date",
    "scene_age_days",
    "cloud_cover",
    "valid_pixel_percentage",
    "aoi_coverage_percentage",
    "scene_quality_score",
    "spectral_extraction_status",
    *STATIC_FEATURE_COLUMNS,
    "vegetation_fraction",
    "mixed_pixel_risk",
    "height_valid_pixel_count",
    "height_total_pixel_count",
    "temporal_status",
    "historical_scene_count",
    "previous_scene_date",
    "temporal_gap_days",
    *TEMPORAL_FEATURE_COLUMNS,
    "recommendation_context",
    "warnings",
)


@dataclass(frozen=True)
class RegressionReadinessConfig:
    min_confirmed_metric_samples: int = 30
    min_complete_static_samples: int = 20
    min_unique_locations: int = 10
    min_unique_dates: int = 3
    require_both_sides_of_30_cm: bool = True


def _optional_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Numeric field must be finite.")
    return number


def _optional_bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "sim"}:
        return True
    if normalized in {"false", "0", "no", "nao", "não"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO date.") from exc


def _parse_sequence(value: Any) -> tuple[Any, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError("Expected a JSON list.")
            return tuple(parsed)
        return tuple(part.strip() for part in stripped.split(";") if part.strip())
    if isinstance(value, Sequence):
        return tuple(value)
    raise ValueError("Expected a sequence.")


def _validate_embedded_crs(document: Mapping[str, Any]) -> None:
    crs = document.get("crs")
    if crs is None:
        return
    try:
        name = str(crs["properties"]["name"]).strip().upper()
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid GeoJSON CRS declaration; expected EPSG:4326.") from exc
    allowed = {
        "EPSG:4326",
        "OGC:CRS84",
        "CRS84",
        "URN:OGC:DEF:CRS:EPSG::4326",
        "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
    }
    if name not in allowed:
        raise ValueError(f"Field geometry must use EPSG:4326, not {name}.")


def _geometry_document(
    row: Mapping[str, Any], *, source_path: Path, geometry_dir: Path | None
) -> Mapping[str, Any] | None:
    raw = row.get("geometry")
    if isinstance(raw, Mapping):
        return raw
    if raw is not None and str(raw).strip():
        parsed = json.loads(str(raw))
        if not isinstance(parsed, Mapping):
            raise ValueError("Embedded geometry must be a GeoJSON object.")
        return parsed
    reference = next(
        (
            row.get(name)
            for name in ("geometry_reference", "geometry_path", "geometry_file")
            if row.get(name) is not None and str(row.get(name)).strip()
        ),
        None,
    )
    if reference is None:
        return None
    path = Path(str(reference))
    if not path.is_absolute():
        path = (geometry_dir or source_path.parent) / path
    return load_geojson_file(path)


def _normalize_geometry(
    row: Mapping[str, Any], *, source_path: Path, geometry_dir: Path | None
) -> Mapping[str, Any] | None:
    document = _geometry_document(row, source_path=source_path, geometry_dir=geometry_dir)
    if document is None:
        return None
    _validate_embedded_crs(document)
    geometry, count = extract_polygon_geometry(dict(document))
    if geometry.geom_type != "Polygon" or count != 1:
        raise ValueError("Field calibration geometry must be one Polygon.")
    return mapping(geometry)


def _real_class(height_p90_cm: float | None, measured_height_cm: float | None) -> str:
    value = height_p90_cm if height_p90_cm is not None else measured_height_cm
    if value is None:
        return "unknown"
    return "le_30_cm" if value <= 30 else "gt_30_cm"


def _normalize_row(
    raw: Mapping[str, Any], *, source_path: Path, geometry_dir: Path | None
) -> FieldObservation:
    sample_id = str(raw.get("sample_id") or "").strip()
    if not sample_id:
        raise ValueError("sample_id is required.")
    observed_at = _parse_date(raw.get("observed_at"), field_name="observed_at")
    measurements = tuple(float(value) for value in _parse_sequence(raw.get("measurements_cm")))
    if any(not math.isfinite(value) or value < 0 for value in measurements):
        raise ValueError("measurements_cm must contain finite non-negative values.")
    measured = _optional_float(raw.get("measured_height_cm"))
    p50 = _optional_float(raw.get("height_p50_cm"))
    p90 = _optional_float(raw.get("height_p90_cm"))
    maximum = _optional_float(raw.get("height_max_cm"))
    if len(measurements) == 1 and measured is None:
        measured = measurements[0]
    elif len(measurements) >= 2:
        p50 = p50 if p50 is not None else float(np.quantile(measurements, 0.50))
        p90 = p90 if p90 is not None else float(np.quantile(measurements, 0.90))
        maximum = maximum if maximum is not None else max(measurements)
    metric_values = (measured, p50, p90, maximum)
    if any(value is not None and value < 0 for value in metric_values):
        raise ValueError("Height measurements must be non-negative.")
    quality = str(raw.get("measurement_quality") or "unknown").strip()
    if quality not in MEASUREMENT_QUALITY_VALUES:
        raise ValueError(
            "measurement_quality must be one of: "
            + ", ".join(MEASUREMENT_QUALITY_VALUES)
        )
    geometry = _normalize_geometry(
        raw, source_path=source_path, geometry_dir=geometry_dir
    )
    latitude = _optional_float(raw.get("latitude"))
    longitude = _optional_float(raw.get("longitude"))
    if geometry is not None and (latitude is None or longitude is None):
        polygon, _ = extract_polygon_geometry(dict(geometry))
        latitude = float(polygon.centroid.y) if latitude is None else latitude
        longitude = float(polygon.centroid.x) if longitude is None else longitude
    if latitude is not None and not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90.")
    if longitude is not None and not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180.")
    derived_class = _real_class(p90, measured)
    supplied_class = str(raw.get("real_class") or "").strip()
    if supplied_class and supplied_class not in {derived_class, "unknown"}:
        raise ValueError("real_class conflicts with the physical height measurement.")
    return FieldObservation(
        sample_id=sample_id,
        road=str(raw.get("road") or "").strip() or None,
        km=_optional_float(raw.get("km")),
        side=str(raw.get("side") or "").strip() or None,
        location_name=str(raw.get("location_name") or "").strip() or None,
        geometry=geometry,
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        gps_accuracy_m=_optional_float(raw.get("gps_accuracy_m")),
        measured_height_cm=measured,
        measurements_cm=measurements,
        height_p50_cm=p50,
        height_p90_cm=p90,
        height_max_cm=maximum,
        vegetation_cover_pct=_optional_float(raw.get("vegetation_cover_pct")),
        vegetation_type=str(raw.get("vegetation_type") or "").strip() or None,
        recent_cut=_optional_bool(raw.get("recent_cut"), False)
        if raw.get("recent_cut") not in (None, "")
        else None,
        cut_date=_parse_date(raw.get("cut_date"), field_name="cut_date")
        if raw.get("cut_date") not in (None, "")
        else None,
        soil_condition=str(raw.get("soil_condition") or "").strip() or None,
        moisture_condition=str(raw.get("moisture_condition") or "").strip() or None,
        shadow_condition=str(raw.get("shadow_condition") or "").strip() or None,
        photos=tuple(str(value) for value in _parse_sequence(raw.get("photos"))),
        video_reference=str(raw.get("video_reference") or "").strip() or None,
        measurement_quality=quality,
        source=str(raw.get("source") or "").strip() or None,
        qualitative_condition=str(raw.get("qualitative_condition") or "").strip()
        or None,
        recommendation_context=str(
            raw.get("recommendation_at_collection_geometry")
            or raw.get("recommendation_context")
            or ""
        ).strip()
        or None,
        real_class=derived_class,
        boundary_case=_optional_bool(raw.get("boundary_case"), derived_class != "unknown" and (
            p90 == 30 or (p90 is None and measured == 30)
        )),
        training_eligible=False,
        external_validation=True,
    )


def load_field_data(
    path: str | Path, *, geometry_dir: str | Path | None = None
) -> list[FieldObservation]:
    source = Path(path)
    if source.suffix.casefold() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows: list[Mapping[str, Any]] = list(csv.DictReader(handle))
    else:
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(document, list):
            rows = document
        elif isinstance(document, Mapping) and isinstance(document.get("samples"), list):
            rows = document["samples"]
        else:
            raise ValueError("Field JSON must be a list or contain a samples list.")
    if not rows:
        raise ValueError("Field dataset is empty.")
    resolved_geometry_dir = Path(geometry_dir) if geometry_dir is not None else None
    observations = [
        _normalize_row(row, source_path=source, geometry_dir=resolved_geometry_dir)
        for row in rows
    ]
    ids = [observation.sample_id for observation in observations]
    if len(ids) != len(set(ids)):
        raise ValueError("sample_id values must be unique within a field campaign.")
    return sorted(observations, key=lambda observation: observation.sample_id)


def geometry_identity(geometry: Mapping[str, Any]) -> str:
    canonical = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    return "field_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def geodesic_area_m2(geometry: Mapping[str, Any]) -> float:
    polygon, _ = extract_polygon_geometry(dict(geometry))
    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(polygon)
    value = abs(float(area))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Field Polygon must have a positive geodesic area.")
    return value


def select_latest_causal_scene(
    records: Sequence[Mapping[str, Any]], observed_at: date
) -> Mapping[str, Any] | None:
    eligible = []
    for record in records:
        scene_date = parse_scene_date(record)
        if (
            scene_date is not None
            and scene_date <= observed_at
            and record.get("accepted_for_timeseries") is True
        ):
            eligible.append((record, scene_date))
    if not eligible:
        return None

    def numeric(record: Mapping[str, Any], name: str, default: float) -> float:
        value = record.get(name)
        return float(value) if value is not None else default

    return min(
        eligible,
        key=lambda value: (
            (observed_at - value[1]).days,
            -numeric(value[0], "scene_quality_score", -math.inf),
            -numeric(value[0], "valid_pixel_percentage", -math.inf),
            numeric(value[0], "cloud_cover", math.inf),
            str(value[0].get("item_id") or ""),
        ),
    )[0]


def model_feature_payload(row: Mapping[str, Any]) -> dict[str, float]:
    """Somente Sentinel; recommendation e labels nunca entram como features."""
    return {
        name: float(row[name])
        for name in MODEL_FEATURE_COLUMNS
        if row.get(name) is not None
    }


def _ground_truth_status(observation: FieldObservation) -> str:
    has_metric = any(
        value is not None
        for value in (
            observation.measured_height_cm,
            observation.height_p50_cm,
            observation.height_p90_cm,
            observation.height_max_cm,
        )
    )
    if observation.measurement_quality in {
        "confirmed_single_measurement",
        "confirmed_multiple_measurements",
    } and has_metric:
        return "valid_ground_truth"
    if observation.measurement_quality == "visual_only" or not has_metric:
        return "qualitative_comparison_only"
    return "weak_metric_measurement"


def _base_row(observation: FieldObservation) -> dict[str, Any]:
    geometry = observation.geometry
    return {
        **{name: None for name in CSV_COLUMNS},
        "sample_id": observation.sample_id,
        "observed_at": observation.observed_at.isoformat()
        if observation.observed_at
        else None,
        "road": observation.road,
        "km": observation.km,
        "side": observation.side,
        "location_name": observation.location_name,
        "latitude": observation.latitude,
        "longitude": observation.longitude,
        "geometry_id": geometry_identity(geometry) if geometry else None,
        "geometry_geojson": dict(geometry) if geometry else None,
        "area_m2": geodesic_area_m2(geometry) if geometry else None,
        "measured_height_cm": observation.measured_height_cm,
        "height_p50_cm": observation.height_p50_cm,
        "height_p90_cm": observation.height_p90_cm,
        "height_max_cm": observation.height_max_cm,
        "real_class_30cm": observation.real_class or "unknown",
        "boundary_case": observation.boundary_case,
        "qualitative_condition": observation.qualitative_condition,
        "measurement_quality": observation.measurement_quality,
        "field_measurement_status": _ground_truth_status(observation),
        "training_eligible": False,
        "external_validation": True,
        "source": observation.source,
        "video_reference": observation.video_reference,
        "spectral_extraction_status": WAITING_FOR_FIELD_AOI_GEOJSON
        if geometry is None
        else "pending",
        "temporal_status": WAITING_FOR_FIELD_AOI_GEOJSON
        if geometry is None
        else "pending",
        "recommendation_context": observation.recommendation_context,
        "warnings": [WAITING_FOR_FIELD_AOI_GEOJSON] if geometry is None else [],
    }


def _apply_mask_diagnostics(row: dict[str, Any], diagnostics: Mapping[str, Any]) -> None:
    for name in (
        "vegetation_fraction",
        "mixed_pixel_risk",
        "height_valid_pixel_count",
        "height_total_pixel_count",
    ):
        row[name] = diagnostics.get(name)


def build_field_calibration_rows(
    observations: Sequence[FieldObservation],
    *,
    scene_query: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]] = query_training_scenes,
    feature_reader: Callable[[Any, dict[str, Any]], Any] = read_multiband_height_features,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    feature_cache: dict[tuple[str, str], Mapping[str, Any] | Exception] = {}
    for observation in sorted(observations, key=lambda value: value.sample_id):
        row = _base_row(observation)
        if observation.geometry is None:
            rows.append(row)
            continue
        observed_at = observation.observed_at
        if not isinstance(observed_at, (date, datetime)):
            row["spectral_extraction_status"] = "invalid_observed_at"
            row["temporal_status"] = "unavailable"
            row["warnings"].append("observed_at is unavailable")
            rows.append(row)
            continue
        observed_date = observed_at.date() if isinstance(observed_at, datetime) else observed_at
        polygon, _ = extract_polygon_geometry(dict(observation.geometry))
        records, query_error = scene_query(
            polygon,
            observed_date - timedelta(days=CAUSAL_SCENE_LOOKBACK_DAYS),
            observed_date,
        )
        if query_error:
            row["warnings"].append(f"scene_query: {query_error}")
        anchor_record = select_latest_causal_scene(records, observed_date)
        if anchor_record is None:
            row["spectral_extraction_status"] = "no_valid_causal_scene"
            row["temporal_status"] = "insufficient_history"
            rows.append(row)
            continue
        anchor_date = parse_scene_date(anchor_record)
        item = anchor_record.get("_scene_item")
        item_id = str(anchor_record.get("item_id") or getattr(item, "id", ""))
        row.update(
            {
                "sentinel_item_id": item_id,
                "scene_date": anchor_date.isoformat() if anchor_date else None,
                "scene_age_days": (observed_date - anchor_date).days
                if anchor_date
                else None,
                "cloud_cover": anchor_record.get("cloud_cover"),
                "valid_pixel_percentage": anchor_record.get("valid_pixel_percentage"),
                "aoi_coverage_percentage": anchor_record.get("aoi_coverage_percentage"),
                "scene_quality_score": anchor_record.get("scene_quality_score"),
            }
        )
        cache_key = (str(row["geometry_id"]), item_id)
        if cache_key not in feature_cache:
            try:
                feature_cache[cache_key] = feature_reader(item, dict(observation.geometry)).to_dict()
            except Exception as exc:  # fail-soft do dataset; medicao permanece valida
                feature_cache[cache_key] = exc
        anchor_payload = feature_cache[cache_key]
        if isinstance(anchor_payload, HeightMaskRejectedError):
            _apply_mask_diagnostics(row, anchor_payload.diagnostics)
            row["spectral_extraction_status"] = "rejected_by_height_mask"
            row["temporal_status"] = "anchor_rejected_by_height_mask"
            row["warnings"].append(str(anchor_payload))
            rows.append(row)
            continue
        if isinstance(anchor_payload, Exception):
            row["spectral_extraction_status"] = "feature_extraction_failed"
            row["temporal_status"] = "unavailable"
            row["warnings"].append(
                f"{type(anchor_payload).__name__}: {anchor_payload}"
            )
            rows.append(row)
            continue
        values = anchor_payload["values"]
        row.update({name: values.get(name) for name in STATIC_FEATURE_COLUMNS})
        _apply_mask_diagnostics(row, anchor_payload)
        row["spectral_extraction_status"] = "available"
        anchor = TemporalObservation(anchor_date, item_id, values)
        historical: list[TemporalObservation] = []
        for record in strictly_historical_records(records, anchor_date):
            historical_item = record.get("_scene_item")
            historical_id = str(
                record.get("item_id") or getattr(historical_item, "id", "")
            )
            historical_key = (str(row["geometry_id"]), historical_id)
            if historical_key not in feature_cache:
                try:
                    feature_cache[historical_key] = feature_reader(
                        historical_item, dict(observation.geometry)
                    ).to_dict()
                except Exception as exc:
                    feature_cache[historical_key] = exc
            payload = feature_cache[historical_key]
            scene_date = parse_scene_date(record)
            if isinstance(payload, Exception) or scene_date is None or scene_date >= anchor_date:
                continue
            historical.append(TemporalObservation(scene_date, historical_id, payload["values"]))
        stages = build_temporal_feature_stages(anchor, historical)
        row.update(
            {
                "historical_scene_count": len(historical),
                "previous_scene_date": stages.previous_scene_date.isoformat()
                if stages.previous_scene_date
                else None,
                "temporal_gap_days": stages.temporal_gap_days,
            }
        )
        if stages.t3 is None:
            row["temporal_status"] = "insufficient_history"
            if stages.invalid_reason_t3:
                row["warnings"].append(stages.invalid_reason_t3)
        else:
            row["temporal_status"] = "available"
            row.update({name: stages.t3.get(name) for name in TEMPORAL_FEATURE_COLUMNS})
        rows.append(row)
    return rows


def _height_bucket(row: Mapping[str, Any]) -> str:
    value = row.get("height_p90_cm")
    if value is None:
        value = row.get("measured_height_cm")
    if value is None:
        return "unknown"
    number = float(value)
    if number <= 10:
        return "le_10_cm"
    if number <= 30:
        return "gt_10_le_30_cm"
    return "gt_30_cm"


def ground_truth_quality_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    readiness_config: RegressionReadinessConfig = RegressionReadinessConfig(),
) -> dict[str, Any]:
    quality_counts = {
        name: sum(row.get("measurement_quality") == name for row in rows)
        for name in MEASUREMENT_QUALITY_VALUES
    }
    distribution = {
        name: sum(_height_bucket(row) == name for row in rows)
        for name in ("le_10_cm", "gt_10_le_30_cm", "gt_30_cm", "unknown")
    }
    confirmed = [
        row
        for row in rows
        if row.get("field_measurement_status") == "valid_ground_truth"
    ]
    metric_values: list[float] = []
    for row in confirmed:
        value = row.get("height_p90_cm")
        if value is None:
            value = row.get("measured_height_cm")
        if value is not None:
            metric_values.append(float(value))
    complete_static = [
        row
        for row in confirmed
        if row.get("spectral_extraction_status") == "available"
        and all(row.get(name) is not None for name in STATIC_FEATURE_COLUMNS)
    ]
    complete_temporal = [
        row
        for row in confirmed
        if row.get("temporal_status") == "available"
        and all(row.get(name) is not None for name in TEMPORAL_FEATURE_COLUMNS)
    ]
    locations = {
        str(row.get("location_name") or row.get("geometry_id") or "").strip()
        for row in confirmed
        if str(row.get("location_name") or row.get("geometry_id") or "").strip()
    }
    dates = {str(row.get("observed_at")) for row in confirmed if row.get("observed_at")}
    both_sides = any(value <= 30 for value in metric_values) and any(
        value > 30 for value in metric_values
    )
    ready = (
        len(confirmed) >= readiness_config.min_confirmed_metric_samples
        and len(complete_static) >= readiness_config.min_complete_static_samples
        and len(locations) >= readiness_config.min_unique_locations
        and len(dates) >= readiness_config.min_unique_dates
        and (both_sides or not readiness_config.require_both_sides_of_30_cm)
    )
    building = len(confirmed) >= 5 or len(complete_static) >= 5
    readiness = {
        "confirmed_metric_samples": len(confirmed),
        "samples_with_complete_static_features": len(complete_static),
        "samples_with_complete_temporal_features": len(complete_temporal),
        "unique_locations": len(locations),
        "unique_dates": len(dates),
        "height_min_cm": min(metric_values) if metric_values else None,
        "height_max_cm": max(metric_values) if metric_values else None,
        "class_distribution": {
            "le_30_cm": sum(row.get("real_class_30cm") == "le_30_cm" for row in confirmed),
            "gt_30_cm": sum(row.get("real_class_30cm") == "gt_30_cm" for row in confirmed),
            "unknown": sum(row.get("real_class_30cm") == "unknown" for row in rows),
        },
        "status": "READY_FOR_EXPERIMENTAL_REGRESSION"
        if ready
        else "BUILDING_GROUND_TRUTH"
        if building
        else "INSUFFICIENT_GROUND_TRUTH",
        "operational_readiness_criteria": {
            **asdict(readiness_config),
            "role": "configurable_project_planning_criteria_not_scientific_truth",
        },
    }
    return {
        "total_samples": len(rows),
        **quality_counts,
        "samples_with_geometry": sum(row.get("geometry_id") is not None for row in rows),
        "samples_without_geometry": sum(row.get("geometry_id") is None for row in rows),
        "samples_with_valid_sentinel_match": sum(
            row.get("spectral_extraction_status") == "available" for row in rows
        ),
        "samples_rejected_by_height_mask": sum(
            row.get("spectral_extraction_status") == "rejected_by_height_mask"
            for row in rows
        ),
        "samples_with_temporal_history": sum(
            row.get("temporal_status") == "available" for row in rows
        ),
        "height_distribution": distribution,
        "regression_readiness": readiness,
        "training_protection": {
            "default_training_eligible": False,
            "default_external_validation": True,
            "automatic_training_ingestion": False,
        },
    }


def write_field_calibration_outputs(
    rows: Sequence[Mapping[str, Any]], output_dir: str | Path
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: str(row.get("sample_id") or ""))
    csv_path = destination / "field_ground_truth_enriched.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for source in ordered:
            row = dict(source)
            for name in ("geometry_geojson", "warnings"):
                row[name] = json.dumps(
                    row.get(name), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            writer.writerow({name: row.get(name) for name in CSV_COLUMNS})
    report = ground_truth_quality_report(ordered)
    report_path = destination / "field_ground_truth_quality.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
