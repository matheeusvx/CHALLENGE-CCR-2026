"""Executa a ablacao offline A/B/C1/C2 do estimador de faixa de altura."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.train_height_estimator import select_training_scene
from src.satellite_monitoring.datasets.spatial_matching import (
    CandidatePolygon,
    build_candidate_polygon_sets,
    load_calibration_rows,
    load_km_markers,
    load_management_features,
    resolve_filename_target_date,
)
from src.satellite_monitoring.datasets.training_scenes import query_training_scenes
from src.satellite_monitoring.experiments.sentinel2_ablation import (
    MULTIBAND_ASSET_SPECS,
    MULTIBAND_INDEX_FEATURES,
    MULTIBAND_RAW_FEATURES,
    HeightMaskRejectedError,
    MissingMultibandAssetError,
    common_sample_ids,
    fixed_fold_oof_scores,
    read_multiband_height_features,
    shared_group_fold_assignment,
    classification_metrics,
)
from src.satellite_monitoring.features.vegetation_mask import (
    HeightMaskConfig,
    extract_height_features,
)
from src.satellite_monitoring.models.grass_threshold import (
    HIGH_DECISION_THRESHOLD,
    LOW_DECISION_THRESHOLD,
    MODEL_FEATURES,
    MODEL_VERSION,
)
from src.satellite_monitoring.models.validation import (
    build_training_pipeline,
    risk_coverage_table,
    selective_validation_metrics,
)
from src.satellite_monitoring.raster_processing import read_scene_bands

SCENARIOS = {"D50": 50.0}
BASE_FEATURES = tuple(MODEL_FEATURES)
C1_FEATURES = BASE_FEATURES + MULTIBAND_RAW_FEATURES
C2_FEATURES = C1_FEATURES + MULTIBAND_INDEX_FEATURES
EXPERIMENT_FEATURES = {
    "A": BASE_FEATURES,
    "B": BASE_FEATURES,
    "C1": C1_FEATURES,
    "C2": C2_FEATURES,
}


def _read_baseline_samples(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    for row in rows:
        row["target"] = int(row["target"])
        row["height_class"] = int(row["height_class"])
        for feature in BASE_FEATURES:
            row[feature] = float(row[feature])
    return sorted(rows, key=lambda row: str(row["sample_id"]))


def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _aggregate_observations(
    baseline: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
) -> dict[str, Any] | None:
    if not observations:
        return None
    values = {
        feature: [float(observation[feature]) for observation in observations]
        for feature in feature_names
    }
    if not all(items and all(math.isfinite(value) for value in items) for items in values.values()):
        return None
    risks = {"low": 0, "medium": 1, "high": 2}
    return {
        "sample_id": str(baseline["sample_id"]),
        "group_id": str(baseline["group_id"]),
        "target": int(baseline["target"]),
        "height_class": int(baseline["height_class"]),
        "candidate_with_valid_scene_count": len(observations),
        "candidate_geometry_ids": sorted(
            {str(item["candidate_geometry_id"]) for item in observations}
        ),
        "sentinel_item_ids": sorted({str(item["item_id"]) for item in observations}),
        "vegetation_fraction": _median(
            [float(item["vegetation_fraction"]) for item in observations]
        ),
        "height_valid_pixel_count": int(
            round(_median([float(item["height_valid_pixel_count"]) for item in observations]))
        ),
        "height_total_pixel_count": int(
            round(_median([float(item["height_total_pixel_count"]) for item in observations]))
        ),
        "mixed_pixel_risk": max(
            (str(item["mixed_pixel_risk"]) for item in observations), key=risks.__getitem__
        ),
        **{feature: _median(items) for feature, items in values.items()},
    }


def _collect_features(
    baseline_rows: Sequence[Mapping[str, Any]],
    dataset: Path,
    management_kmz: Path,
    km_markers_kmz: Path,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    baseline_by_id = {str(row["sample_id"]): row for row in baseline_rows}
    dataset_rows = {
        str(row["sample_id"]): row
        for row in load_calibration_rows(dataset)
        if str(row.get("sample_id") or "") in baseline_by_id
    }
    selected_rows = [dataset_rows[str(row["sample_id"])] for row in baseline_rows]
    features = load_management_features(management_kmz)
    markers = load_km_markers(km_markers_kmz)
    candidate_sets = build_candidate_polygon_sets(
        selected_rows, features, markers, scenarios=SCENARIOS
    )
    feature_by_id = {
        candidate.feature.feature_id: candidate.feature
        for row in selected_rows
        for candidate in candidate_sets[str(row["sample_id"])]["D50"]
    }
    targets = sorted({resolve_filename_target_date(row)[0] for row in selected_rows})
    query_start = min(targets) - timedelta(days=5)
    query_end = max(targets) + timedelta(days=5)
    observations: dict[str, dict[date, dict[str, Any] | None]] = {}
    failure_by_feature_target: dict[tuple[str, date], str] = {}
    height_rejection_by_feature_target: dict[tuple[str, date], dict[str, Any]] = {}
    unique_items: set[str] = set()
    b_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    c_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    failure_counts = {
        "scene_quality_or_alignment": 0,
        "height_mask": 0,
        "band_missing": 0,
        "multiband_quality": 0,
    }
    errors: list[dict[str, str]] = []

    for feature in sorted(feature_by_id.values(), key=lambda value: value.feature_id):
        records, query_error = query_training_scenes(feature.geometry, query_start, query_end)
        if query_error:
            errors.append(
                {"candidate_geometry_id": feature.feature_id, "stage": "query", "error": query_error}
            )
        observations[feature.feature_id] = {}
        for target in targets:
            selected = select_training_scene(records, target)
            if selected is None or selected.get("_scene_item") is None:
                observations[feature.feature_id][target] = None
                failure_by_feature_target[(feature.feature_id, target)] = (
                    "scene_quality_or_alignment"
                )
                failure_counts["scene_quality_or_alignment"] += 1
                continue
            item = selected["_scene_item"]
            item_id = str(selected.get("item_id") or getattr(item, "id", ""))
            unique_items.add(item_id)
            cache_key = (feature.feature_id, item_id)
            if cache_key not in b_cache:
                try:
                    raster = read_scene_bands(item, mapping(feature.geometry))
                    extracted = extract_height_features(item, raster)
                    b_cache[cache_key] = extracted
                    if not extracted["height_purity_gate_passed"]:
                        failure_counts["height_mask"] += 1
                except Exception as exc:
                    b_cache[cache_key] = None
                    failure_counts["height_mask"] += 1
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "height_mask_10m",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            if cache_key not in c_cache:
                try:
                    multiband = read_multiband_height_features(
                        item, mapping(feature.geometry)
                    )
                    c_cache[cache_key] = multiband.to_dict()
                except MissingMultibandAssetError as exc:
                    c_cache[cache_key] = None
                    failure_counts["band_missing"] += 1
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "multiband",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                except HeightMaskRejectedError as exc:
                    c_cache[cache_key] = None
                    failure_counts["height_mask"] += 1
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "height_mask_20m",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                except Exception as exc:
                    c_cache[cache_key] = None
                    failure_counts["multiband_quality"] += 1
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "multiband",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            b_diagnostic = b_cache[cache_key]
            b = (
                b_diagnostic
                if b_diagnostic is not None
                and b_diagnostic["height_purity_gate_passed"]
                else None
            )
            c = c_cache[cache_key]
            if b is None:
                failure_by_feature_target[(feature.feature_id, target)] = "height_mask"
                if b_diagnostic is not None:
                    height_rejection_by_feature_target[(feature.feature_id, target)] = {
                        "reasons": list(b_diagnostic["height_purity_gate_reasons"]),
                        "mixed_pixel_risk": str(b_diagnostic["mixed_pixel_risk"]),
                        "vegetation_fraction": float(
                            b_diagnostic["vegetation_fraction"]
                        ),
                        "height_valid_pixel_count": int(
                            b_diagnostic["height_valid_pixel_count"]
                        ),
                    }
                observations[feature.feature_id][target] = None
                continue
            observations[feature.feature_id][target] = {
                "candidate_geometry_id": feature.feature_id,
                "item_id": item_id,
                "b": {
                    "red_reflectance": b["red_median_reflectance"],
                    "nir_reflectance": b["nir_median_reflectance"],
                    "ndvi": b["ndvi_median"],
                    "vegetation_fraction": b["vegetation_fraction"],
                    "height_valid_pixel_count": b["height_valid_pixel_count"],
                    "height_total_pixel_count": b["height_total_pixel_count"],
                    "mixed_pixel_risk": b["mixed_pixel_risk"],
                },
                "c": c,
            }
            if c is None:
                failure_by_feature_target[(feature.feature_id, target)] = (
                    "band_missing_or_multiband_quality"
                )

    variants: dict[str, list[dict[str, Any]]] = {name: [] for name in EXPERIMENT_FEATURES}
    variants["A"] = [dict(row) for row in baseline_rows]
    coverage_rows: list[dict[str, Any]] = []
    for baseline in baseline_rows:
        sample_id = str(baseline["sample_id"])
        source = dataset_rows[sample_id]
        target = resolve_filename_target_date(source)[0]
        candidates: list[CandidatePolygon] = candidate_sets[sample_id]["D50"]
        b_observations: list[dict[str, Any]] = []
        c_observations: list[dict[str, Any]] = []
        reasons: list[str] = []
        height_rejections: list[dict[str, Any]] = []
        for candidate in candidates:
            record = observations.get(candidate.feature.feature_id, {}).get(target)
            if record is None:
                reason = failure_by_feature_target.get(
                    (candidate.feature.feature_id, target), "unknown"
                )
                reasons.append(reason)
                rejection = height_rejection_by_feature_target.get(
                    (candidate.feature.feature_id, target)
                )
                if rejection is not None:
                    height_rejections.append(rejection)
                continue
            b_observations.append(
                {
                    **record["b"],
                    "candidate_geometry_id": candidate.feature.feature_id,
                    "item_id": record["item_id"],
                }
            )
            if record["c"] is not None:
                c_observations.append(
                    {
                        **record["b"],
                        **record["c"]["values"],
                        "candidate_geometry_id": candidate.feature.feature_id,
                        "item_id": record["item_id"],
                        "vegetation_fraction": record["c"]["vegetation_fraction"],
                        "height_valid_pixel_count": record["c"][
                            "height_valid_pixel_count"
                        ],
                        "height_total_pixel_count": record["c"][
                            "height_total_pixel_count"
                        ],
                        "mixed_pixel_risk": record["c"]["mixed_pixel_risk"],
                    }
                )
        b_row = _aggregate_observations(baseline, b_observations, BASE_FEATURES)
        c1_row = _aggregate_observations(baseline, c_observations, C1_FEATURES)
        c2_row = _aggregate_observations(baseline, c_observations, C2_FEATURES)
        if b_row:
            variants["B"].append(b_row)
        if c1_row:
            variants["C1"].append(c1_row)
        if c2_row:
            variants["C2"].append(c2_row)
        for experiment, row in (("A", baseline), ("B", b_row), ("C1", c1_row), ("C2", c2_row)):
            coverage_rows.append(
                {
                    "sample_id": sample_id,
                    "experiment": experiment,
                    "valid": row is not None,
                    "target": int(baseline["target"]),
                    "group_id": str(baseline["group_id"]),
                    "candidate_polygon_count": len(candidates),
                    "candidate_with_valid_scene_count": int(
                        row.get("candidate_with_valid_scene_count", 0)
                    )
                    if row
                    else 0,
                    "vegetation_fraction": row.get("vegetation_fraction") if row else None,
                    "mixed_pixel_risk": row.get("mixed_pixel_risk") if row else None,
                    "invalid_reason": None if row else ";".join(sorted(set(reasons))),
                    "height_mask_rejection_reasons": ";".join(
                        sorted(
                            {
                                reason
                                for rejection in height_rejections
                                for reason in rejection["reasons"]
                            }
                        )
                    )
                    or None,
                    "height_mask_mixed_pixel_risk_high": any(
                        rejection["mixed_pixel_risk"] == "high"
                        for rejection in height_rejections
                    ),
                    "height_mask_vegetation_fraction_insufficient": any(
                        "insufficient_vegetation_fraction" in rejection["reasons"]
                        for rejection in height_rejections
                    ),
                }
            )
    diagnostics = {
        "unique_candidate_polygons": len(feature_by_id),
        "unique_sentinel_items": len(unique_items),
        "unique_sentinel_item_ids": sorted(unique_items),
        "unique_polygon_scene_reads_10m": len(b_cache),
        "unique_polygon_scene_reads_20m": len(c_cache),
        "failure_counts": failure_counts,
        "height_mask_rejected_candidate_scenes": sum(
            1
            for value in b_cache.values()
            if value is not None and not value["height_purity_gate_passed"]
        ),
        "height_mask_rejection_reason_counts": {
            reason: sum(
                reason in value["height_purity_gate_reasons"]
                for value in b_cache.values()
                if value is not None and not value["height_purity_gate_passed"]
            )
            for reason in (
                "insufficient_height_valid_pixels",
                "insufficient_vegetation_fraction",
                "high_mixed_pixel_risk",
            )
        },
        "errors": errors,
        "observed_source_resolutions_m": {
            band: sorted(
                {
                    float(value["source_resolution"][band])
                    for value in c_cache.values()
                    if value is not None and band in value["source_resolution"]
                }
            )
            for band in (*MULTIBAND_ASSET_SPECS, "scl")
        },
        "height_mask_configuration": HeightMaskConfig().to_dict(),
        "resolution_strategy": {
            "baseline_features": "native_10m_scalar_summaries",
            "multiband_features": "common_B11_20m_grid",
            "continuous_resampling": "bilinear",
            "categorical_resampling": "nearest",
            "indices": {
                "NDRE": "(B8A - B05) / (B8A + B05)",
                "NDII": "(B8A - B11) / (B8A + B11)",
            },
        },
    }
    return variants, coverage_rows, diagnostics


def _stability(fold_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for metric in ("roc_auc", "pr_auc", "precision_gt_30", "recall_gt_30"):
        values = [float(row[metric]) for row in fold_rows if row.get(metric) is not None]
        result[metric] = {
            "mean": float(np.mean(values)) if values else None,
            "std": float(np.std(values)) if values else None,
            "min": float(np.min(values)) if values else None,
            "max": float(np.max(values)) if values else None,
            "folds_with_metric": len(values),
        }
    return result


def _evaluate(
    experiment: str,
    cohort: str,
    rows: Sequence[Mapping[str, Any]],
    fold_assignment: Mapping[str, int],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    feature_names = EXPERIMENT_FEATURES[experiment]
    y, scores, fold_rows = fixed_fold_oof_scores(rows, feature_names, fold_assignment)
    metrics = {
        "experiment": experiment,
        "cohort": cohort,
        "valid_samples": len(rows),
        "evaluated_samples": int(len(y)),
        "positive_samples": int(sum(int(row["target"]) for row in rows)),
        "negative_samples": int(len(rows) - sum(int(row["target"]) for row in rows)),
        **classification_metrics(y, scores),
        "fold_stability": _stability(fold_rows),
    }
    selective = {
        "experiment": experiment,
        "cohort": cohort,
        **selective_validation_metrics(
            y,
            scores,
            lower=LOW_DECISION_THRESHOLD,
            upper=HIGH_DECISION_THRESHOLD,
        ),
    }
    risks = [
        {"experiment": experiment, "cohort": cohort, **entry}
        for entry in risk_coverage_table(y, scores)
    ]
    decorated_folds = [
        {"experiment": experiment, "cohort": cohort, **row} for row in fold_rows
    ]
    return metrics, decorated_folds, selective, risks


def _coefficients(
    experiment: str, rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    features = EXPERIMENT_FEATURES[experiment]
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x = np.asarray([[float(row[name]) for name in features] for row in ordered])
    y = np.asarray([int(row["target"]) for row in ordered])
    model = build_training_pipeline()
    model.fit(x, y)
    classifier = model.named_steps["classifier"]
    return [
        {
            "experiment": experiment,
            "feature": feature,
            "standardized_coefficient": float(coefficient),
            "intercept": float(classifier.intercept_[0]),
        }
        for feature, coefficient in zip(features, classifier.coef_[0])
    ]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in row.items()
                }
            )


def _correlation_rows(
    rows: Sequence[Mapping[str, Any]], features: Sequence[str]
) -> list[dict[str, Any]]:
    matrix = np.asarray([[float(row[name]) for name in features] for row in rows])
    correlation = np.corrcoef(matrix, rowvar=False)
    return [
        {"feature": left, **{right: float(correlation[i, j]) for j, right in enumerate(features)}}
        for i, left in enumerate(features)
    ]


def _plots(
    output_dir: Path,
    metrics: Sequence[Mapping[str, Any]],
    risk_rows: Sequence[Mapping[str, Any]],
) -> None:
    natural = [row for row in metrics if row["cohort"] == "natural"]
    labels = [str(row["experiment"]) for row in natural]
    figure, axis = plt.subplots(figsize=(6, 4))
    axis.bar(labels, [float(row["pr_auc"]) for row in natural])
    axis.set_ylabel("PR-AUC OOF")
    axis.set_title("Ablacao Sentinel-2")
    figure.tight_layout()
    figure.savefig(output_dir / "pr_auc_comparison.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6, 4))
    for row in natural:
        axis.scatter(row["recall_gt_30"], row["precision_gt_30"], label=row["experiment"])
    axis.set_xlabel("Recall >30")
    axis.set_ylabel("Precision >30")
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "precision_recall_comparison.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(7, 4))
    for experiment in EXPERIMENT_FEATURES:
        selected = [
            row
            for row in risk_rows
            if row["cohort"] == "common_all" and row["experiment"] == experiment
        ]
        axis.plot(
            [float(row["decision_coverage"]) for row in selected],
            [float(row["precision_gt_30"] or 0.0) for row in selected],
            marker="o",
            label=experiment,
        )
    axis.set_xlabel("Decision coverage")
    axis.set_ylabel("Selective precision >30")
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "risk_coverage.png", dpi=150)
    plt.close(figure)


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    baseline = _read_baseline_samples(arguments.baseline_samples)
    variants, coverage_rows, collection = _collect_features(
        baseline, arguments.dataset, arguments.management_kmz, arguments.km_markers_kmz
    )
    natural_assignment, natural_folds = shared_group_fold_assignment(variants["A"])
    common_ids = common_sample_ids(*(variants[name] for name in EXPERIMENT_FEATURES))
    common_variants = {
        name: [row for row in rows if str(row["sample_id"]) in common_ids]
        for name, rows in variants.items()
    }
    common_assignment, common_folds = shared_group_fold_assignment(common_variants["A"])

    metrics_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    selective_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    common_metrics: list[dict[str, Any]] = []
    coefficient_rows: list[dict[str, Any]] = []
    for experiment in EXPERIMENT_FEATURES:
        metrics, folds, selective, risks = _evaluate(
            experiment, "natural", variants[experiment], natural_assignment
        )
        metrics_rows.append(metrics)
        fold_rows.extend(folds)
        selective_rows.append(selective)
        risk_rows.extend(risks)
        common, common_fold, common_selective, common_risks = _evaluate(
            experiment, "common_all", common_variants[experiment], common_assignment
        )
        common_metrics.append(common)
        fold_rows.extend(common_fold)
        selective_rows.append(common_selective)
        risk_rows.extend(common_risks)
        coefficient_rows.extend(_coefficients(experiment, variants[experiment]))

    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "ablation_metrics.csv", metrics_rows)
    _write_csv(output_dir / "fold_metrics.csv", fold_rows)
    _write_csv(output_dir / "selective_metrics.csv", selective_rows)
    _write_csv(output_dir / "risk_coverage.csv", risk_rows)
    _write_csv(output_dir / "sample_coverage.csv", coverage_rows)
    _write_csv(output_dir / "common_cohort_metrics.csv", common_metrics)
    _write_csv(
        output_dir / "feature_correlation_matrix.csv",
        _correlation_rows(variants["C2"], C2_FEATURES),
    )
    _write_csv(output_dir / "model_coefficients.csv", coefficient_rows)
    _plots(output_dir, metrics_rows, risk_rows)
    summary = {
        "model_version": MODEL_VERSION,
        "scope": "offline_controlled_ablation_no_production_integration",
        "target": "height_class_3_gt_30_vs_classes_1_2_le_30",
        "algorithm": "StandardScaler_plus_balanced_LogisticRegression",
        "random_seed": 20260818,
        "natural_fold_count": natural_folds,
        "common_fold_count": common_folds,
        "fold_group_assignments": {
            "natural": natural_assignment,
            "common_all": common_assignment,
        },
        "coverage": {
            name: {
                "total_requested_samples": len(baseline),
                "valid_samples": len(rows),
                "invalid_samples": len(baseline) - len(rows),
                "positive_samples": sum(int(row["target"]) for row in rows),
                "negative_samples": len(rows) - sum(int(row["target"]) for row in rows),
            }
            for name, rows in variants.items()
        },
        "common_cohort": {
            "common_cohort_n": len(common_ids),
            "common_cohort_positive_n": sum(
                int(row["target"]) for row in common_variants["A"]
            ),
            "common_cohort_negative_n": len(common_ids)
            - sum(int(row["target"]) for row in common_variants["A"]),
            "sample_ids": common_ids,
        },
        "natural_metrics": metrics_rows,
        "common_cohort_metrics": common_metrics,
        "selective_metrics": selective_rows,
        "collection_diagnostics": collection,
        "unique_spatial_groups": len({str(row["group_id"]) for row in baseline}),
        "execution_time_seconds": time.perf_counter() - started,
        "production_changes": False,
    }
    (output_dir / "ablation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled Sentinel-2 height ablation.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--management-kmz", required=True, type=Path)
    parser.add_argument("--km-markers-kmz", required=True, type=Path)
    parser.add_argument(
        "--baseline-samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs/height_ablation_sentinel2")
    )
    return parser.parse_args()


def main() -> int:
    summary = run(_arguments())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
