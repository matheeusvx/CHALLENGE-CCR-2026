"""Construcao offline de ground truth de campo enriquecido por Sentinel-2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from pyproj import Geod
from shapely.geometry import mapping

from ..datasets.field_schema import (
    MEASUREMENT_QUALITY_VALUES,
    MEASUREMENT_TYPE_VALUES,
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
PERCENTILE_METHOD = "linear (NumPy; equivalent to Hyndman-Fan type 7)"
EXTREME_HEIGHT_WARNING_CM = 300.0

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
    "campaign_id",
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
    "reference_bbox",
    "measurement_type",
    "height_measurements_cm",
    "measurement_count",
    "height_min_cm",
    "height_mean_cm",
    "measured_height_cm",
    "height_lower_bound_cm",
    "height_upper_bound_cm",
    "confirmed_above_lower_bound",
    "height_p50_cm",
    "height_p90_cm",
    "height_max_cm",
    "measurement_method",
    "measurement_spacing_m",
    "real_class_30cm",
    "regulatory_class_30cm",
    "experimental_certainty_zone",
    "future_primary_target_gt30",
    "boundary_case",
    "qualitative_condition",
    "measurement_quality",
    "ground_truth_strength",
    "classification_ground_truth_strength",
    "metric_regression_eligible",
    "regulatory_gt30",
    "vegetation_cover_pct",
    "vegetation_type",
    "recent_cut",
    "cut_date",
    "soil_condition",
    "moisture_condition",
    "photo_references",
    "video_references",
    "collector_notes",
    "field_measurement_status",
    "training_eligible",
    "external_validation",
    "source",
    "video_reference",
    "sentinel_item_id",
    "scene_date",
    "scene_datetime",
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
    min_height_range_cm: float = 20.0
    min_clear_negative_samples: int = 5
    min_clear_positive_samples: int = 5
    min_gt30_metric_samples: int = 5
    require_both_sides_of_30_cm: bool = True


@dataclass(frozen=True)
class CoveragePlanningConfig:
    """Metas operacionais de coleta; nao representam tamanho amostral cientifico."""

    target_clear_negative: int = 10
    target_uncertainty_zone: int = 10
    target_clear_positive: int = 10
    target_gt30_metric_samples: int = 10


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


def _parse_observed_at(value: Any) -> date | datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if "T" not in raw and " " not in raw:
        return _parse_date(raw, field_name="observed_at")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO date or datetime.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at datetime must include a timezone offset.")
    return parsed


def _scene_datetime(record: Mapping[str, Any]) -> datetime | None:
    raw = record.get("datetime")
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_sequence(value: Any) -> tuple[Any, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        if stripped.casefold() == "null":
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
    if isinstance(crs, str):
        name = crs.strip().upper()
    else:
        try:
            name = str(crs["properties"]["name"]).strip().upper()
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "Invalid GeoJSON CRS declaration; expected EPSG:4326."
            ) from exc
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
    if raw is None or not str(raw).strip():
        raw = row.get("geometry_geojson")
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
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or count != 1:
        raise ValueError(
            "Field calibration geometry must be one Polygon or MultiPolygon."
        )
    return mapping(geometry)


def regulatory_class_30cm(value: float | None) -> str:
    if value is None:
        return "unknown"
    return "le_30_cm" if value <= 30 else "gt_30_cm"


def experimental_certainty_zone(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value <= 25:
        return "CLEAR_NEGATIVE"
    if value < 35:
        return "UNCERTAINTY_ZONE"
    return "CLEAR_POSITIVE"


def ground_truth_strength(
    measurement_quality: str,
    *,
    has_geometry: bool,
    has_metric_height: bool,
) -> str:
    """Diagnostico de confiabilidade; nunca e usado como feature espectral."""
    if measurement_quality == "visual_only" or not has_metric_height:
        return "non_metric"
    if measurement_quality == "confirmed_multiple_measurements":
        return "strong" if has_geometry else "moderate"
    if measurement_quality == "confirmed_single_measurement":
        return "moderate" if has_geometry else "weak"
    return "weak"


def _validate_supplied_statistic(
    supplied: float | None, calculated: float, *, field_name: str
) -> float:
    if supplied is not None and not math.isclose(
        supplied, calculated, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError(
            f"{field_name} conflicts with height_measurements_cm; "
            f"expected {calculated}."
        )
    return calculated


def _normalize_row(
    raw: Mapping[str, Any],
    *,
    source_path: Path,
    geometry_dir: Path | None,
    campaign_id: str | None = None,
    campaign_observed_at: Any = None,
) -> FieldObservation:
    sample_id = str(raw.get("sample_id") or "").strip()
    if not sample_id:
        raise ValueError("sample_id is required.")
    raw_observed_at = raw.get("observed_at") or campaign_observed_at
    observed_at = (
        _parse_observed_at(raw_observed_at)
        if raw_observed_at not in (None, "")
        else None
    )
    measurement_key = next(
        (
            name
            for name in ("height_measurements_cm", "measurements_cm")
            if name in raw
        ),
        None,
    )
    measurements = tuple(
        float(value)
        for value in _parse_sequence(raw.get(measurement_key))
    ) if measurement_key else ()
    supplied_measurements = raw.get(measurement_key) if measurement_key else None
    explicitly_empty_list = (
        isinstance(supplied_measurements, Sequence)
        and not isinstance(supplied_measurements, str)
        and len(supplied_measurements) == 0
    ) or (
        isinstance(supplied_measurements, str)
        and supplied_measurements.strip() == "[]"
    )
    if explicitly_empty_list:
        raise ValueError(f"{measurement_key} cannot be an explicitly empty list.")
    if any(not math.isfinite(value) or value < 0 for value in measurements):
        raise ValueError(
            "height_measurements_cm must contain finite non-negative values."
        )
    measured = _optional_float(raw.get("measured_height_cm"))
    lower_bound = _optional_float(raw.get("height_lower_bound_cm"))
    upper_bound = _optional_float(raw.get("height_upper_bound_cm"))
    confirmed_above_lower_bound = _optional_bool(
        raw.get("confirmed_above_lower_bound"), False
    )
    minimum = _optional_float(raw.get("height_min_cm"))
    mean = _optional_float(raw.get("height_mean_cm"))
    p50 = _optional_float(raw.get("height_p50_cm"))
    p90 = _optional_float(raw.get("height_p90_cm"))
    maximum = _optional_float(raw.get("height_max_cm"))
    if len(measurements) == 1 and measured is None:
        measured = measurements[0]
    statistic_values = measurements or ((measured,) if measured is not None else ())
    measurement_count = len(statistic_values)
    if statistic_values:
        calculated_minimum = min(statistic_values)
        calculated_mean = float(np.mean(statistic_values))
        calculated_maximum = max(statistic_values)
        minimum = _validate_supplied_statistic(
            minimum, calculated_minimum, field_name="height_min_cm"
        )
        mean = _validate_supplied_statistic(
            mean, calculated_mean, field_name="height_mean_cm"
        )
        maximum = _validate_supplied_statistic(
            maximum, calculated_maximum, field_name="height_max_cm"
        )
    if len(measurements) >= 2:
        calculated_p50 = float(np.quantile(measurements, 0.50, method="linear"))
        calculated_p90 = float(np.quantile(measurements, 0.90, method="linear"))
        p50 = _validate_supplied_statistic(
            p50, calculated_p50, field_name="height_p50_cm"
        )
        p90 = _validate_supplied_statistic(
            p90, calculated_p90, field_name="height_p90_cm"
        )
    metric_values = (measured, minimum, mean, p50, p90, maximum)
    if any(value is not None and value < 0 for value in metric_values):
        raise ValueError("Height measurements must be non-negative.")
    quality = str(raw.get("measurement_quality") or "unknown").strip()
    if quality not in MEASUREMENT_QUALITY_VALUES:
        raise ValueError(
            "measurement_quality must be one of: "
            + ", ".join(MEASUREMENT_QUALITY_VALUES)
        )
    if quality == "confirmed_multiple_measurements" and len(measurements) <= 1:
        raise ValueError(
            "confirmed_multiple_measurements requires more than one physical measurement."
        )
    if quality == "visual_only" and any(value is not None for value in metric_values):
        raise ValueError("visual_only samples cannot contain metric height values.")
    measurement_type = str(raw.get("measurement_type") or "").strip()
    if not measurement_type:
        if len(measurements) > 1:
            measurement_type = "multiple"
        elif measured is not None or len(measurements) == 1:
            measurement_type = "exact_single"
        else:
            measurement_type = "visual_only"
    if measurement_type not in MEASUREMENT_TYPE_VALUES:
        raise ValueError(
            "measurement_type must be one of: " + ", ".join(MEASUREMENT_TYPE_VALUES)
        )
    if lower_bound is not None and lower_bound < 0:
        raise ValueError("height_lower_bound_cm must be non-negative.")
    if upper_bound is not None and upper_bound < 0:
        raise ValueError("height_upper_bound_cm must be non-negative.")
    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        raise ValueError("height_lower_bound_cm cannot exceed height_upper_bound_cm.")
    if measurement_type == "threshold_lower_bound":
        if lower_bound is None:
            raise ValueError("threshold_lower_bound requires height_lower_bound_cm.")
        threshold_forbidden = (
            measured,
            minimum,
            mean,
            p50,
            p90,
            maximum,
        )
        if measurements or any(value is not None for value in threshold_forbidden):
            raise ValueError(
                "threshold_lower_bound cannot contain exact or derived metric heights."
            )
    elif lower_bound is not None or upper_bound is not None or confirmed_above_lower_bound:
        raise ValueError(
            "height bounds and their confirmation require threshold_lower_bound."
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
    representative_height = p90 if len(measurements) >= 2 else measured
    if representative_height is None and p90 is not None:
        # Compatibilidade com campanhas historicas que ja traziam p90 explicito.
        representative_height = p90
    confirmed_clear_positive_threshold = (
        measurement_type == "threshold_lower_bound"
        and confirmed_above_lower_bound
        and lower_bound is not None
        and lower_bound >= 35
    )
    derived_class = (
        "gt_30_cm"
        if confirmed_clear_positive_threshold
        else regulatory_class_30cm(representative_height)
    )
    certainty_zone = (
        "CLEAR_POSITIVE"
        if confirmed_clear_positive_threshold
        else experimental_certainty_zone(representative_height)
    )
    future_target = (
        p90 > 30
        if quality == "confirmed_multiple_measurements"
        and len(measurements) > 1
        and p90 is not None
        else None
    )
    supplied_class = str(raw.get("real_class") or "").strip()
    supplied_class = {">30": "gt_30_cm", "<=30": "le_30_cm"}.get(
        supplied_class, supplied_class
    )
    if supplied_class and supplied_class not in {derived_class, "unknown"}:
        raise ValueError("real_class conflicts with the physical height measurement.")
    warnings: list[str] = []
    if maximum is not None and maximum > EXTREME_HEIGHT_WARNING_CM:
        warnings.append(
            f"height_max_cm_above_planning_review_threshold:{EXTREME_HEIGHT_WARNING_CM:g}"
        )
    strength = ground_truth_strength(
        quality,
        has_geometry=geometry is not None,
        has_metric_height=representative_height is not None,
    )
    classification_strength = (
        "strong"
        if confirmed_clear_positive_threshold
        else strength
        if derived_class != "unknown"
        else "none"
    )
    metric_regression_eligible = (
        measurement_type in {"exact_single", "multiple"}
        and representative_height is not None
        and quality
        in {"confirmed_single_measurement", "confirmed_multiple_measurements"}
    )
    photo_references = tuple(
        str(value)
        for value in _parse_sequence(
            raw.get("photo_references")
            if raw.get("photo_references") is not None
            else raw.get("photos")
        )
    )
    video_references = tuple(
        str(value)
        for value in _parse_sequence(raw.get("video_references"))
    )
    legacy_video = str(raw.get("video_reference") or "").strip() or None
    if not video_references and legacy_video:
        video_references = (legacy_video,)
    return FieldObservation(
        sample_id=sample_id,
        campaign_id=str(raw.get("campaign_id") or campaign_id or "").strip() or None,
        road=str(raw.get("road") or "").strip() or None,
        km=_optional_float(raw.get("km")),
        side=str(raw.get("side") or "").strip() or None,
        location_name=str(raw.get("location_name") or "").strip() or None,
        geometry=geometry,
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        gps_accuracy_m=_optional_float(raw.get("gps_accuracy_m")),
        measurement_type=measurement_type,
        measured_height_cm=measured,
        height_lower_bound_cm=lower_bound,
        height_upper_bound_cm=upper_bound,
        confirmed_above_lower_bound=confirmed_above_lower_bound,
        height_measurements_cm=measurements,
        measurements_cm=measurements,
        measurement_count=measurement_count,
        height_min_cm=minimum,
        height_mean_cm=mean,
        height_p50_cm=p50,
        height_p90_cm=p90,
        height_max_cm=maximum,
        measurement_method=str(raw.get("measurement_method") or "").strip() or None,
        measurement_spacing_m=_optional_float(raw.get("measurement_spacing_m")),
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
        photos=photo_references,
        photo_references=photo_references,
        video_reference=legacy_video,
        video_references=video_references,
        collector_notes=str(raw.get("collector_notes") or "").strip() or None,
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
        regulatory_class_30cm=derived_class,
        experimental_certainty_zone=certainty_zone,
        future_primary_target_gt30=future_target,
        ground_truth_strength=strength,
        classification_ground_truth_strength=classification_strength,
        metric_regression_eligible=metric_regression_eligible,
        regulatory_gt30=True if derived_class == "gt_30_cm" else False if derived_class == "le_30_cm" else None,
        reference_bbox=raw.get("reference_bbox")
        if isinstance(raw.get("reference_bbox"), Mapping)
        else None,
        validation_warnings=tuple(warnings),
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
    campaign_id: str | None = None
    campaign_observed_at: Any = None
    if source.suffix.casefold() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows: list[Mapping[str, Any]] = list(csv.DictReader(handle))
    else:
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(document, list):
            rows = document
        elif isinstance(document, Mapping) and isinstance(document.get("samples"), list):
            rows = document["samples"]
            campaign_id = str(document.get("campaign_id") or "").strip() or None
            campaign_observed_at = document.get("observed_at")
            _validate_embedded_crs(document)
        else:
            raise ValueError("Field JSON must be a list or contain a samples list.")
    if not rows:
        raise ValueError("Field dataset is empty.")
    resolved_geometry_dir = Path(geometry_dir) if geometry_dir is not None else None
    observations = [
        _normalize_row(
            row,
            source_path=source,
            geometry_dir=resolved_geometry_dir,
            campaign_id=campaign_id,
            campaign_observed_at=campaign_observed_at,
        )
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
    records: Sequence[Mapping[str, Any]], observed_at: date | datetime
) -> Mapping[str, Any] | None:
    eligible = []
    for record in records:
        scene_date = parse_scene_date(record)
        scene_datetime = _scene_datetime(record)
        if isinstance(observed_at, datetime):
            causal = (
                scene_datetime is not None
                and scene_datetime.astimezone(timezone.utc)
                <= observed_at.astimezone(timezone.utc)
            )
            age = (
                observed_at.astimezone(timezone.utc)
                - scene_datetime.astimezone(timezone.utc)
            ).total_seconds()
        else:
            causal = scene_date is not None and scene_date <= observed_at
            age = float((observed_at - scene_date).days) if scene_date else math.inf
        if (
            scene_date is not None
            and causal
            and record.get("accepted_for_timeseries") is True
        ):
            eligible.append((record, scene_date, age))
    if not eligible:
        return None

    def numeric(record: Mapping[str, Any], name: str, default: float) -> float:
        value = record.get(name)
        return float(value) if value is not None else default

    return min(
        eligible,
        key=lambda value: (
            value[2],
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
    if observation.metric_regression_eligible and observation.measurement_quality in {
        "confirmed_single_measurement",
        "confirmed_multiple_measurements",
    } and has_metric:
        return "valid_ground_truth"
    if observation.classification_ground_truth_strength == "strong":
        return "valid_classification_ground_truth"
    if observation.measurement_quality == "visual_only" or not has_metric:
        return "qualitative_comparison_only"
    return "weak_metric_measurement"


def _base_row(observation: FieldObservation) -> dict[str, Any]:
    geometry = observation.geometry
    warnings = list(observation.validation_warnings)
    if geometry is None:
        warnings.append(WAITING_FOR_FIELD_AOI_GEOJSON)
    return {
        **{name: None for name in CSV_COLUMNS},
        "campaign_id": observation.campaign_id,
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
        "reference_bbox": dict(observation.reference_bbox)
        if observation.reference_bbox
        else None,
        "measurement_type": observation.measurement_type,
        "height_measurements_cm": list(observation.height_measurements_cm)
        if observation.height_measurements_cm
        else None,
        "measurement_count": observation.measurement_count,
        "height_min_cm": observation.height_min_cm,
        "height_mean_cm": observation.height_mean_cm,
        "measured_height_cm": observation.measured_height_cm,
        "height_lower_bound_cm": observation.height_lower_bound_cm,
        "height_upper_bound_cm": observation.height_upper_bound_cm,
        "confirmed_above_lower_bound": observation.confirmed_above_lower_bound,
        "height_p50_cm": observation.height_p50_cm,
        "height_p90_cm": observation.height_p90_cm,
        "height_max_cm": observation.height_max_cm,
        "measurement_method": observation.measurement_method,
        "measurement_spacing_m": observation.measurement_spacing_m,
        "real_class_30cm": observation.real_class or "unknown",
        "regulatory_class_30cm": observation.regulatory_class_30cm or "unknown",
        "experimental_certainty_zone": observation.experimental_certainty_zone
        or "UNKNOWN",
        "future_primary_target_gt30": observation.future_primary_target_gt30,
        "boundary_case": observation.boundary_case,
        "qualitative_condition": observation.qualitative_condition,
        "measurement_quality": observation.measurement_quality,
        "ground_truth_strength": observation.ground_truth_strength,
        "classification_ground_truth_strength": observation.classification_ground_truth_strength,
        "metric_regression_eligible": observation.metric_regression_eligible,
        "regulatory_gt30": observation.regulatory_gt30,
        "vegetation_cover_pct": observation.vegetation_cover_pct,
        "vegetation_type": observation.vegetation_type,
        "recent_cut": observation.recent_cut,
        "cut_date": observation.cut_date.isoformat() if observation.cut_date else None,
        "soil_condition": observation.soil_condition,
        "moisture_condition": observation.moisture_condition,
        "photo_references": list(observation.photo_references),
        "video_references": list(observation.video_references),
        "collector_notes": observation.collector_notes,
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
        "warnings": warnings,
    }


def upgrade_existing_enriched_rows(
    path: str | Path, *, campaign_id: str | None = None
) -> list[dict[str, Any]]:
    """Migra outputs anteriores ao protocolo v1 sem consultar Sentinel novamente."""
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    if not source_rows:
        raise ValueError("Existing enriched field dataset is empty.")
    observations = [
        _normalize_row(
            source,
            source_path=source_path,
            geometry_dir=None,
            campaign_id=campaign_id,
        )
        for source in source_rows
    ]
    protocol_columns = {
        "campaign_id",
        "geometry_id",
        "geometry_geojson",
        "area_m2",
        "reference_bbox",
        "measurement_type",
        "height_measurements_cm",
        "measurement_count",
        "height_min_cm",
        "height_mean_cm",
        "measured_height_cm",
        "height_lower_bound_cm",
        "height_upper_bound_cm",
        "confirmed_above_lower_bound",
        "height_p50_cm",
        "height_p90_cm",
        "height_max_cm",
        "measurement_method",
        "measurement_spacing_m",
        "real_class_30cm",
        "regulatory_class_30cm",
        "experimental_certainty_zone",
        "future_primary_target_gt30",
        "boundary_case",
        "measurement_quality",
        "ground_truth_strength",
        "classification_ground_truth_strength",
        "metric_regression_eligible",
        "regulatory_gt30",
        "vegetation_cover_pct",
        "vegetation_type",
        "recent_cut",
        "cut_date",
        "soil_condition",
        "moisture_condition",
        "photo_references",
        "video_references",
        "collector_notes",
        "field_measurement_status",
        "training_eligible",
        "external_validation",
    }
    upgraded: list[dict[str, Any]] = []
    for source, observation in zip(source_rows, observations, strict=True):
        base = _base_row(observation)
        row = {name: source.get(name) for name in CSV_COLUMNS}
        row.update({name: base.get(name) for name in protocol_columns})
        try:
            old_warnings = json.loads(str(source.get("warnings") or "[]"))
        except json.JSONDecodeError:
            old_warnings = [str(source.get("warnings"))]
        if not isinstance(old_warnings, list):
            old_warnings = [str(old_warnings)]
        row["warnings"] = list(
            dict.fromkeys([*old_warnings, *base.get("warnings", [])])
        )
        upgraded.append(row)
    return sorted(upgraded, key=lambda row: str(row.get("sample_id") or ""))


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
        anchor_record = select_latest_causal_scene(records, observed_at)
        if anchor_record is None:
            row["spectral_extraction_status"] = "no_valid_causal_scene"
            row["temporal_status"] = "insufficient_history"
            rows.append(row)
            continue
        anchor_date = parse_scene_date(anchor_record)
        anchor_datetime = _scene_datetime(anchor_record)
        item = anchor_record.get("_scene_item")
        item_id = str(anchor_record.get("item_id") or getattr(item, "id", ""))
        row.update(
            {
                "sentinel_item_id": item_id,
                "scene_date": anchor_date.isoformat() if anchor_date else None,
                "scene_datetime": anchor_datetime.isoformat()
                if anchor_datetime
                else None,
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


def _representative_metric_height(row: Mapping[str, Any]) -> float | None:
    value = row.get("height_p90_cm")
    if value is None:
        value = row.get("measured_height_cm")
    return float(value) if value is not None and str(value).strip() != "" else None


def ground_truth_coverage_gaps(
    rows: Sequence[Mapping[str, Any]],
    *,
    planning_config: CoveragePlanningConfig = CoveragePlanningConfig(),
) -> dict[str, Any]:
    zones = {
        name: sum(row.get("experimental_certainty_zone") == name for row in rows)
        for name in ("CLEAR_NEGATIVE", "UNCERTAINTY_ZONE", "CLEAR_POSITIVE")
    }
    gt30 = sum(
        row.get("regulatory_class_30cm") == "gt_30_cm"
        and row.get("field_measurement_status") == "valid_ground_truth"
        for row in rows
    )
    return {
        "clear_negative_needed": max(
            0, planning_config.target_clear_negative - zones["CLEAR_NEGATIVE"]
        ),
        "uncertainty_zone_needed": max(
            0,
            planning_config.target_uncertainty_zone - zones["UNCERTAINTY_ZONE"],
        ),
        "clear_positive_needed": max(
            0, planning_config.target_clear_positive - zones["CLEAR_POSITIVE"]
        ),
        "gt30_metric_samples_needed": max(
            0, planning_config.target_gt30_metric_samples - gt30
        ),
        "current_counts": {**zones, "regulatory_gt30_metric": gt30},
        "planning_targets": {
            **asdict(planning_config),
            "role": "configurable_field_planning_targets_not_scientific_sample_size",
        },
    }


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
        value = _representative_metric_height(row)
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
    height_range = (
        max(metric_values) - min(metric_values) if len(metric_values) >= 2 else 0.0
    )
    clear_negative_count = sum(
        row.get("experimental_certainty_zone") == "CLEAR_NEGATIVE"
        for row in confirmed
    )
    clear_positive_count = sum(
        row.get("experimental_certainty_zone") == "CLEAR_POSITIVE"
        for row in confirmed
    )
    gt30_count = sum(
        row.get("regulatory_class_30cm") == "gt_30_cm" for row in confirmed
    )
    ready = (
        len(confirmed) >= readiness_config.min_confirmed_metric_samples
        and len(complete_static) >= readiness_config.min_complete_static_samples
        and len(locations) >= readiness_config.min_unique_locations
        and len(dates) >= readiness_config.min_unique_dates
        and height_range >= readiness_config.min_height_range_cm
        and clear_negative_count >= readiness_config.min_clear_negative_samples
        and clear_positive_count >= readiness_config.min_clear_positive_samples
        and gt30_count >= readiness_config.min_gt30_metric_samples
        and (both_sides or not readiness_config.require_both_sides_of_30_cm)
    )
    building = len(confirmed) >= 5 or len(complete_static) >= 5
    readiness = {
        "confirmed_metric_samples": len(confirmed),
        "strong_classification_ground_truth_samples": sum(
            row.get("classification_ground_truth_strength") == "strong"
            for row in rows
        ),
        "samples_with_complete_static_features": len(complete_static),
        "samples_with_complete_temporal_features": len(complete_temporal),
        "unique_locations": len(locations),
        "unique_dates": len(dates),
        "height_min_cm": min(metric_values) if metric_values else None,
        "height_max_cm": max(metric_values) if metric_values else None,
        "height_range_cm": height_range if metric_values else None,
        "clear_negative_samples": clear_negative_count,
        "clear_positive_samples": clear_positive_count,
        "gt30_metric_samples": gt30_count,
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
        "ground_truth_coverage_gaps": ground_truth_coverage_gaps(rows),
    }


def field_campaign_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    campaign_id: str | None = None,
    planning_config: CoveragePlanningConfig = CoveragePlanningConfig(),
    readiness_config: RegressionReadinessConfig = RegressionReadinessConfig(),
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row.get("sample_id") or ""))
    campaign_ids = sorted(
        {
            str(row.get("campaign_id") or "").strip()
            for row in ordered
            if str(row.get("campaign_id") or "").strip()
        }
    )
    resolved_campaign_id = campaign_id or (
        campaign_ids[0]
        if len(campaign_ids) == 1
        else "mixed_campaigns"
        if campaign_ids
        else None
    )
    metric_rows = [
        row for row in ordered if _representative_metric_height(row) is not None
    ]
    metric_values = [
        value
        for row in metric_rows
        if (value := _representative_metric_height(row)) is not None
    ]
    warning_values = sorted(
        {
            str(warning)
            for row in ordered
            for warning in (row.get("warnings") or [])
            if str(warning).strip()
        }
    )
    quality = ground_truth_quality_report(
        ordered, readiness_config=readiness_config
    )
    return {
        "campaign_id": resolved_campaign_id,
        "samples_total": len(ordered),
        "metric_samples": len(metric_rows),
        "non_metric_samples": len(ordered) - len(metric_rows),
        "strong_classification_ground_truth_samples": sum(
            row.get("classification_ground_truth_strength") == "strong"
            for row in ordered
        ),
        "metric_regression_eligible_samples": sum(
            row.get("metric_regression_eligible") is True for row in ordered
        ),
        "confirmed_single": sum(
            row.get("measurement_quality") == "confirmed_single_measurement"
            for row in ordered
        ),
        "confirmed_multiple": sum(
            row.get("measurement_quality") == "confirmed_multiple_measurements"
            for row in ordered
        ),
        "clear_negative": sum(
            row.get("experimental_certainty_zone") == "CLEAR_NEGATIVE"
            for row in ordered
        ),
        "uncertainty_zone": sum(
            row.get("experimental_certainty_zone") == "UNCERTAINTY_ZONE"
            for row in ordered
        ),
        "clear_positive": sum(
            row.get("experimental_certainty_zone") == "CLEAR_POSITIVE"
            for row in ordered
        ),
        "regulatory_le_30": sum(
            row.get("regulatory_class_30cm") == "le_30_cm" for row in ordered
        ),
        "regulatory_gt_30": sum(
            row.get("regulatory_class_30cm") == "gt_30_cm" for row in ordered
        ),
        "samples_with_geometry": sum(row.get("geometry_id") is not None for row in ordered),
        "samples_with_valid_static_features": sum(
            row.get("spectral_extraction_status") == "available"
            and all(row.get(name) is not None for name in STATIC_FEATURE_COLUMNS)
            for row in ordered
        ),
        "samples_with_temporal_features": sum(
            row.get("temporal_status") == "available"
            and all(row.get(name) is not None for name in TEMPORAL_FEATURE_COLUMNS)
            for row in ordered
        ),
        "training_eligible_count": sum(
            row.get("training_eligible") is True for row in ordered
        ),
        "height_min_cm": min(metric_values) if metric_values else None,
        "height_max_cm": max(metric_values) if metric_values else None,
        "warnings": warning_values,
        "percentile_calculation": PERCENTILE_METHOD,
        "ground_truth_coverage_gaps": ground_truth_coverage_gaps(
            ordered, planning_config=planning_config
        ),
        "regression_readiness": quality["regression_readiness"],
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
            for name in (
                "geometry_geojson",
                "reference_bbox",
                "height_measurements_cm",
                "photo_references",
                "video_references",
                "warnings",
            ):
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
    summary = field_campaign_summary(ordered)
    summary_path = destination / "field_campaign_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**report, "field_campaign_summary": summary}
