"""Snapshot construction, aggregate statistics, and flat export rows."""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from src.satellite_monitoring.outputs import to_json_compatible
from src.satellite_monitoring.service import AnalysisResult

from .models import MaintenanceTruth, ValidationSampleCreate, VegetationClass
from .identity import geometry_fingerprint
from .repository import SCHEMA_VERSION


TARGET_PER_CLASS = 6
TARGET_TOTAL = 30


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _find_sentinel1(multisource: Any) -> dict[str, Any] | None:
    for source in _mapping(multisource).get("sources") or []:
        if isinstance(source, dict) and source.get("source") == "sentinel-1":
            return source
    return None


def _snapshot_geometry(result: AnalysisResult) -> dict[str, Any] | None:
    """Prefer a real GeoJSON geometry and retain compatibility with test results."""

    aoi = _mapping(getattr(result, "aoi", None))
    if aoi.get("type") in {"Polygon", "MultiPolygon"}:
        return aoi
    if aoi.get("type") == "Feature" and isinstance(aoi.get("geometry"), dict):
        return aoi["geometry"]

    artifact = _mapping(getattr(result, "artifacts", None)).get("aoi")
    if artifact:
        try:
            document = json.loads(Path(artifact).read_text(encoding="utf-8"))
            if document.get("type") in {"Polygon", "MultiPolygon"}:
                return document
            if document.get("type") == "Feature" and isinstance(
                document.get("geometry"), dict
            ):
                return document["geometry"]
        except (OSError, TypeError, ValueError):
            pass
    return None


def _snapshot_analysis_period(result: AnalysisResult) -> dict[str, str] | None:
    multisource = _mapping(getattr(result, "multisource", None))
    raw_range = _mapping(multisource.get("configuration")).get("analysis_period")
    if isinstance(raw_range, str) and "/" in raw_range:
        start_date, end_date = raw_range.split("/", 1)
        if start_date and end_date:
            return {"start_date": start_date, "end_date": end_date}

    parameters = _mapping(_mapping(getattr(result, "summary", None)).get("parameters"))
    start_date = parameters.get("start_date")
    end_date = parameters.get("end_date")
    if isinstance(start_date, str) and isinstance(end_date, str):
        return {"start_date": start_date, "end_date": end_date}
    return None


def _latest_vegetation_fraction(result: AnalysisResult) -> Any:
    height = _mapping(getattr(result, "height_estimation", None))
    if height.get("vegetation_fraction") is not None:
        return height["vegetation_fraction"]
    for observation in reversed(getattr(result, "timeseries", None) or []):
        if isinstance(observation, dict) and observation.get("vegetation_fraction") is not None:
            return observation["vegetation_fraction"]
    return None


def build_snapshot(
    result: AnalysisResult,
    ground_truth: ValidationSampleCreate,
    *,
    sample_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Capture only benchmark-relevant state from an in-memory analysis."""

    sample_id = sample_id or str(uuid4())
    created_at = created_at or datetime.now(timezone.utc)
    recommendation = _mapping(getattr(result, "recommendation", None))
    recommendation_metrics = _mapping(recommendation.get("metrics"))
    summary = _mapping(getattr(result, "summary", None))
    quality = _mapping(summary.get("analysis_quality"))
    summary_aoi = _mapping(summary.get("aoi"))
    aoi = _mapping(getattr(result, "aoi", None))
    aoi_properties = _mapping(aoi.get("properties"))

    sentinel1_source = _find_sentinel1(getattr(result, "multisource", None))
    source = sentinel1_source or {}
    source_metrics = _mapping(source.get("metrics"))
    canonical = _mapping(source_metrics.get("canonical_metrics"))

    aoi_snapshot = {
        "geometry_geojson": _snapshot_geometry(result),
        "selected_area_m2": getattr(result, "selected_area_m2", None),
        "effective_analysis_area_m2": getattr(
            result, "effective_analysis_area_m2", None
        ),
        "effective_analysis_pct": getattr(result, "effective_analysis_pct", None),
        "centroid": summary_aoi.get("centroid")
        or aoi.get("centroid")
        or aoi_properties.get("centroid"),
        "bounding_box": summary_aoi.get("bounding_box")
        or summary_aoi.get("bbox")
        or aoi.get("bbox")
        or aoi_properties.get("bbox"),
    }
    sentinel2_snapshot = {
        "analysis_status": getattr(result, "status", None),
        "recommendation_decision": recommendation.get("recommendation"),
        "recommendation_confidence": recommendation.get("confidence"),
        "observation_count": recommendation_metrics.get("observation_count"),
        "current_ndvi_mean": recommendation_metrics.get("current_ndvi_mean"),
        "current_ndvi_median": recommendation_metrics.get("current_ndvi_median"),
        "historical_median": recommendation_metrics.get("historical_median"),
        "historical_mean": recommendation_metrics.get("historical_mean"),
        "historical_standard_deviation": recommendation_metrics.get(
            "historical_standard_deviation"
        ),
        "current_percentile": recommendation_metrics.get("current_percentile"),
        "recent_trend": recommendation_metrics.get("recent_trend"),
        "recent_trend_status": recommendation_metrics.get("recent_trend_status"),
        "last_absolute_change": recommendation_metrics.get("last_absolute_change"),
        "last_relative_change_percentage": recommendation_metrics.get(
            "last_relative_change_percentage"
        ),
        "significant_drop_detected": recommendation_metrics.get(
            "significant_drop_detected"
        ),
        "significant_drop_confirmed": recommendation_metrics.get(
            "significant_drop_confirmed"
        ),
        "max_observation_gap_days": recommendation_metrics.get(
            "max_observation_gap_days"
        ),
        "first_date": recommendation_metrics.get("first_date"),
        "last_date": recommendation_metrics.get("last_date"),
        "analysis_quality_score": quality.get("score"),
        "analysis_quality_status": quality.get("status"),
        "vegetation_fraction": _latest_vegetation_fraction(result),
    }
    sentinel1_snapshot = {
        "source_status": source.get("status"),
        "quality": source.get("quality"),
        "coverage": source.get("coverage"),
        "observation_count": source_metrics.get("observation_count"),
        "calibrated_observation_count": source_metrics.get(
            "calibrated_observation_count"
        ),
        "uncalibrated_observation_count": source_metrics.get(
            "uncalibrated_observation_count"
        ),
        "relative_orbits": source_metrics.get("relative_orbits"),
        "canonical_relative_orbit": source_metrics.get(
            "canonical_relative_orbit"
        ),
        "canonical_observation_count": source_metrics.get(
            "canonical_observation_count"
        ),
        "temporal_comparability": source_metrics.get("temporal_comparability"),
        "radiometric_calibration_status": source_metrics.get(
            "radiometric_calibration_status"
        ),
        "canonical_metrics": {
            "vv_sigma0_median_linear": canonical.get("vv_sigma0_median_linear"),
            "vh_sigma0_median_linear": canonical.get("vh_sigma0_median_linear"),
            "vv_sigma0_median_db": canonical.get("vv_sigma0_median_db"),
            "vh_sigma0_median_db": canonical.get("vh_sigma0_median_db"),
            "vh_vv_sigma0_ratio_median": canonical.get(
                "vh_vv_sigma0_ratio_median"
            ),
            "vh_minus_vv_db_median": canonical.get("vh_minus_vv_db_median"),
            "mean_valid_pixel_percentage": canonical.get(
                "mean_valid_pixel_percentage"
            ),
            "mean_coverage": canonical.get("mean_coverage"),
        },
        "warnings": source.get("warnings") or [],
        "provenance": source.get("provenance") or {},
    }
    ground_truth_snapshot = {
        "vegetation_class": ground_truth.vegetation_class.value,
        "maintenance_truth": ground_truth.maintenance_truth.value,
        "validation_source": ground_truth.validation_source.value,
        "reference_date": ground_truth.reference_date.isoformat(),
        "notes": ground_truth.notes,
        "cohort": ground_truth.cohort.value,
    }
    snapshot = to_json_compatible(
        {
            "schema_version": SCHEMA_VERSION,
            "sample_id": sample_id,
            "analysis_id": str(ground_truth.analysis_id),
            "created_at": created_at,
            "analysis_period": _snapshot_analysis_period(result),
            "aoi": aoi_snapshot,
            "sentinel2": sentinel2_snapshot,
            "sentinel1": sentinel1_snapshot,
            "ground_truth": ground_truth_snapshot,
        }
    )
    return snapshot


def snapshot_to_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    ground_truth = snapshot["ground_truth"]
    aoi = snapshot["aoi"]
    sentinel2 = snapshot["sentinel2"]
    sentinel1 = snapshot["sentinel1"]
    canonical = sentinel1["canonical_metrics"]
    return {
        "sample_id": snapshot["sample_id"],
        "analysis_id": snapshot["analysis_id"],
        "schema_version": snapshot["schema_version"],
        "created_at": snapshot["created_at"],
        **ground_truth,
        "cohort": ground_truth.get("cohort", "development"),
        "aoi_fingerprint": geometry_fingerprint(aoi.get("geometry_geojson")),
        "selected_area_m2": aoi["selected_area_m2"],
        "s2_decision": sentinel2["recommendation_decision"],
        "s2_confidence": sentinel2["recommendation_confidence"],
        "s2_ndvi_mean": sentinel2["current_ndvi_mean"],
        "s2_ndvi_median": sentinel2["current_ndvi_median"],
        "s2_current_percentile": sentinel2["current_percentile"],
        "s1_status": sentinel1["source_status"],
        "s1_quality": sentinel1["quality"],
        "s1_coverage": sentinel1["coverage"],
        "s1_canonical_relative_orbit": sentinel1["canonical_relative_orbit"],
        "s1_canonical_observation_count": sentinel1[
            "canonical_observation_count"
        ],
        "s1_vv_sigma0_linear": canonical["vv_sigma0_median_linear"],
        "s1_vh_sigma0_linear": canonical["vh_sigma0_median_linear"],
        "s1_vv_sigma0_db": canonical["vv_sigma0_median_db"],
        "s1_vh_sigma0_db": canonical["vh_sigma0_median_db"],
        "s1_vh_minus_vv_db": canonical["vh_minus_vv_db_median"],
        "s1_vh_vv_sigma0_ratio": canonical["vh_vv_sigma0_ratio_median"],
        "snapshot_json": json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False
        ),
    }


def create_record(
    result: AnalysisResult,
    ground_truth: ValidationSampleCreate,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    return snapshot_to_record(build_snapshot(result, ground_truth, created_at=created_at))


def _finite_values(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric):
                values.append(numeric)
    return sorted(values)


def _percentile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _statistics(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    values = _finite_values(rows, key)
    if not values:
        return None
    return {
        "count": len(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "min": values[0],
        "max": values[-1],
        "q25": _percentile(values, 0.25),
        "q75": _percentile(values, 0.75),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_class: dict[str, Any] = {}
    metric_groups = {
        "sentinel2": {
            "current_ndvi_mean": "s2_ndvi_mean",
            "current_ndvi_median": "s2_ndvi_median",
            "current_percentile": "s2_current_percentile",
        },
        "sentinel1": {
            "canonical_vv_sigma0_db": "s1_vv_sigma0_db",
            "canonical_vh_sigma0_db": "s1_vh_sigma0_db",
            "canonical_vh_minus_vv_db": "s1_vh_minus_vv_db",
        },
    }
    for vegetation_class in VegetationClass:
        class_rows = [
            row for row in rows if row["vegetation_class"] == vegetation_class.value
        ]
        grouped_statistics: dict[str, Any] = {}
        for group, metrics in metric_groups.items():
            available = {
                public_name: computed
                for public_name, column in metrics.items()
                if (computed := _statistics(class_rows, column)) is not None
            }
            grouped_statistics[group] = available
        count = len(class_rows)
        by_class[vegetation_class.value] = {
            "count": count,
            "target": TARGET_PER_CLASS,
            "remaining": max(0, TARGET_PER_CLASS - count),
            "statistics": grouped_statistics,
        }
    counts_by_truth = {
        truth.value: sum(
            1 for row in rows if row["maintenance_truth"] == truth.value
        )
        for truth in MaintenanceTruth
    }
    counts_by_cohort = {
        cohort: sum(
            1 for row in rows if row.get("cohort", "development") == cohort
        )
        for cohort in ("development", "holdout")
    }
    return {
        "total_samples": len(rows),
        "target_total": TARGET_TOTAL,
        "remaining_total": max(0, TARGET_TOTAL - len(rows)),
        "target_per_class": TARGET_PER_CLASS,
        "counts_by_vegetation_class": {
            vegetation_class: details["count"]
            for vegetation_class, details in by_class.items()
        },
        "by_vegetation_class": by_class,
        "counts_by_maintenance_truth": counts_by_truth,
        "counts_by_cohort": counts_by_cohort,
    }


CSV_COLUMNS = (
    "sample_id", "analysis_id", "vegetation_class", "maintenance_truth",
    "validation_source", "reference_date", "created_at", "cohort", "selected_area_m2",
    "s2_decision", "s2_confidence", "s2_ndvi_mean", "s2_ndvi_median",
    "s2_current_percentile", "s1_status", "s1_quality", "s1_coverage",
    "s1_canonical_relative_orbit", "s1_canonical_observation_count",
    "s1_vv_sigma0_linear", "s1_vh_sigma0_linear", "s1_vv_sigma0_db",
    "s1_vh_sigma0_db", "s1_vh_minus_vv_db", "s1_vh_vv_sigma0_ratio",
)
