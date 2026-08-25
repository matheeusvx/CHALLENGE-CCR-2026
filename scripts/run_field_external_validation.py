"""Validação externa das duas medições de campo de 2026-08-21."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from shapely.geometry import shape

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_height_ablation import C2_FEATURES, _read_baseline_samples, _write_csv
from scripts.run_height_temporal_experiment import (
    EXPERIMENT_FEATURES as TEMPORAL_FEATURES,
    _collect_feature_histories,
)
from src.satellite_monitoring.datasets.training_scenes import query_training_scenes
from src.satellite_monitoring.datasets.spatial_matching import load_km_markers
from src.satellite_monitoring.experiments.field_external_validation import (
    BUFFER_RADII_M,
    ExternalFieldSample,
    assert_external_holdout_excluded,
    classification_result,
    field_scene_eligibility,
    load_external_holdouts,
    metric_buffer_geojson,
    projected_area_m2,
    score_with_frozen_pipeline,
    select_nearest_field_scene,
    select_strict_causal_scene,
    spatial_stability,
)
from src.satellite_monitoring.experiments.sentinel2_ablation import (
    HeightMaskRejectedError,
    read_multiband_height_features,
)
from src.satellite_monitoring.experiments.sentinel2_temporal import (
    TEMPORAL_LOOKBACK_DAYS,
    TemporalObservation,
    build_temporal_feature_stages,
    parse_scene_date,
    strictly_historical_records,
)
from src.satellite_monitoring.features.vegetation_mask import extract_height_features
from src.satellite_monitoring.height_estimation import estimate_height_class
from src.satellite_monitoring.models.validation import build_training_pipeline
from src.satellite_monitoring.raster_processing import read_scene_bands

SCENE_QUERY_LOOKBACK_DAYS = 60
FIELD_VIDEO_LIMITATION = "field videos include surrounding heterogeneous vegetation"
AOI_LIMITATION = "circular sensitivity buffer is not exact field vegetation geometry"


def _write_reconstructed_old_training_metadata(
    rows: Sequence[Mapping[str, Any]], km_markers_kmz: Path, destination: Path
) -> None:
    """Reconstrói somente provenance/KM dos IDs antigos; não copia dataset bruto."""
    markers = load_km_markers(km_markers_kmz)
    fields = (
        "sample_id", "km", "height_level", "asset_component",
        "source_snapshot_date", "coordinate_method", "latitude", "longitude",
        "mowing_method_km_bucket", "mowing_method_dominant",
    )
    reconstructed: list[dict[str, Any]] = []
    for row in rows:
        sample_id = str(row["sample_id"])
        parts = sample_id.split("_")
        if len(parts) < 4 or not parts[1].isdigit() or not parts[-1].isdigit():
            raise ValueError(f"Cannot reconstruct old training metadata: {sample_id}")
        snapshot = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
        km_m = int(parts[-1])
        km = km_m / 1000.0
        component = "_".join(parts[2:-1])
        lower = math.floor(km)
        upper = math.ceil(km)
        if lower not in markers or upper not in markers:
            raise ValueError(f"KM markers unavailable for reconstructed sample: {sample_id}")
        if lower == upper:
            latitude = markers[lower].latitude
            longitude = markers[lower].longitude
            method = "EXACT_KM_MARKER"
        else:
            fraction = km - lower
            latitude = markers[lower].latitude + fraction * (
                markers[upper].latitude - markers[lower].latitude
            )
            longitude = markers[lower].longitude + fraction * (
                markers[upper].longitude - markers[lower].longitude
            )
            method = "LINEAR_INTERPOLATION_BETWEEN_KM_MARKERS"
        reconstructed.append(
            {
                "sample_id": sample_id,
                "km": km,
                "height_level": int(row["height_class"]),
                "asset_component": component,
                "source_snapshot_date": snapshot,
                "coordinate_method": method,
                "latitude": latitude,
                "longitude": longitude,
                "mowing_method_km_bucket": "",
                "mowing_method_dominant": "",
            }
        )
    assert_external_holdout_excluded(reconstructed)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(reconstructed)


def _fit_reproduced_challenger(
    rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str]
) -> Any:
    assert_external_holdout_excluded(rows)
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x = np.asarray([[float(row[name]) for name in feature_names] for row in ordered])
    y = np.asarray([int(row["target"]) for row in ordered])
    if not np.all(np.isfinite(x)) or set(y.tolist()) != {0, 1}:
        raise ValueError("Frozen challenger reproduction cohort is invalid.")
    model = build_training_pipeline()
    model.fit(x, y)
    return model


def _scene_payload(
    record: Mapping[str, Any],
    *,
    sample_id: str,
    radius_m: int,
    geometry: Mapping[str, Any],
    cache: dict[tuple[str, int, str], dict[str, Any]],
) -> dict[str, Any]:
    item = record.get("_scene_item")
    item_id = str(record.get("item_id") or getattr(item, "id", ""))
    key = (sample_id, radius_m, item_id)
    if key in cache:
        return cache[key]
    payload: dict[str, Any] = {"item_id": item_id, "item": item}
    try:
        raster = read_scene_bands(item, dict(geometry))
        payload["v0_features"] = extract_height_features(item, raster)
        payload["v0_error"] = None
    except Exception as exc:
        payload["v0_features"] = None
        payload["v0_error"] = f"{type(exc).__name__}: {exc}"
    try:
        multiband = read_multiband_height_features(item, dict(geometry))
        payload["c2"] = multiband.to_dict()
        payload["c2_error"] = None
        payload["c2_rejection"] = None
    except HeightMaskRejectedError as exc:
        payload["c2"] = None
        payload["c2_error"] = None
        payload["c2_rejection"] = dict(exc.diagnostics)
    except Exception as exc:
        payload["c2"] = None
        payload["c2_error"] = f"{type(exc).__name__}: {exc}"
        payload["c2_rejection"] = None
    cache[key] = payload
    return payload


def _base_result(
    sample: ExternalFieldSample,
    radius_m: int,
    area_m2: float,
    strategy: str,
    scene: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    scene_date = parse_scene_date(scene)
    age = (sample.field_date - scene_date).days if scene_date else None
    warnings = [FIELD_VIDEO_LIMITATION, AOI_LIMITATION]
    if sample.boundary_case:
        warnings.append("30 cm is a confirmed <=30 cm boundary case")
    if age is not None and age < 0:
        warnings.append("diagnostic scene is future relative to field measurement")
    if scene.get("field_external_scene_selection") == "field_small_aoi_minimum_pixel_count_override":
        warnings.append(
            "offline field validation override: only operational absolute minimum pixel count was waived"
        )
    return {
        "external_holdout": True,
        "sample_id": sample.sample_id,
        "field_date": sample.field_date.isoformat(),
        "latitude": sample.latitude,
        "longitude": sample.longitude,
        "measured_height_cm": sample.measured_height_cm,
        "real_class": sample.real_class,
        "boundary_case": sample.boundary_case,
        "radius_m": radius_m,
        "area_m2": area_m2,
        "scene_strategy": strategy,
        "sentinel_item_id": str(scene.get("item_id") or ""),
        "scene_date": scene_date.isoformat() if scene_date else None,
        "scene_age_days": age,
        "future_relative_to_field": bool(age is not None and age < 0),
        "model": model,
        "score_gt_30_cm": None,
        "estimated_class": None,
        "status": "unavailable",
        "confidence": None,
        "model_version": None,
        "classification_result": "unavailable",
        "vegetation_fraction": None,
        "mixed_pixel_risk": None,
        "height_valid_pixel_count": None,
        "height_total_pixel_count": None,
        "temporal_history_available": False,
        "historical_scene_count": 0,
        "previous_scene_date": None,
        "temporal_gap_days": None,
        "features_available": False,
        "warnings": warnings,
    }


def _copy_mask_metadata(result: dict[str, Any], values: Mapping[str, Any] | None) -> None:
    if not values:
        return
    for key in (
        "vegetation_fraction",
        "mixed_pixel_risk",
        "height_valid_pixel_count",
        "height_total_pixel_count",
    ):
        result[key] = values.get(key)


def _v0_result(base: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    features = payload.get("v0_features")
    if features is None:
        result["warnings"] = [*result["warnings"], str(payload.get("v0_error"))]
        return result
    inferred = estimate_height_class(features)
    result.update(
        {
            "status": inferred["status"],
            "score_gt_30_cm": inferred["score_gt_30_cm"],
            "estimated_class": inferred["estimated_class"],
            "confidence": inferred["confidence"],
            "model_version": inferred["model_version"],
            "features_available": inferred["status"] != "unavailable",
        }
    )
    _copy_mask_metadata(result, inferred)
    result["classification_result"] = classification_result(
        result["real_class"], result["estimated_class"]
    )
    return result


def _c2_result(
    base: dict[str, Any], payload: Mapping[str, Any], model: Any
) -> dict[str, Any]:
    result = dict(base)
    c2 = payload.get("c2")
    rejection = payload.get("c2_rejection")
    if rejection is not None:
        _copy_mask_metadata(result, rejection)
        result.update(
            {
                "status": "experimental",
                "estimated_class": "inconclusive",
                "confidence": "low",
                "model_version": "C2-reproduced-old-cohort",
                "features_available": False,
                "classification_result": "abstained",
                "warnings": [
                    *result["warnings"],
                    "height mask rejected C2: "
                    + ", ".join(rejection.get("height_purity_gate_reasons") or []),
                ],
            }
        )
        return result
    if c2 is None:
        result["warnings"] = [*result["warnings"], str(payload.get("c2_error"))]
        return result
    inferred = score_with_frozen_pipeline(model, C2_FEATURES, c2["values"])
    result.update(
        {
            **inferred,
            "model_version": "C2-reproduced-old-cohort",
            "features_available": True,
        }
    )
    _copy_mask_metadata(result, c2)
    result["classification_result"] = classification_result(
        result["real_class"], result["estimated_class"]
    )
    return result


def _t3_result(
    base: dict[str, Any],
    anchor_payload: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
    sample: ExternalFieldSample,
    radius_m: int,
    cache: dict[tuple[str, int, str], dict[str, Any]],
    model: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = dict(base)
    c2 = anchor_payload.get("c2")
    rejection = anchor_payload.get("c2_rejection")
    if rejection is not None:
        _copy_mask_metadata(result, rejection)
        result.update(
            {
                "status": "experimental",
                "estimated_class": "inconclusive",
                "confidence": "low",
                "model_version": "T3-reproduced-old-cohort",
                "classification_result": "abstained",
                "warnings": [*result["warnings"], "anchor rejected by height mask"],
            }
        )
        return result, None
    if c2 is None:
        result["warnings"] = [*result["warnings"], str(anchor_payload.get("c2_error"))]
        return result, None
    anchor_date = date_from_iso(result["scene_date"])
    anchor = TemporalObservation(
        anchor_date,
        result["sentinel_item_id"],
        {name: float(value) for name, value in c2["values"].items()},
    )
    historical: list[TemporalObservation] = []
    rejected = Counter()
    for record in strictly_historical_records(records, anchor_date):
        payload = _scene_payload(
            record,
            sample_id=sample.sample_id,
            radius_m=radius_m,
            geometry=geometry,
            cache=cache,
        )
        historic_c2 = payload.get("c2")
        if historic_c2 is None:
            rejected["height_mask" if payload.get("c2_rejection") else "invalid_feature"] += 1
            continue
        scene_date = parse_scene_date(record)
        if scene_date is None or scene_date >= anchor_date:
            continue
        historical.append(
            TemporalObservation(
                scene_date,
                str(record.get("item_id") or ""),
                {name: float(value) for name, value in historic_c2["values"].items()},
            )
        )
    stages = build_temporal_feature_stages(anchor, historical)
    result.update(
        {
            "historical_scene_count": len(historical),
            "temporal_history_available": stages.t3 is not None,
            "previous_scene_date": stages.previous_scene_date.isoformat()
            if stages.previous_scene_date else None,
            "temporal_gap_days": stages.temporal_gap_days,
        }
    )
    _copy_mask_metadata(result, c2)
    temporal_row = {
        "sample_id": sample.sample_id,
        "radius_m": radius_m,
        "scene_strategy": result["scene_strategy"],
        "anchor_scene_date": anchor_date.isoformat(),
        "previous_scene_date": result["previous_scene_date"],
        "temporal_gap_days": stages.temporal_gap_days,
        "historical_scene_count": len(historical),
        "observation_count_30d": stages.observation_count_30d,
        "observation_count_60d": stages.observation_count_60d,
        "invalid_reason": stages.invalid_reason_t3,
        "rejected_historical_scenes": dict(rejected),
    }
    if stages.t3 is None:
        result.update(
            {
                "status": "insufficient_temporal_history",
                "estimated_class": None,
                "model_version": "T3-reproduced-old-cohort",
                "features_available": False,
                "classification_result": "unavailable",
                "warnings": [
                    *result["warnings"],
                    f"T3 unavailable: {stages.invalid_reason_t3}",
                ],
            }
        )
        return result, temporal_row
    features = {**c2["values"], **stages.t3}
    inferred = score_with_frozen_pipeline(model, TEMPORAL_FEATURES["T3"], features)
    result.update(
        {
            **inferred,
            "model_version": "T3-reproduced-old-cohort",
            "features_available": True,
        }
    )
    result["classification_result"] = classification_result(
        result["real_class"], result["estimated_class"]
    )
    temporal_row.update({name: features[name] for name in TEMPORAL_FEATURES["T3"]})
    return result, temporal_row


def date_from_iso(value: str) -> Any:
    from datetime import date

    return date.fromisoformat(value)


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    samples, dataset_diagnostics = load_external_holdouts(arguments.dataset)
    old_baseline = _read_baseline_samples(arguments.baseline_samples)
    assert_external_holdout_excluded(old_baseline)
    training_metadata_source = "provided_dataset"
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    training_dataset = arguments.dataset
    if not training_dataset.exists():
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="motiva_old_training_metadata_"
        )
        training_dataset = Path(temporary_directory.name) / "old_training_metadata.csv"
        _write_reconstructed_old_training_metadata(
            old_baseline, arguments.km_markers_kmz, training_dataset
        )
        training_metadata_source = "temporary_reconstruction_from_frozen_training_sample_ids"
    try:
        old_variants, _, _, old_collection = _collect_feature_histories(
            old_baseline,
            training_dataset,
            arguments.management_kmz,
            arguments.km_markers_kmz,
        )
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()
    assert_external_holdout_excluded(old_variants["T0"])
    assert_external_holdout_excluded(old_variants["T3"])
    c2_model = _fit_reproduced_challenger(old_variants["T0"], C2_FEATURES)
    t3_model = _fit_reproduced_challenger(
        old_variants["T3"], TEMPORAL_FEATURES["T3"]
    )

    results: list[dict[str, Any]] = []
    scene_diagnostics: list[dict[str, Any]] = []
    temporal_rows: list[dict[str, Any]] = []
    payload_cache: dict[tuple[str, int, str], dict[str, Any]] = {}
    scene_counts: dict[str, dict[str, int]] = {}
    for sample in samples:
        scene_counts[sample.sample_id] = {
            "found": 0,
            "accepted_for_timeseries": 0,
            "eligible_external_validation": 0,
        }
        for radius_m in BUFFER_RADII_M:
            geometry = metric_buffer_geojson(
                sample.latitude, sample.longitude, radius_m
            )
            area_m2 = projected_area_m2(geometry)
            records, query_error = query_training_scenes(
                shape(geometry),
                sample.field_date - timedelta(days=SCENE_QUERY_LOOKBACK_DAYS),
                sample.field_date + timedelta(days=5),
            )
            scene_counts[sample.sample_id]["found"] += len(records)
            scene_counts[sample.sample_id]["accepted_for_timeseries"] += sum(
                record.get("accepted_for_timeseries") is True for record in records
            )
            scene_counts[sample.sample_id]["eligible_external_validation"] += sum(
                field_scene_eligibility(record)[0] for record in records
            )
            strict = select_strict_causal_scene(records, sample.field_date)
            nearest = select_nearest_field_scene(records, sample.field_date)
            selected = {
                "strict_causal_scene": strict,
                "diagnostic_nearest_scene": nearest,
            }
            for record in records:
                record_date = parse_scene_date(record)
                scene_diagnostics.append(
                    {
                        "sample_id": sample.sample_id,
                        "radius_m": radius_m,
                        "item_id": record.get("item_id"),
                        "scene_date": record_date.isoformat() if record_date else None,
                        "accepted_for_timeseries": record.get("accepted_for_timeseries"),
                        "field_external_eligible": field_scene_eligibility(record)[0],
                        "field_external_eligibility_method": field_scene_eligibility(record)[1],
                        "processing_status": record.get("processing_status"),
                        "quality_status": record.get("quality_status"),
                        "scene_quality_score": record.get("scene_quality_score"),
                        "valid_pixel_percentage": record.get("valid_pixel_percentage"),
                        "valid_pixel_count": record.get("valid_pixel_count"),
                        "aoi_coverage_percentage": record.get("aoi_coverage_percentage"),
                        "cloud_cover": record.get("cloud_cover"),
                        "quality_reasons": record.get("quality_reasons"),
                        "query_error": query_error,
                        "selected_strict_causal": bool(strict and strict.get("item_id") == record.get("item_id")),
                        "selected_diagnostic_nearest": bool(nearest and nearest.get("item_id") == record.get("item_id")),
                    }
                )
            for strategy, scene in selected.items():
                if scene is None:
                    for model_name in ("V0", "C2", "T3"):
                        unavailable = {
                            **sample.to_dict(),
                            "external_holdout": True,
                            "radius_m": radius_m,
                            "area_m2": area_m2,
                            "scene_strategy": strategy,
                            "model": model_name,
                            "status": "unavailable",
                            "estimated_class": None,
                            "score_gt_30_cm": None,
                            "classification_result": "unavailable",
                            "warnings": ["no valid scene", FIELD_VIDEO_LIMITATION, AOI_LIMITATION],
                        }
                        results.append(unavailable)
                    continue
                payload = _scene_payload(
                    scene,
                    sample_id=sample.sample_id,
                    radius_m=radius_m,
                    geometry=geometry,
                    cache=payload_cache,
                )
                v0_base = _base_result(sample, radius_m, area_m2, strategy, scene, "V0")
                c2_base = _base_result(sample, radius_m, area_m2, strategy, scene, "C2")
                t3_base = _base_result(sample, radius_m, area_m2, strategy, scene, "T3")
                results.append(_v0_result(v0_base, payload))
                results.append(_c2_result(c2_base, payload, c2_model))
                t3_result, temporal_row = _t3_result(
                    t3_base,
                    payload,
                    records,
                    geometry,
                    sample,
                    radius_m,
                    payload_cache,
                    t3_model,
                )
                results.append(t3_result)
                if temporal_row is not None:
                    temporal_rows.append(temporal_row)

    sensitivity_rows: list[dict[str, Any]] = []
    for sample in samples:
        for strategy in ("strict_causal_scene", "diagnostic_nearest_scene"):
            for model_name in ("V0", "C2", "T3"):
                selected_results = [
                    row for row in results
                    if row["sample_id"] == sample.sample_id
                    and row["scene_strategy"] == strategy
                    and row["model"] == model_name
                ]
                sensitivity_rows.append(
                    {
                        "sample_id": sample.sample_id,
                        "scene_strategy": strategy,
                        "model": model_name,
                        **spatial_stability(selected_results),
                        "classes_by_radius": {
                            str(row["radius_m"]): row.get("estimated_class")
                            for row in selected_results
                        },
                        "scores_by_radius": {
                            str(row["radius_m"]): row.get("score_gt_30_cm")
                            for row in selected_results
                        },
                    }
                )

    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "field_validation_results.csv", results)
    _write_csv(
        output_dir / "field_validation_spatial_sensitivity.csv", sensitivity_rows
    )
    _write_csv(output_dir / "field_validation_scene_diagnostics.csv", scene_diagnostics)
    if temporal_rows:
        _write_csv(output_dir / "field_validation_temporal_features.csv", temporal_rows)
    summary = {
        "external_holdout": True,
        "production_changes": False,
        "field_date": "2026-08-21",
        "dataset_diagnostics": dataset_diagnostics,
        "samples": [sample.to_dict() for sample in samples],
        "holdout_protection": {
            "holdout_ids": [sample.sample_id for sample in samples],
            "used_in_any_fit": False,
            "old_baseline_training_sample_count": len(old_baseline),
            "c2_reproduced_fit_sample_count": len(old_variants["T0"]),
            "t3_reproduced_fit_sample_count": len(old_variants["T3"]),
            "challenger_artifacts_written": False,
            "training_metadata_source": training_metadata_source,
            "temporary_training_metadata_deleted": temporary_directory is not None,
        },
        "scene_counts": scene_counts,
        "results": results,
        "spatial_sensitivity": sensitivity_rows,
        "old_training_collection": {
            "unique_anchor_items": old_collection["unique_anchor_items"],
            "unique_historical_items": old_collection["unique_historical_items"],
        },
        "limitations": [FIELD_VIDEO_LIMITATION, AOI_LIMITATION],
        "execution_time_seconds": time.perf_counter() - started,
    }
    (output_dir / "field_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="External field holdout validation.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--management-kmz", required=True, type=Path)
    parser.add_argument("--km-markers-kmz", required=True, type=Path)
    parser.add_argument(
        "--baseline-samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/field_external_validation_20260821"),
    )
    return parser.parse_args()


def main() -> int:
    summary = run(_arguments())
    print(json.dumps({
        "output": "field_validation_summary.json",
        "dataset_diagnostics": summary["dataset_diagnostics"],
        "scene_counts": summary["scene_counts"],
        "execution_time_seconds": summary["execution_time_seconds"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
