"""Executa T0/T1/T2/T3 temporal do estimador de altura, somente offline."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_height_ablation import (
    C2_FEATURES,
    _aggregate_observations,
    _correlation_rows,
    _read_baseline_samples,
    _stability,
    _write_csv,
)
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
    HeightMaskRejectedError,
    MissingMultibandAssetError,
    classification_metrics,
    common_sample_ids,
    fixed_fold_oof_scores,
    read_multiband_height_features,
    shared_group_fold_assignment,
)
from src.satellite_monitoring.experiments.sentinel2_temporal import (
    DELTA_FEATURES,
    EVENT_FEATURES,
    TEMPORAL_LOOKBACK_DAYS,
    TRAINING_TEMPORAL_CONTEXT,
    TREND_FEATURES,
    TemporalFeatureStages,
    TemporalObservation,
    build_temporal_feature_stages,
    parse_scene_date,
    strictly_historical_records,
)
from src.satellite_monitoring.models.grass_threshold import (
    HIGH_DECISION_THRESHOLD,
    LOW_DECISION_THRESHOLD,
)
from src.satellite_monitoring.models.validation import (
    build_training_pipeline,
    risk_coverage_table,
    selective_validation_metrics,
)

SCENARIOS = {"D50": 50.0}
EXPERIMENT_FEATURES = {
    "T0": tuple(C2_FEATURES),
    "T1": tuple(C2_FEATURES) + DELTA_FEATURES,
    "T2": tuple(C2_FEATURES) + DELTA_FEATURES + TREND_FEATURES,
    "T3": tuple(C2_FEATURES) + DELTA_FEATURES + TREND_FEATURES + EVENT_FEATURES,
}


def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "q1": None, "median": None, "q3": None, "max": None}
    array = np.asarray(values, dtype=float)
    return {
        "min": float(np.min(array)),
        "q1": float(np.quantile(array, 0.25)),
        "median": float(np.median(array)),
        "q3": float(np.quantile(array, 0.75)),
        "max": float(np.max(array)),
    }


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, HeightMaskRejectedError):
        return "height_mask"
    if isinstance(exc, MissingMultibandAssetError):
        return "invalid_feature"
    return "other"


def _read_cached_multiband(
    feature: Any,
    record: Mapping[str, Any],
    cache: dict[tuple[str, str], dict[str, Any] | None],
    cache_failures: dict[tuple[str, str], str],
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    item = record.get("_scene_item")
    if item is None:
        return None
    item_id = str(record.get("item_id") or getattr(item, "id", ""))
    key = (str(feature.feature_id), item_id)
    if key not in cache:
        try:
            cache[key] = read_multiband_height_features(
                item, mapping(feature.geometry)
            ).to_dict()
        except Exception as exc:
            cache[key] = None
            cache_failures[key] = _failure_category(exc)
            errors.append(
                {
                    "candidate_geometry_id": str(feature.feature_id),
                    "sentinel_item_id": item_id,
                    "stage": "multiband_20m",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return cache[key]


def _temporal_observation(
    record: Mapping[str, Any], payload: Mapping[str, Any]
) -> TemporalObservation:
    scene_date = parse_scene_date(record)
    if scene_date is None:
        raise ValueError("Scene date is unavailable for temporal observation.")
    return TemporalObservation(
        scene_date=scene_date,
        item_id=str(record.get("item_id") or ""),
        values={name: float(value) for name, value in payload["values"].items()},
    )


def _anchor_observation(
    feature_id: str,
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    scene_date = parse_scene_date(record)
    if scene_date is None:
        raise ValueError("Anchor scene date is unavailable.")
    return {
        **{name: float(value) for name, value in payload["values"].items()},
        "candidate_geometry_id": feature_id,
        "item_id": str(record.get("item_id") or ""),
        "anchor_scene_date": scene_date,
        "vegetation_fraction": float(payload["vegetation_fraction"]),
        "height_valid_pixel_count": int(payload["height_valid_pixel_count"]),
        "height_total_pixel_count": int(payload["height_total_pixel_count"]),
        "mixed_pixel_risk": str(payload["mixed_pixel_risk"]),
    }


def _collect_feature_histories(
    baseline_rows: Sequence[Mapping[str, Any]],
    dataset: Path,
    management_kmz: Path,
    km_markers_kmz: Path,
    *,
    frozen_candidate_geometry_ids: Mapping[str, Sequence[str]] | None = None,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    baseline_by_id = {str(row["sample_id"]): row for row in baseline_rows}
    management = load_management_features(management_kmz)
    if frozen_candidate_geometry_ids is None:
        dataset_by_id = {
            str(row["sample_id"]): row
            for row in load_calibration_rows(dataset)
            if str(row.get("sample_id") or "") in baseline_by_id
        }
        selected_rows = [dataset_by_id[str(row["sample_id"])] for row in baseline_rows]
        markers = load_km_markers(km_markers_kmz)
        candidate_sets = build_candidate_polygon_sets(
            selected_rows, management, markers, scenarios=SCENARIOS
        )
    else:
        management_by_id = {feature.feature_id: feature for feature in management}
        dataset_by_id = {
            str(row["sample_id"]): {
                "sample_id": str(row["sample_id"]),
                "source_snapshot_date": str(row["field_date"]),
            }
            for row in baseline_rows
        }
        selected_rows = [dataset_by_id[str(row["sample_id"])] for row in baseline_rows]
        candidate_sets = {}
        for row in baseline_rows:
            sample_id = str(row["sample_id"])
            feature_ids = tuple(frozen_candidate_geometry_ids.get(sample_id, ()))
            missing = sorted(set(feature_ids) - set(management_by_id))
            if missing:
                raise ValueError(
                    f"Frozen candidate geometries unavailable for {sample_id}: "
                    + ", ".join(missing)
                )
            candidate_sets[sample_id] = {
                "D50": [
                    CandidatePolygon(
                        feature=management_by_id[feature_id],
                        distance_m=0.0,
                        semantic_match_reason="frozen_historical_geometry_group",
                    )
                    for feature_id in feature_ids
                ]
            }
    feature_by_id = {
        candidate.feature.feature_id: candidate.feature
        for row in selected_rows
        for candidate in candidate_sets[str(row["sample_id"])]["D50"]
    }
    targets = sorted({resolve_filename_target_date(row)[0] for row in selected_rows})
    query_start = min(targets) - timedelta(days=TEMPORAL_LOOKBACK_DAYS + 5)
    query_end = max(targets) + timedelta(days=5)
    multiband_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    cache_failures: dict[tuple[str, str], str] = {}
    feature_target: dict[tuple[str, date], dict[str, Any] | None] = {}
    feature_target_failure: dict[tuple[str, date], str] = {}
    errors: list[dict[str, str]] = []
    rejection_keys: set[tuple[str, str, str]] = set()
    unique_anchor_items: set[str] = set()
    unique_historical_items: set[str] = set()

    for feature in sorted(feature_by_id.values(), key=lambda item: item.feature_id):
        records, query_error = query_training_scenes(
            feature.geometry, query_start, query_end
        )
        if query_error:
            errors.append(
                {
                    "candidate_geometry_id": str(feature.feature_id),
                    "stage": "query",
                    "error": query_error,
                }
            )
        for target in targets:
            key = (str(feature.feature_id), target)
            anchor_record = select_training_scene(records, target)
            if anchor_record is None:
                feature_target[key] = None
                feature_target_failure[key] = "scene_quality"
                continue
            anchor_payload = _read_cached_multiband(
                feature, anchor_record, multiband_cache, cache_failures, errors
            )
            anchor_item_id = str(anchor_record.get("item_id") or "")
            if anchor_payload is None:
                feature_target[key] = None
                feature_target_failure[key] = cache_failures.get(
                    (str(feature.feature_id), anchor_item_id), "other"
                )
                continue
            anchor = _temporal_observation(anchor_record, anchor_payload)
            unique_anchor_items.add(anchor.item_id)
            historical: list[TemporalObservation] = []
            for historical_record in strictly_historical_records(
                records, anchor.scene_date
            ):
                historical_item_id = str(historical_record.get("item_id") or "")
                historical_payload = _read_cached_multiband(
                    feature,
                    historical_record,
                    multiband_cache,
                    cache_failures,
                    errors,
                )
                if historical_payload is None:
                    reason = cache_failures.get(
                        (str(feature.feature_id), historical_item_id), "other"
                    )
                    rejection_keys.add(
                        (str(feature.feature_id), historical_item_id, reason)
                    )
                    continue
                observation = _temporal_observation(
                    historical_record, historical_payload
                )
                historical.append(observation)
                unique_historical_items.add(observation.item_id)
            for record in records:
                scene_date = parse_scene_date(record)
                if (
                    scene_date is not None
                    and 0 < (anchor.scene_date - scene_date).days
                    <= TEMPORAL_LOOKBACK_DAYS
                    and record.get("accepted_for_timeseries") is not True
                ):
                    rejection_keys.add(
                        (
                            str(feature.feature_id),
                            str(record.get("item_id") or ""),
                            "scene_quality",
                        )
                    )
            stages = build_temporal_feature_stages(anchor, historical)
            previous = (
                max(historical, key=lambda item: (item.scene_date, item.item_id))
                if historical
                else None
            )
            feature_target[key] = {
                "anchor": _anchor_observation(
                    str(feature.feature_id), anchor_record, anchor_payload
                ),
                "stages": stages,
                "previous_item_id": previous.item_id if previous else None,
                "historical_item_ids": sorted({item.item_id for item in historical}),
            }

    variants: dict[str, list[dict[str, Any]]] = {
        name: [] for name in EXPERIMENT_FEATURES
    }
    coverage_rows: list[dict[str, Any]] = []
    gap_rows: list[dict[str, Any]] = []
    strict_causal: list[dict[str, Any]] = []
    for baseline in baseline_rows:
        sample_id = str(baseline["sample_id"])
        source = dataset_by_id[sample_id]
        target, _ = resolve_filename_target_date(source)
        candidates: list[CandidatePolygon] = candidate_sets[sample_id]["D50"]
        t0_observations: list[dict[str, Any]] = []
        stage_observations: dict[str, list[dict[str, Any]]] = {
            "T1": [], "T2": [], "T3": []
        }
        reasons: dict[str, list[str]] = {name: [] for name in EXPERIMENT_FEATURES}
        anchor_dates: list[date] = []
        for candidate in candidates:
            key = (candidate.feature.feature_id, target)
            record = feature_target.get(key)
            if record is None:
                reason = feature_target_failure.get(key, "other")
                for experiment in EXPERIMENT_FEATURES:
                    reasons[experiment].append(reason)
                continue
            anchor = record["anchor"]
            stages: TemporalFeatureStages = record["stages"]
            t0_observations.append(anchor)
            anchor_dates.append(anchor["anchor_scene_date"])
            if stages.t1 is not None:
                stage_observations["T1"].append(
                    {
                        **stages.t1,
                        "candidate_geometry_id": candidate.feature.feature_id,
                        "previous_item_id": record["previous_item_id"],
                        "previous_scene_date": stages.previous_scene_date,
                        "temporal_gap_days": stages.temporal_gap_days,
                    }
                )
            else:
                reasons["T1"].append(stages.invalid_reason_t1 or "other")
            if stages.t2 is not None:
                stage_observations["T2"].append(
                    {
                        **{name: stages.t2[name] for name in TREND_FEATURES},
                        "candidate_geometry_id": candidate.feature.feature_id,
                        "observation_count_30d": stages.observation_count_30d,
                        "observation_count_60d": stages.observation_count_60d,
                    }
                )
            else:
                reasons["T2"].append(stages.invalid_reason_t2 or "other")
            if stages.t3 is not None:
                stage_observations["T3"].append(
                    {
                        **{name: stages.t3[name] for name in EVENT_FEATURES},
                        "candidate_geometry_id": candidate.feature.feature_id,
                    }
                )
            else:
                reasons["T3"].append(stages.invalid_reason_t3 or "other")

        t0 = _aggregate_observations(baseline, t0_observations, C2_FEATURES)
        rows: dict[str, dict[str, Any] | None] = {"T0": t0, "T1": None, "T2": None, "T3": None}
        if t0 is not None and stage_observations["T1"]:
            t1_features = {
                name: _median([float(item[name]) for item in stage_observations["T1"]])
                for name in DELTA_FEATURES
            }
            rows["T1"] = {
                **t0,
                **t1_features,
                "candidate_with_temporal_count": len(stage_observations["T1"]),
                "previous_scene_dates": sorted(
                    {
                        item["previous_scene_date"].isoformat()
                        for item in stage_observations["T1"]
                    }
                ),
                "previous_item_ids": sorted(
                    {str(item["previous_item_id"]) for item in stage_observations["T1"]}
                ),
                "temporal_gap_days": _median(
                    [float(item["temporal_gap_days"]) for item in stage_observations["T1"]]
                ),
            }
        if rows["T1"] is not None and stage_observations["T2"]:
            rows["T2"] = {
                **rows["T1"],
                **{
                    name: _median([float(item[name]) for item in stage_observations["T2"]])
                    for name in TREND_FEATURES
                },
                "candidate_with_trend_count": len(stage_observations["T2"]),
                "observation_count_30d": int(round(_median([
                    float(item["observation_count_30d"]) for item in stage_observations["T2"]
                ]))),
                "observation_count_60d": int(round(_median([
                    float(item["observation_count_60d"]) for item in stage_observations["T2"]
                ]))),
            }
        if rows["T2"] is not None and stage_observations["T3"]:
            rows["T3"] = {
                **rows["T2"],
                **{
                    name: _median([float(item[name]) for item in stage_observations["T3"]])
                    for name in EVENT_FEATURES
                },
                "candidate_with_event_count": len(stage_observations["T3"]),
            }
        for experiment, row in rows.items():
            if row is not None:
                variants[experiment].append(row)
            invalid_reasons = [] if row is not None else reasons[experiment]
            if row is None and experiment != "T0":
                prior = {"T1": "T0", "T2": "T1", "T3": "T2"}[experiment]
                if rows[prior] is None and not invalid_reasons:
                    invalid_reasons = ["other"]
            coverage_rows.append(
                {
                    "sample_id": sample_id,
                    "experiment": experiment,
                    "requested": True,
                    "valid": row is not None,
                    "target": int(baseline["target"]),
                    "group_id": str(baseline["group_id"]),
                    "invalid_reason": None
                    if row is not None
                    else ";".join(sorted(set(invalid_reasons or ["other"]))),
                }
            )
        if rows["T1"] is not None:
            gap_rows.append(
                {
                    "sample_id": sample_id,
                    "target": int(baseline["target"]),
                    "temporal_gap_days": rows["T1"]["temporal_gap_days"],
                    "previous_scene_dates": rows["T1"]["previous_scene_dates"],
                    "previous_item_ids": rows["T1"]["previous_item_ids"],
                }
            )
        if t0 is not None:
            strict_causal.append(
                {
                    "sample_id": sample_id,
                    "target": int(baseline["target"]),
                    "strict_field_causal": bool(anchor_dates)
                    and all(value <= target for value in anchor_dates),
                }
            )

    rejection_counts = Counter(reason for _, _, reason in rejection_keys)
    diagnostics = {
        "query_start": query_start.isoformat(),
        "query_end": query_end.isoformat(),
        "training_temporal_context": TRAINING_TEMPORAL_CONTEXT,
        "anchor_alignment": "nearest_valid_scene_within_5_days",
        "candidate_geometry_source": "frozen_historical_geometry_group"
        if frozen_candidate_geometry_ids is not None
        else "recomputed_spatial_matching",
        "anchor_scene_may_be_within_plus_or_minus_5_days_of_field_observation": True,
        "unique_candidate_polygons": len(feature_by_id),
        "unique_anchor_items": len(unique_anchor_items),
        "unique_anchor_item_ids": sorted(unique_anchor_items),
        "unique_historical_items": len(unique_historical_items),
        "unique_historical_item_ids": sorted(unique_historical_items),
        "total_unique_sentinel_items": len(unique_anchor_items | unique_historical_items),
        "multiband_cache_entries": len(multiband_cache),
        "historical_scene_rejection_counts": dict(sorted(rejection_counts.items())),
        "errors": errors,
        "strict_field_causal_sample_count": sum(
            item["strict_field_causal"] for item in strict_causal
        ),
        "strict_field_causal_positive_count": sum(
            item["strict_field_causal"] and item["target"] == 1
            for item in strict_causal
        ),
        "strict_field_causal_negative_count": sum(
            item["strict_field_causal"] and item["target"] == 0
            for item in strict_causal
        ),
        "temporal_gap_days": _quantiles(
            [float(row["temporal_gap_days"]) for row in gap_rows]
        ),
        "definitions": {
            "delta": "anchor_value_minus_immediately_previous_valid_value",
            "slope": "ordinary_least_squares_value_per_day_minimum_2_observations",
            "recent_drop": "latest_consecutive_absolute_index_drop_at_least_0.06_within_30_days",
            "no_recent_drop_days_marker": 61,
        },
    }
    return variants, coverage_rows, gap_rows, diagnostics


def _evaluate(
    experiment: str,
    cohort: str,
    rows: Sequence[Mapping[str, Any]],
    fold_assignment: Mapping[str, int],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    y, scores, folds = fixed_fold_oof_scores(
        rows, EXPERIMENT_FEATURES[experiment], fold_assignment
    )
    metrics = {
        "experiment": experiment,
        "cohort": cohort,
        "valid_samples": len(rows),
        "evaluated_samples": len(y),
        "positive_samples": sum(int(row["target"]) for row in rows),
        "negative_samples": len(rows) - sum(int(row["target"]) for row in rows),
        **classification_metrics(y, scores),
        "fold_stability": _stability(folds),
    }
    selective = {
        "experiment": experiment,
        "cohort": cohort,
        **selective_validation_metrics(
            y, scores, lower=LOW_DECISION_THRESHOLD, upper=HIGH_DECISION_THRESHOLD
        ),
    }
    risk = [
        {"experiment": experiment, "cohort": cohort, **row}
        for row in risk_coverage_table(y, scores)
    ]
    return metrics, [
        {"experiment": experiment, "cohort": cohort, **row} for row in folds
    ], selective, risk


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


def _plots(
    output_dir: Path,
    metrics: Sequence[Mapping[str, Any]],
    risks: Sequence[Mapping[str, Any]],
    coverage: Mapping[str, Mapping[str, Any]],
) -> None:
    natural = [row for row in metrics if row["cohort"] == "natural"]
    labels = [str(row["experiment"]) for row in natural]
    figure, axis = plt.subplots(figsize=(6, 4))
    axis.bar(labels, [float(row["pr_auc"]) for row in natural])
    axis.set_ylabel("PR-AUC OOF")
    axis.set_title("Experimento temporal Sentinel-2")
    figure.tight_layout()
    figure.savefig(output_dir / "temporal_pr_auc.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6, 4))
    for row in natural:
        axis.scatter(row["recall_gt_30"], row["precision_gt_30"], label=row["experiment"])
    axis.set_xlabel("Recall >30")
    axis.set_ylabel("Precision >30")
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "temporal_precision_recall.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(7, 4))
    for experiment in EXPERIMENT_FEATURES:
        selected = [
            row for row in risks
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
    figure.savefig(output_dir / "temporal_risk_coverage.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(6, 4))
    axis.bar(
        list(EXPERIMENT_FEATURES),
        [int(coverage[name]["valid_samples"]) for name in EXPERIMENT_FEATURES],
    )
    axis.set_ylabel("Valid samples")
    axis.set_title("Cobertura por estágio temporal")
    figure.tight_layout()
    figure.savefig(output_dir / "temporal_coverage.png", dpi=150)
    plt.close(figure)


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    baseline = _read_baseline_samples(arguments.baseline_samples)
    variants, coverage_rows, gap_rows, collection = _collect_feature_histories(
        baseline, arguments.dataset, arguments.management_kmz, arguments.km_markers_kmz
    )
    # Reproduz exatamente os folds naturais da ablação A/B/C1/C2: a atribuição
    # nasce da coorte baseline completa (186), e cada challenger usa o mesmo
    # group -> fold mesmo quando perde amostras por máscara ou histórico.
    natural_assignment, natural_folds = shared_group_fold_assignment(baseline)
    common_ids = common_sample_ids(*(variants[name] for name in EXPERIMENT_FEATURES))
    common_variants = {
        name: [row for row in rows if str(row["sample_id"]) in common_ids]
        for name, rows in variants.items()
    }
    common_assignment, common_folds = shared_group_fold_assignment(common_variants["T0"])
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
        common, common_folds_rows, common_selective, common_risks = _evaluate(
            experiment, "common_all", common_variants[experiment], common_assignment
        )
        common_metrics.append(common)
        fold_rows.extend(common_folds_rows)
        selective_rows.append(common_selective)
        risk_rows.extend(common_risks)
        coefficient_rows.extend(_coefficients(experiment, variants[experiment]))

    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    coverage = {
        name: {
            "requested_samples": len(baseline),
            "valid_samples": len(rows),
            "invalid_samples": len(baseline) - len(rows),
            "positive_samples": sum(int(row["target"]) for row in rows),
            "negative_samples": len(rows) - sum(int(row["target"]) for row in rows),
            "invalid_reason_counts": dict(sorted(Counter(
                reason
                for coverage_row in coverage_rows
                if coverage_row["experiment"] == name and not coverage_row["valid"]
                for reason in str(coverage_row["invalid_reason"] or "other").split(";")
            ).items())),
        }
        for name, rows in variants.items()
    }
    common_rows = [
        {
            "sample_id": str(row["sample_id"]),
            "group_id": str(row["group_id"]),
            "target": int(row["target"]),
            "height_class": int(row["height_class"]),
        }
        for row in common_variants["T0"]
    ]
    _write_csv(output_dir / "temporal_metrics.csv", metrics_rows)
    _write_csv(output_dir / "temporal_fold_metrics.csv", fold_rows)
    _write_csv(output_dir / "temporal_selective_metrics.csv", selective_rows)
    _write_csv(output_dir / "temporal_risk_coverage.csv", risk_rows)
    _write_csv(output_dir / "temporal_sample_coverage.csv", coverage_rows)
    _write_csv(output_dir / "temporal_common_cohort.csv", common_rows)
    _write_csv(output_dir / "temporal_gap_diagnostics.csv", gap_rows)
    _write_csv(
        output_dir / "temporal_feature_correlation.csv",
        _correlation_rows(variants["T3"], EXPERIMENT_FEATURES["T3"]),
    )
    _write_csv(output_dir / "temporal_model_coefficients.csv", coefficient_rows)
    _plots(output_dir, metrics_rows, risk_rows, coverage)
    summary = {
        "scope": "offline_controlled_sentinel2_temporal_experiment",
        "production_changes": False,
        "target": "height_class_3_gt_30_vs_classes_1_2_le_30",
        "algorithm": "StandardScaler_plus_balanced_LogisticRegression",
        "training_temporal_context": TRAINING_TEMPORAL_CONTEXT,
        "anchor_alignment": "nearest_valid_scene_within_5_days",
        "natural_fold_count": natural_folds,
        "common_fold_count": common_folds,
        "fold_group_assignments": {
            "natural": natural_assignment,
            "common_all": common_assignment,
        },
        "features": {name: list(values) for name, values in EXPERIMENT_FEATURES.items()},
        "coverage": coverage,
        "common_cohort": {
            "common_cohort_n": len(common_ids),
            "positive_n": sum(int(row["target"]) for row in common_variants["T0"]),
            "negative_n": len(common_ids) - sum(
                int(row["target"]) for row in common_variants["T0"]
            ),
            "sample_ids": common_ids,
        },
        "natural_metrics": metrics_rows,
        "common_cohort_metrics": common_metrics,
        "selective_metrics": selective_rows,
        "collection_diagnostics": collection,
        "unique_spatial_groups": len({str(row["group_id"]) for row in baseline}),
        "execution_time_seconds": time.perf_counter() - started,
    }
    (output_dir / "temporal_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled Sentinel-2 temporal experiment.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--management-kmz", required=True, type=Path)
    parser.add_argument("--km-markers-kmz", required=True, type=Path)
    parser.add_argument(
        "--baseline-samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs/height_temporal_experiment")
    )
    return parser.parse_args()


def main() -> int:
    summary = run(_arguments())
    print(
        json.dumps(
            {
                "output": "temporal_summary.json",
                "coverage": summary["coverage"],
                "common_cohort": summary["common_cohort"],
                "execution_time_seconds": summary["execution_time_seconds"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
