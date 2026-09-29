"""Helpers conservadores para validação externa das medições de 2026-08-21."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from pyproj import CRS, Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

from ..models.grass_threshold import classify_height_score, height_score_confidence

EXTERNAL_HOLDOUT_IDS = frozenset({"FIELD_VIDEO_01", "FIELD_VIDEO_02"})
FIELD_DATE = date(2026, 8, 21)
BUFFER_RADII_M = (10, 20, 30)


@dataclass(frozen=True)
class ExternalFieldSample:
    sample_id: str
    latitude: float
    longitude: float
    field_date: date
    measured_height_cm: float
    real_class: str = "le_30_cm"
    boundary_case: bool = False
    source: str = "explicit_confirmed_field_measurement"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["field_date"] = self.field_date.isoformat()
        return value


EXPECTED_HOLDOUTS = (
    ExternalFieldSample(
        "FIELD_VIDEO_01", -23.292111, -46.844444, FIELD_DATE, 14.0
    ),
    ExternalFieldSample(
        "FIELD_VIDEO_02", -23.129694, -46.951000, FIELD_DATE, 30.0,
        boundary_case=True,
    ),
)


def _first(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    lower = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in {None, ""}:
            return value
    return None


def load_external_holdouts(
    dataset: str | Path,
) -> tuple[list[ExternalFieldSample], dict[str, Any]]:
    """Valida linhas externas quando presentes; nunca as devolve como treino."""
    path = Path(dataset)
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        rows = []
    found: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        sample_id = str(_first(row, ("sample_id", "id", "amostra")) or "").strip()
        if sample_id in EXTERNAL_HOLDOUT_IDS:
            found[sample_id] = row
    method = "sample_id"
    if len(found) != len(EXTERNAL_HOLDOUT_IDS):
        method = "coordinates_date_height"
        for expected in EXPECTED_HOLDOUTS:
            if expected.sample_id in found:
                continue
            for row in rows:
                try:
                    latitude = float(_first(row, ("latitude", "lat")))
                    longitude = float(_first(row, ("longitude", "lon", "lng")))
                    measured = float(
                        _first(row, ("measured_height_cm", "height_cm", "altura_cm"))
                    )
                    observed = date.fromisoformat(
                        str(_first(row, ("field_date", "observed_at", "data")))[:10]
                    )
                except (TypeError, ValueError):
                    continue
                if (
                    abs(latitude - expected.latitude) <= 1e-6
                    and abs(longitude - expected.longitude) <= 1e-6
                    and measured == expected.measured_height_cm
                    and observed == expected.field_date
                ):
                    found[expected.sample_id] = row
                    break
    resolved = [
        ExternalFieldSample(
            **{
                **asdict(expected),
                "source": (
                    f"dataset:{path.resolve()}:{method}"
                    if expected.sample_id in found
                    else "explicit_confirmed_field_measurement_dataset_row_not_found"
                ),
            }
        )
        for expected in EXPECTED_HOLDOUTS
    ]
    return resolved, {
        "dataset_path": str(path.resolve()),
        "dataset_available": path.exists(),
        "dataset_row_count": len(rows),
        "matched_sample_ids": sorted(found),
        "missing_sample_ids": sorted(EXTERNAL_HOLDOUT_IDS - set(found)),
        "identification_method": method if found else "explicit_field_spec_fallback",
        "raw_dataset_copied_to_repository": False,
    }


def assert_external_holdout_excluded(rows: Sequence[Mapping[str, Any]]) -> None:
    included = sorted(
        EXTERNAL_HOLDOUT_IDS
        & {str(row.get("sample_id") or "").strip() for row in rows}
    )
    if included:
        raise ValueError("External holdout cannot be used in fit: " + ", ".join(included))


def metric_buffer_geojson(
    latitude: float, longitude: float, radius_m: float
) -> dict[str, Any]:
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("Buffer radius must be positive and finite.")
    utm_zone = int((longitude + 180) // 6) + 1
    projected = CRS.from_dict(
        {"proj": "utm", "zone": utm_zone, "south": latitude < 0, "datum": "WGS84"}
    )
    forward = Transformer.from_crs("EPSG:4326", projected, always_xy=True).transform
    backward = Transformer.from_crs(projected, "EPSG:4326", always_xy=True).transform
    point = transform(forward, Point(longitude, latitude))
    polygon = transform(backward, point.buffer(radius_m))
    return mapping(polygon)


def projected_area_m2(geometry_geojson: Mapping[str, Any]) -> float:
    geometry = shape(geometry_geojson)
    center = geometry.centroid
    utm_zone = int((center.x + 180) // 6) + 1
    projected = CRS.from_dict(
        {"proj": "utm", "zone": utm_zone, "south": center.y < 0, "datum": "WGS84"}
    )
    forward = Transformer.from_crs("EPSG:4326", projected, always_xy=True).transform
    return float(transform(forward, geometry).area)


def select_strict_causal_scene(
    records: Sequence[Mapping[str, Any]], field_date: date
) -> dict[str, Any] | None:
    eligible = [
        (record, scene_date)
        for record in records
        if field_scene_eligibility(record)[0]
        and (scene_date := _scene_date(record)) is not None
        and scene_date <= field_date
    ]
    if not eligible:
        return None
    record, scene_date = min(
        eligible,
        key=lambda entry: (
            (field_date - entry[1]).days,
            -_finite(entry[0].get("scene_quality_score"), -math.inf),
            -_finite(entry[0].get("valid_pixel_percentage"), -math.inf),
            _finite(entry[0].get("cloud_cover"), math.inf),
            str(entry[0].get("item_id") or ""),
        ),
    )
    selected = dict(record)
    selected["sentinel_scene_date"] = scene_date.isoformat()
    selected["days_before_field_measurement"] = (field_date - scene_date).days
    selected["field_external_scene_selection"] = field_scene_eligibility(record)[1]
    return selected


def field_scene_eligibility(record: Mapping[str, Any]) -> tuple[bool, str]:
    """Mantém os filtros; isola apenas o mínimo absoluto inviável em AOI pequena."""
    if record.get("accepted_for_timeseries") is True:
        return True, "accepted_for_timeseries"
    reasons = set(record.get("quality_reasons") or [])
    small_aoi_only = bool(reasons) and reasons <= {"insufficient_valid_pixel_count"}
    if (
        small_aoi_only
        and record.get("processing_status") == "processed"
        and record.get("quality_status") in {"medium", "high"}
        and _finite(record.get("valid_pixel_count"), 0.0) > 0
        and _finite(record.get("valid_pixel_percentage"), 0.0) > 0
    ):
        return True, "field_small_aoi_minimum_pixel_count_override"
    return False, "rejected_by_quality"


def select_nearest_field_scene(
    records: Sequence[Mapping[str, Any]],
    field_date: date,
    *,
    maximum_absolute_lag_days: int = 5,
) -> dict[str, Any] | None:
    eligible = [
        (record, scene_date)
        for record in records
        if field_scene_eligibility(record)[0]
        and (scene_date := _scene_date(record)) is not None
        and abs((scene_date - field_date).days) <= maximum_absolute_lag_days
    ]
    if not eligible:
        return None
    record, scene_date = min(
        eligible,
        key=lambda entry: (
            abs((entry[1] - field_date).days),
            -_finite(entry[0].get("scene_quality_score"), -math.inf),
            -_finite(entry[0].get("valid_pixel_percentage"), -math.inf),
            _finite(entry[0].get("cloud_cover"), math.inf),
            str(entry[0].get("item_id") or ""),
        ),
    )
    selected = dict(record)
    selected["sentinel_scene_date"] = scene_date.isoformat()
    selected["absolute_field_scene_lag_days"] = abs((scene_date - field_date).days)
    selected["field_external_scene_selection"] = field_scene_eligibility(record)[1]
    return selected


def _scene_date(record: Mapping[str, Any]) -> date | None:
    raw = record.get("datetime") or record.get("sentinel_scene_date")
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _finite(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def score_with_frozen_pipeline(
    model: Any, feature_names: Sequence[str], features: Mapping[str, Any]
) -> dict[str, Any]:
    vector = [[float(features[name]) for name in feature_names]]
    if not all(math.isfinite(value) for value in vector[0]):
        raise ValueError("External validation features must be finite.")
    score = float(model.predict_proba(vector)[0, 1])
    return {
        "status": "experimental",
        "score_gt_30_cm": score,
        "estimated_class": classify_height_score(score),
        "confidence": height_score_confidence(score),
    }


def classification_result(real_class: str, estimated_class: str | None) -> str:
    if estimated_class == "inconclusive":
        return "abstained"
    if estimated_class in {None, "unavailable", "insufficient_temporal_history"}:
        return "unavailable"
    if real_class == "le_30_cm" and estimated_class == "le_30_cm":
        return "correct"
    if real_class == "le_30_cm" and estimated_class == "gt_30_cm":
        return "false_positive"
    return "correct" if real_class == estimated_class else "incorrect"


def spatial_stability(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classes = [
        str(row["estimated_class"])
        for row in results
        if row.get("estimated_class") not in {None, "unavailable"}
    ]
    scores = [
        float(row["score_gt_30_cm"])
        for row in results
        if row.get("score_gt_30_cm") is not None
        and math.isfinite(float(row["score_gt_30_cm"]))
    ]
    amplitude = max(scores) - min(scores) if scores else None
    if len(classes) < 2:
        status = "insufficient_data"
    elif amplitude is not None and amplitude >= 0.30:
        status = "spatially_sensitive"
    elif len(set(classes)) == 1 and len(classes) == len(results):
        status = "stable"
    elif max(classes.count(value) for value in set(classes)) >= 2:
        status = "partially_stable"
    else:
        status = "spatially_sensitive"
    return {
        "spatial_stability": status,
        "score_amplitude": amplitude,
        "available_radius_count": len(classes),
    }
