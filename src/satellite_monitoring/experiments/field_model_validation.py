"""Validação externa dos estimadores de altura em ground truth enriquecido."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..height_estimation import estimate_height_class
from ..models.grass_threshold import classify_height_score, height_score_confidence
from .field_external_validation import score_with_frozen_pipeline
from .sentinel2_temporal import DELTA_FEATURES, EVENT_FEATURES, TREND_FEATURES

EXTERNAL_HOLDOUT_IDS = (
    "CAMPO_20260821_VIDEO_01_HIGH",
    "CAMPO_20260821_VIDEO_01_LOW",
    "CAMPO_20260821_VIDEO_02",
    "GT35-01",
    "GT35-02",
)
COMPARISON_HOLDOUT_IDS = (
    "CAMPO_20260821_VIDEO_01_LOW",
    "CAMPO_20260821_VIDEO_02",
    "GT35-01",
    "GT35-02",
)
GT35_HOLDOUT_IDS = ("GT35-01", "GT35-02")
FROZEN_ARTIFACT_UNAVAILABLE = "FROZEN_ARTIFACT_UNAVAILABLE"
V0_FEATURES = ("red_reflectance", "nir_reflectance", "ndvi")
C2_FEATURES = V0_FEATURES + (
    "red_edge_1_reflectance",
    "red_edge_2_reflectance",
    "red_edge_3_reflectance",
    "narrow_nir_reflectance",
    "swir1_reflectance",
    "swir2_reflectance",
    "ndre",
    "ndii",
)
T3_FEATURES = C2_FEATURES + DELTA_FEATURES + TREND_FEATURES + EVENT_FEATURES

OUTPUT_COLUMNS = (
    "sample_id",
    "measurement_type",
    "measured_height_cm",
    "height_lower_bound_cm",
    "classification_ground_truth_strength",
    "metric_regression_eligible",
    "real_class",
    "boundary_case",
    "qualitative_condition",
    "recommendation_context",
    "model",
    "score_gt_30_cm",
    "estimated_class",
    "classification_result",
    "qualitative_score",
    "qualitative_consistency",
    "vegetation_fraction",
    "mixed_pixel_risk",
    "height_valid_pixel_count",
    "height_total_pixel_count",
    "temporal_status",
    "model_version",
    "warnings",
)

COMPARISON_COLUMNS = (
    "sample_id",
    "ground_truth",
    "measurement_type",
    "height_lower_bound_cm",
    "V0_score",
    "V0_class",
    "C2_score",
    "C2_class",
    "T3_score",
    "T3_class",
)


def _optional_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Holdout features must be finite.")
    return number


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes", "sim"}


def _warnings(value: Any) -> list[str]:
    if value is None or str(value).strip() == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]


def load_enriched_holdouts(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        source = [dict(row) for row in csv.DictReader(handle)]
    by_id = {str(row.get("sample_id") or ""): row for row in source}
    missing = sorted(set(EXTERNAL_HOLDOUT_IDS) - set(by_id))
    if missing:
        raise ValueError("Missing external holdouts: " + ", ".join(missing))
    rows: list[dict[str, Any]] = []
    for sample_id in EXTERNAL_HOLDOUT_IDS:
        row = dict(by_id[sample_id])
        if _boolean(row.get("training_eligible")):
            raise ValueError(f"External holdout cannot be training eligible: {sample_id}")
        if not _boolean(row.get("external_validation")):
            raise ValueError(f"External holdout flag is required: {sample_id}")
        for name in (
            *T3_FEATURES,
            "measured_height_cm",
            "height_lower_bound_cm",
            "vegetation_fraction",
        ):
            row[name] = _optional_float(row.get(name))
        for name in ("height_valid_pixel_count", "height_total_pixel_count"):
            value = _optional_float(row.get(name))
            row[name] = int(value) if value is not None else None
        row["boundary_case"] = _boolean(row.get("boundary_case"))
        row["metric_regression_eligible"] = _boolean(
            row.get("metric_regression_eligible")
        )
        row["warnings"] = _warnings(row.get("warnings"))
        row["real_class"] = str(row.get("real_class_30cm") or "unknown")
        rows.append(row)
    return rows


def assert_external_holdouts_excluded(
    training_rows: Sequence[Mapping[str, Any]],
) -> None:
    overlap = sorted(
        set(EXTERNAL_HOLDOUT_IDS)
        & {str(row.get("sample_id") or "") for row in training_rows}
    )
    if overlap:
        raise ValueError("External holdout leaked into fit: " + ", ".join(overlap))


def metric_classification_result(
    *,
    measured_height_cm: float | None,
    boundary_case: bool,
    estimated_class: str | None,
    real_class: str | None = None,
) -> str:
    normalized_real_class = {
        ">30": "gt_30_cm",
        "<=30": "le_30_cm",
    }.get(str(real_class or ""), str(real_class or ""))
    if normalized_real_class not in {"le_30_cm", "gt_30_cm"}:
        normalized_real_class = (
            "le_30_cm"
            if measured_height_cm is not None and measured_height_cm <= 30
            else "gt_30_cm"
            if measured_height_cm is not None
            else ""
        )
    if not normalized_real_class:
        return "not_metric_ground_truth"
    if estimated_class in {None, "unavailable"}:
        return "unavailable"
    if estimated_class == "inconclusive":
        return "acceptable_boundary_abstention" if boundary_case else "abstained"
    if estimated_class == normalized_real_class:
        return "correct"
    if normalized_real_class == "le_30_cm" and estimated_class == "gt_30_cm":
        return "boundary_false_positive" if boundary_case else "false_positive"
    return "false_negative"


def qualitative_consistency(
    qualitative_condition: str | None, estimated_class: str | None
) -> str | None:
    if qualitative_condition != "high_vegetation":
        return None
    if estimated_class == "gt_30_cm":
        return "consistent_with_high_vegetation"
    if estimated_class == "le_30_cm":
        return "inconsistent_with_high_vegetation"
    if estimated_class == "inconclusive":
        return "qualitatively_inconclusive"
    return "unavailable"


def _features(row: Mapping[str, Any], names: Sequence[str]) -> dict[str, float] | None:
    if any(row.get(name) is None for name in names):
        return None
    return {name: float(row[name]) for name in names}


def _base_result(row: Mapping[str, Any], model: str) -> dict[str, Any]:
    return {
        "sample_id": str(row["sample_id"]),
        "measurement_type": row.get("measurement_type") or None,
        "measured_height_cm": row.get("measured_height_cm"),
        "height_lower_bound_cm": row.get("height_lower_bound_cm"),
        "classification_ground_truth_strength": row.get(
            "classification_ground_truth_strength"
        )
        or None,
        "metric_regression_eligible": bool(
            row.get("metric_regression_eligible")
        ),
        "real_class": str(row.get("real_class") or "unknown"),
        "boundary_case": bool(row.get("boundary_case")),
        "qualitative_condition": row.get("qualitative_condition") or None,
        "recommendation_context": row.get("recommendation_context") or None,
        "model": model,
        "score_gt_30_cm": None,
        "estimated_class": None,
        "classification_result": "unavailable",
        "qualitative_score": None,
        "qualitative_consistency": None,
        "vegetation_fraction": row.get("vegetation_fraction"),
        "mixed_pixel_risk": row.get("mixed_pixel_risk") or None,
        "height_valid_pixel_count": row.get("height_valid_pixel_count"),
        "height_total_pixel_count": row.get("height_total_pixel_count"),
        "temporal_status": row.get("temporal_status") or None,
        "model_version": None,
        "warnings": list(row.get("warnings") or []),
    }


def _complete_result(result: dict[str, Any]) -> dict[str, Any]:
    score = result.get("score_gt_30_cm")
    if result.get("qualitative_condition") == "high_vegetation":
        result["classification_result"] = "not_metric_ground_truth"
        result["qualitative_score"] = score
        result["qualitative_consistency"] = qualitative_consistency(
            result.get("qualitative_condition"), result.get("estimated_class")
        )
    else:
        result["classification_result"] = metric_classification_result(
            measured_height_cm=result.get("measured_height_cm"),
            boundary_case=bool(result.get("boundary_case")),
            estimated_class=result.get("estimated_class"),
            real_class=result.get("real_class"),
        )
    return result


def _v0_result(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _base_result(row, "V0")
    values = _features(row, V0_FEATURES)
    if values is None:
        result["warnings"].append("missing V0 features")
        return _complete_result(result)
    inferred = estimate_height_class(
        {
            **values,
            "vegetation_fraction": row.get("vegetation_fraction"),
            "mixed_pixel_risk": row.get("mixed_pixel_risk"),
            "height_valid_pixel_count": row.get("height_valid_pixel_count"),
            "height_total_pixel_count": row.get("height_total_pixel_count"),
            "height_purity_gate_passed": row.get("spectral_extraction_status")
            == "available",
        }
    )
    result.update(
        {
            "score_gt_30_cm": inferred.get("score_gt_30_cm"),
            "estimated_class": inferred.get("estimated_class"),
            "model_version": inferred.get("model_version"),
        }
    )
    return _complete_result(result)


def _challenger_result(
    row: Mapping[str, Any], *, model_name: str, model: Any | None
) -> dict[str, Any]:
    result = _base_result(row, model_name)
    names = C2_FEATURES if model_name == "C2" else T3_FEATURES
    if model is None:
        result["warnings"].append(f"{model_name} historical-only fit unavailable")
        return _complete_result(result)
    if model_name == "T3" and row.get("temporal_status") != "available":
        result["warnings"].append("T3 temporal features unavailable")
        return _complete_result(result)
    values = _features(row, names)
    if values is None:
        result["warnings"].append(f"missing {model_name} features")
        return _complete_result(result)
    inferred = score_with_frozen_pipeline(model, names, values)
    result.update(
        {
            "score_gt_30_cm": inferred["score_gt_30_cm"],
            "estimated_class": inferred["estimated_class"],
            "model_version": f"{model_name}-reproduced-historical-only",
        }
    )
    return _complete_result(result)


def evaluate_external_holdouts(
    rows: Sequence[Mapping[str, Any]], *, c2_model: Any | None, t3_model: Any | None
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item["sample_id"])):
        results.extend(
            (
                _v0_result(row),
                _challenger_result(row, model_name="C2", model=c2_model),
                _challenger_result(row, model_name="T3", model=t3_model),
            )
        )
    return results


def external_verdict(results: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    challengers = [row for row in results if row.get("model") in {"C2", "T3"}]
    metric = [row for row in challengers if row.get("measured_height_cm") is not None]
    qualitative = [
        row for row in challengers if row.get("qualitative_condition") == "high_vegetation"
    ]
    robust_false_positive = any(
        all(
            row.get("classification_result")
            in {"false_positive", "boundary_false_positive"}
            for row in metric
            if row.get("sample_id") == sample_id
        )
        for sample_id in {
            str(row.get("sample_id"))
            for row in metric
            if row.get("classification_result")
            in {"false_positive", "boundary_false_positive"}
        }
    )
    promising_model = any(
        all(
            row.get("classification_result") == "correct"
            for row in metric
            if row.get("model") == model
        )
        and any(
            row.get("qualitative_consistency") == "consistent_with_high_vegetation"
            for row in qualitative
            if row.get("model") == model
        )
        for model in ("C2", "T3")
    )
    if robust_false_positive:
        return "FIELD_SIGNAL_CONTRADICTORY", "COLLECT_GT30_FIELD_SAMPLES"
    if promising_model:
        return "C2_T3_FIELD_SIGNAL_PROMISING", "COLLECT_GT30_FIELD_SAMPLES"
    return "FIELD_VALIDATION_INCONCLUSIVE", "COLLECT_MORE_METRIC_SAMPLES"


def comparison_table(
    results: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Monta a comparação pequena sem calcular métricas agregadas."""
    by_sample_model = {
        (str(row.get("sample_id") or ""), str(row.get("model") or "")): row
        for row in results
    }
    labels = {
        "CAMPO_20260821_VIDEO_01_LOW": "14 cm",
        "CAMPO_20260821_VIDEO_02": "30 cm",
        "GT35-01": "GT35-01",
        "GT35-02": "GT35-02",
    }
    rows: list[dict[str, Any]] = []
    for sample_id in COMPARISON_HOLDOUT_IDS:
        available = [
            by_sample_model[(sample_id, model)]
            for model in ("V0", "C2", "T3")
            if (sample_id, model) in by_sample_model
        ]
        source = available[0] if available else {}
        real_class = str(source.get("real_class") or "unknown")
        ground_truth = {"le_30_cm": "<=30", "gt_30_cm": ">30"}.get(
            real_class, real_class
        )
        comparison = {
            "sample_id": labels[sample_id],
            "ground_truth": ground_truth,
            "measurement_type": source.get("measurement_type"),
            "height_lower_bound_cm": source.get("height_lower_bound_cm"),
        }
        for model in ("V0", "C2", "T3"):
            result = by_sample_model.get((sample_id, model), {})
            comparison[f"{model}_score"] = result.get("score_gt_30_cm")
            comparison[f"{model}_class"] = result.get("estimated_class")
        rows.append(comparison)
    ordering: dict[str, list[str]] = {}
    for model in ("V0", "C2", "T3"):
        scored = [
            row
            for row in rows
            if row.get(f"{model}_score") is not None
        ]
        ordering[model] = [
            str(row["sample_id"])
            for row in sorted(
                scored,
                key=lambda row: (-float(row[f"{model}_score"]), str(row["sample_id"])),
            )
        ]
    return rows, ordering


def evaluate_gt35_v0(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Avalia somente V0 congelado nos GT35; C2/T3 nunca são reconstruídos."""
    by_id = {str(row.get("sample_id") or ""): row for row in rows}
    missing = sorted(set(GT35_HOLDOUT_IDS) - set(by_id))
    if missing:
        raise ValueError("Missing GT35 holdouts: " + ", ".join(missing))
    results: list[dict[str, Any]] = []
    for sample_id in GT35_HOLDOUT_IDS:
        row = by_id[sample_id]
        observed_at = datetime.fromisoformat(
            str(row.get("observed_at") or "").replace("Z", "+00:00")
        )
        scene_datetime = datetime.fromisoformat(
            str(row.get("scene_datetime") or "").replace("Z", "+00:00")
        )
        if (
            observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or scene_datetime.tzinfo is None
            or scene_datetime.utcoffset() is None
        ):
            raise ValueError("GT35 observed_at and scene_datetime must be timezone-aware.")
        if scene_datetime.astimezone(timezone.utc) > observed_at.astimezone(timezone.utc):
            raise ValueError(f"Future Sentinel scene selected for {sample_id}.")
        spectral_status = str(row.get("spectral_extraction_status") or "")
        features: dict[str, Any] = {
            "vegetation_fraction": float(row["vegetation_fraction"]),
            "height_valid_pixel_count": int(float(row["height_valid_pixel_count"])),
            "height_total_pixel_count": int(float(row["height_total_pixel_count"])),
            "mixed_pixel_risk": str(row["mixed_pixel_risk"]),
            "height_purity_gate_passed": spectral_status == "available",
        }
        if spectral_status == "available":
            features.update(
                {
                    name: float(row[name])
                    for name in V0_FEATURES
                }
            )
        inferred = estimate_height_class(features)
        estimated_class = inferred.get("estimated_class")
        evaluation = (
            "CORRECT"
            if estimated_class == "gt_30_cm"
            else "FALSE_NEGATIVE"
            if estimated_class == "le_30_cm"
            else "ABSTAINED"
        )
        v0_status = (
            "SCORED"
            if estimated_class in {"gt_30_cm", "le_30_cm"}
            else "ABSTAINED_HEIGHT_MASK"
            if spectral_status != "available"
            else "ABSTAINED_DECISION_GATE"
        )
        results.append(
            {
                "sample_id": sample_id,
                "observed_at": observed_at.isoformat(),
                "ground_truth": ">30",
                "measurement_type": str(row.get("measurement_type") or ""),
                "height_lower_bound_cm": float(row["height_lower_bound_cm"]),
                "measured_height_cm": None,
                "metric_regression_eligible": False,
                "training_eligible": False,
                "external_validation": True,
                "geometry_status": "VALID_REAL_AOI",
                "sentinel_item_id": str(row.get("sentinel_item_id") or ""),
                "scene_date": str(row.get("scene_date") or ""),
                "scene_datetime": scene_datetime.isoformat(),
                "scene_age_days": int(float(row["scene_age_days"])),
                "scene_quality_score": float(row["scene_quality_score"]),
                "valid_pixel_percentage": float(row["valid_pixel_percentage"]),
                "aoi_coverage_percentage": float(row["aoi_coverage_percentage"]),
                "cloud_cover": float(row["cloud_cover"]),
                "red_reflectance": float(row["red_reflectance"])
                if str(row.get("red_reflectance") or "").strip()
                else None,
                "nir_reflectance": float(row["nir_reflectance"])
                if str(row.get("nir_reflectance") or "").strip()
                else None,
                "ndvi": float(row["ndvi"])
                if str(row.get("ndvi") or "").strip()
                else None,
                "height_valid_pixel_count": features["height_valid_pixel_count"],
                "height_total_pixel_count": features["height_total_pixel_count"],
                "vegetation_fraction": features["vegetation_fraction"],
                "mixed_pixel_risk": features["mixed_pixel_risk"],
                "V0_score": inferred.get("score_gt_30_cm"),
                "V0_class": estimated_class,
                "V0_confidence": inferred.get("confidence"),
                "V0_status": v0_status,
                "V0_evaluation": evaluation,
                "C2_status": FROZEN_ARTIFACT_UNAVAILABLE,
                "T3_status": FROZEN_ARTIFACT_UNAVAILABLE,
                "warnings": _warnings(row.get("warnings")),
            }
        )
    evaluations = [str(row["V0_evaluation"]) for row in results]
    conclusion = (
        "GT35_V0_BOTH_CORRECT"
        if evaluations == ["CORRECT", "CORRECT"]
        else "GT35_V0_BOTH_FALSE_NEGATIVE"
        if evaluations == ["FALSE_NEGATIVE", "FALSE_NEGATIVE"]
        else "GT35_V0_ABSTAINED"
        if "ABSTAINED" in evaluations
        else "GT35_V0_PARTIALLY_CORRECT"
    )
    scored = sorted(
        (row for row in results if row["V0_score"] is not None),
        key=lambda row: (-float(row["V0_score"]), str(row["sample_id"])),
    )
    return {
        "conclusion": conclusion,
        "scene_selection_rule": "scene_datetime <= field_observed_at",
        "models_retrained": False,
        "aggregate_metrics_calculated": False,
        "roc_auc_calculated": False,
        "pr_auc_calculated": False,
        "results": results,
        "V0_score_ordering_descending": [row["sample_id"] for row in scored],
        "V0_score_unranked": [
            row["sample_id"] for row in results if row["V0_score"] is None
        ],
        "blocked_holdouts": [
            {
                "sample_id": "CAMPO_20260821_VIDEO_01_LOW",
                "ground_truth": "<=30",
                "height_cm": 14,
                "status": "WAITING_FOR_FIELD_AOI_GEOJSON",
            },
            {
                "sample_id": "CAMPO_20260821_VIDEO_02",
                "ground_truth": "<=30",
                "height_cm": 30,
                "boundary_case": True,
                "status": "WAITING_FOR_FIELD_AOI_GEOJSON",
            },
        ],
        "readiness": {
            "clear_positive_samples": 2,
            "strong_classification_ground_truth_samples": 2,
            "real_aoi_samples": 2,
            "timezone_aware_timestamp_samples": 2,
            "new_metric_regression_eligible_samples": 0,
        },
        "unchanged": {
            "recommendation": True,
            "frontend": True,
            "V0_artifact": True,
            "C2": True,
            "T3": True,
            "training_data": True,
        },
    }


def write_gt35_v0_validation(
    rows: Sequence[Mapping[str, Any]], output_path: str | Path
) -> dict[str, Any]:
    payload = evaluate_gt35_v0(rows)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def write_external_validation_outputs(
    results: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    *,
    reproduction: Mapping[str, Any],
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        results,
        key=lambda row: (str(row.get("sample_id") or ""), str(row.get("model") or "")),
    )
    csv_path = destination / "field_model_validation.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for source in ordered:
            row = dict(source)
            row["warnings"] = json.dumps(
                row.get("warnings") or [],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            writer.writerow({name: row.get(name) for name in OUTPUT_COLUMNS})
    comparison, score_ordering = comparison_table(ordered)
    comparison_path = destination / "field_model_validation_comparison.csv"
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_COLUMNS)
        writer.writeheader()
        writer.writerows(comparison)
    verdict, next_action = external_verdict(ordered)
    unique_results = {
        str(row.get("sample_id") or ""): row for row in ordered
    }
    summary = {
        "external_holdout": True,
        "holdout_ids": list(EXTERNAL_HOLDOUT_IDS),
        "metric_ground_truth_sample_count": sum(
            row.get("measured_height_cm") is not None
            for row in unique_results.values()
        ),
        "strong_classification_ground_truth_sample_count": sum(
            row.get("classification_ground_truth_strength") == "strong"
            for row in unique_results.values()
        ),
        "qualitative_only_sample_count": 1,
        "aggregate_metrics_calculated": False,
        "roc_auc_calculated": False,
        "pr_auc_calculated": False,
        "gate": {"lower": 0.35, "upper": 0.65},
        "reproduction": dict(reproduction),
        "results": ordered,
        "comparison": comparison,
        "score_ordering_descending": score_ordering,
        "verdict": verdict,
        "recommended_next_action": next_action,
        "production_changes": False,
    }
    (destination / "field_model_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
