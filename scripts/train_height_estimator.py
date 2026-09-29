"""Treina o estimador binario experimental de faixa de altura v0."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from shapely.geometry import mapping
from sklearn.pipeline import Pipeline

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.satellite_monitoring.datasets.spatial_matching import (
    CandidatePolygon,
    ManagementFeature,
    build_candidate_polygon_sets,
    load_calibration_rows,
    load_km_markers,
    load_management_features,
    resolve_filename_target_date,
)
from src.satellite_monitoring.datasets.training_scenes import query_training_scenes
from src.satellite_monitoring.features.vegetation_mask import extract_height_features
from src.satellite_monitoring.models.grass_threshold import MODEL_FEATURES, MODEL_VERSION
from src.satellite_monitoring.models.validation import (
    build_training_pipeline as _shared_build_training_pipeline,
    experimental_validation_metrics as _shared_experimental_validation_metrics,
    grouped_validation_predictions as _shared_grouped_validation_predictions,
    training_arrays as _shared_training_arrays,
)
from src.satellite_monitoring.raster_processing import read_scene_bands

TRAINING_ALIGNMENT_DAYS = 5
TRAINING_ALIGNMENT = "nearest_valid_scene_within_5_days"
TRAINING_SCENARIOS = {"D50": 50.0}


def binary_height_target(height_class: Any) -> int:
    value = int(float(height_class))
    if value in {1, 2}:
        return 0
    if value == 3:
        return 1
    raise ValueError("height_class must be 1, 2 or 3.")


def training_group_id(row: Mapping[str, Any]) -> str:
    road = str(row.get("road") or "UNKNOWN_ROAD").strip()
    km = str(row.get("km_m") or row.get("km") or "UNKNOWN_KM").strip()
    return f"{road}|{km}"


def _parse_scene_date(record: Mapping[str, Any]) -> date | None:
    raw = record.get("datetime") or record.get("sentinel_scene_date")
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def select_training_scene(
    records: Sequence[Mapping[str, Any]],
    target_date: date,
    *,
    maximum_absolute_lag_days: int = TRAINING_ALIGNMENT_DAYS,
) -> dict[str, Any] | None:
    eligible: list[tuple[Mapping[str, Any], date]] = []
    for record in records:
        scene_date = _parse_scene_date(record)
        if (
            record.get("accepted_for_timeseries") is True
            and scene_date is not None
            and abs((scene_date - target_date).days) <= maximum_absolute_lag_days
        ):
            eligible.append((record, scene_date))
    if not eligible:
        return None

    def finite(value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    record, scene_date = min(
        eligible,
        key=lambda entry: (
            abs((entry[1] - target_date).days),
            -finite(entry[0].get("scene_quality_score"), -math.inf),
            -finite(entry[0].get("valid_pixel_percentage"), -math.inf),
            finite(entry[0].get("cloud_cover"), math.inf),
            str(entry[0].get("item_id") or ""),
        ),
    )
    selected = dict(record)
    selected["sentinel_scene_date"] = scene_date.isoformat()
    selected["absolute_field_scene_lag_days"] = abs(
        (scene_date - target_date).days
    )
    return selected


def aggregate_sample_candidate_features(
    row: Mapping[str, Any],
    candidates: Sequence[CandidatePolygon],
    observations: Mapping[str, Mapping[date, Mapping[str, Any] | None]],
) -> dict[str, Any] | None:
    target_date, target_source = resolve_filename_target_date(row)
    valid = [
        observation
        for candidate in candidates
        if (
            observation := observations.get(candidate.feature.feature_id, {}).get(
                target_date
            )
        )
        is not None
    ]
    if not valid:
        return None
    feature_values = {
        feature: [float(observation[feature]) for observation in valid]
        for feature in MODEL_FEATURES
    }
    if not all(
        values and all(math.isfinite(value) for value in values)
        for values in feature_values.values()
    ):
        return None
    geometry_ids = sorted(
        {
            str(observation["candidate_geometry_id"])
            for observation in valid
        }
    )
    item_ids = sorted({str(observation["item_id"]) for observation in valid})
    return {
        "sample_id": str(row["sample_id"]),
        "group_id": training_group_id(row),
        "geometry_group_id": ";".join(geometry_ids),
        "height_class": int(float(str(row["height_level"]))),
        "target": binary_height_target(row["height_level"]),
        "field_date": target_date.isoformat(),
        "field_date_source": target_source,
        "training_temporal_alignment": TRAINING_ALIGNMENT,
        "maximum_absolute_field_scene_lag_days": TRAINING_ALIGNMENT_DAYS,
        "absolute_field_scene_lag_days": float(
            np.median(
                [
                    float(observation["absolute_field_scene_lag_days"])
                    for observation in valid
                ]
            )
        ),
        "sentinel_item_id": ";".join(item_ids),
        "candidate_polygon_count": len(candidates),
        "candidate_with_valid_scene_count": len(valid),
        "candidate_polygons_are_ground_truth": False,
        "vegetation_fraction": float(
            np.median([float(item["vegetation_fraction"]) for item in valid])
        ),
        "height_valid_pixel_count": int(
            round(np.median([float(item["height_valid_pixel_count"]) for item in valid]))
        ),
        "height_total_pixel_count": int(
            round(np.median([float(item["height_total_pixel_count"]) for item in valid]))
        ),
        "mixed_pixel_risk": max(
            (str(item["mixed_pixel_risk"]) for item in valid),
            key={"low": 0, "medium": 1, "high": 2}.__getitem__,
        ),
        **{
            feature: float(np.median(values))
            for feature, values in feature_values.items()
        },
    }


def build_training_pipeline() -> Pipeline:
    return _shared_build_training_pipeline()


def _training_arrays(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return _shared_training_arrays(rows)


def grouped_validation_predictions(
    x: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> tuple[np.ndarray | None, int, list[str]]:
    return _shared_grouped_validation_predictions(x, y, groups)


def experimental_validation_metrics(
    y: np.ndarray, probabilities: np.ndarray | None
) -> dict[str, Any]:
    return _shared_experimental_validation_metrics(y, probabilities)


def train_artifact(
    rows: Sequence[Mapping[str, Any]],
    *,
    training_timestamp: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x, y, groups = _training_arrays(ordered)
    probabilities, fold_count, limitations = grouped_validation_predictions(x, y, groups)
    metrics = experimental_validation_metrics(y, probabilities)
    model = build_training_pipeline()
    model.fit(x, y)
    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["classifier"]
    canonical_rows = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
    return {
        "model_version": MODEL_VERSION,
        "features": list(MODEL_FEATURES),
        "scaler_mean": [float(value) for value in scaler.mean_],
        "scaler_scale": [float(value) for value in scaler.scale_],
        "coefficients": [float(value) for value in classifier.coef_[0]],
        "intercept": float(classifier.intercept_[0]),
        "training_sample_count": len(ordered),
        "positive_sample_count": int(np.count_nonzero(y == 1)),
        "negative_sample_count": int(np.count_nonzero(y == 0)),
        "group_count": len(set(str(value) for value in groups)),
        "geometry_group_count": len(
            {str(row["geometry_group_id"]) for row in ordered}
        ),
        "fold_count": fold_count,
        "training_alignment_days": TRAINING_ALIGNMENT_DAYS,
        "training_temporal_alignment": TRAINING_ALIGNMENT,
        "validation_strategy": "StratifiedGroupKFold_by_KM_local",
        "experimental_validation_metrics": metrics,
        "validation_metrics": metrics,
        "training_timestamp": training_timestamp
        or datetime.now(timezone.utc).isoformat(),
        "training_dataset_sha256": hashlib.sha256(
            canonical_rows.encode("utf-8")
        ).hexdigest(),
        "limitations": [
            "Experimental decision support; not a physical field measurement.",
            "Candidate polygons are ambiguous and are not ground truth.",
            "Offline training allows nearest valid scenes within +/-5 days and is not causal inference.",
            "Spatially dependent samples are validated by KM/local groups.",
            *limitations,
        ],
    }


def write_model_artifact(path: str | Path, artifact: Mapping[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dict(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def build_real_training_samples(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    *,
    query_scenes: Callable[..., tuple[list[dict[str, Any]], str | None]] = (
        query_training_scenes
    ),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for row in load_calibration_rows(dataset):
        try:
            resolve_filename_target_date(row)
            binary_height_target(row.get("height_level"))
        except ValueError:
            continue
        rows.append(row)
    features = load_management_features(management_kmz)
    markers = load_km_markers(km_markers_kmz)
    candidate_sets = build_candidate_polygon_sets(
        rows, features, markers, scenarios=TRAINING_SCENARIOS
    )
    feature_by_id: dict[str, ManagementFeature] = {}
    for row in rows:
        for candidate in candidate_sets[str(row["sample_id"])]["D50"]:
            feature_by_id[candidate.feature.feature_id] = candidate.feature

    observations: dict[str, dict[date, Mapping[str, Any] | None]] = {}
    feature_cache: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    query_start = min(resolve_filename_target_date(row)[0] for row in rows) - timedelta(
        days=TRAINING_ALIGNMENT_DAYS
    )
    query_end = max(resolve_filename_target_date(row)[0] for row in rows) + timedelta(
        days=TRAINING_ALIGNMENT_DAYS
    )
    targets = sorted({resolve_filename_target_date(row)[0] for row in rows})
    for feature in sorted(feature_by_id.values(), key=lambda item: item.feature_id):
        records, query_error = query_scenes(feature.geometry, query_start, query_end)
        if query_error:
            errors.append(
                {
                    "candidate_geometry_id": feature.feature_id,
                    "stage": "scene_query",
                    "error": query_error,
                }
            )
        observations[feature.feature_id] = {}
        for target in targets:
            selected = select_training_scene(records, target)
            if selected is None or selected.get("_scene_item") is None:
                observations[feature.feature_id][target] = None
                continue
            item = selected["_scene_item"]
            item_id = str(selected.get("item_id") or getattr(item, "id", ""))
            cache_key = (feature.feature_id, item_id)
            if cache_key not in feature_cache:
                try:
                    raster = read_scene_bands(item, mapping(feature.geometry))
                    extracted = extract_height_features(item, raster)
                    if not extracted["height_purity_gate_passed"]:
                        observations[feature.feature_id][target] = None
                        continue
                    feature_cache[cache_key] = {
                        "red_reflectance": extracted["red_median_reflectance"],
                        "nir_reflectance": extracted["nir_median_reflectance"],
                        "ndvi": extracted["ndvi_median"],
                        "vegetation_fraction": extracted["vegetation_fraction"],
                        "height_valid_pixel_count": extracted[
                            "height_valid_pixel_count"
                        ],
                        "height_total_pixel_count": extracted[
                            "height_total_pixel_count"
                        ],
                        "mixed_pixel_risk": extracted["mixed_pixel_risk"],
                    }
                except Exception as exc:
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "feature_read",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    observations[feature.feature_id][target] = None
                    continue
            observations[feature.feature_id][target] = {
                **feature_cache[cache_key],
                "candidate_geometry_id": feature.feature_id,
                "item_id": item_id,
                "sentinel_scene_date": selected["sentinel_scene_date"],
                "absolute_field_scene_lag_days": selected[
                    "absolute_field_scene_lag_days"
                ],
            }

    training_rows = [
        aggregated
        for row in rows
        if (
            aggregated := aggregate_sample_candidate_features(
                row,
                candidate_sets[str(row["sample_id"])]["D50"],
                observations,
            )
        )
        is not None
    ]
    diagnostics = {
        "input_record_count": len(rows),
        "records_with_D50_candidates": sum(
            bool(candidate_sets[str(row["sample_id"])]["D50"]) for row in rows
        ),
        "training_sample_count": len(training_rows),
        "unique_candidate_polygons_processed": len(feature_by_id),
        "unique_polygon_scene_reads": len(feature_cache),
        "errors": errors,
    }
    return training_rows, diagnostics


def _write_training_samples(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["sample_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train experimental height estimator v0.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--management-kmz", required=True, type=Path)
    parser.add_argument("--km-markers-kmz", required=True, type=Path)
    parser.add_argument(
        "--model-output", type=Path, default=Path("models/height_estimator_v0.json")
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_report.json"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    started = datetime.now(timezone.utc)
    rows, diagnostics = build_real_training_samples(
        arguments.dataset, arguments.management_kmz, arguments.km_markers_kmz
    )
    artifact = train_artifact(rows)
    model_path = write_model_artifact(arguments.model_output, artifact)
    samples_path = arguments.report_output.with_name("training_samples.csv")
    _write_training_samples(samples_path, rows)
    report = {
        "model_artifact": str(model_path),
        "training_samples_csv": str(samples_path),
        "elapsed_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        "diagnostics": diagnostics,
        **{
            key: artifact[key]
            for key in (
                "training_sample_count",
                "positive_sample_count",
                "negative_sample_count",
                "group_count",
                "fold_count",
                "experimental_validation_metrics",
            )
        },
    }
    arguments.report_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
