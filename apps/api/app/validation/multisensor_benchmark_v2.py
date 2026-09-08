"""Versioned, offline-only Sentinel-2/Sentinel-1 validation benchmark.

This module is deliberately a pure analytical consumer.  It does not import the
operational multisource service and it never emits or changes a recommendation.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .fusion_benchmark import COMBINATIONS, RULES, TEMPORAL_STATUSES


SCHEMA_VERSION = "2.0"
BOOTSTRAP_SEED = 20260907
DEFAULT_BOOTSTRAP_ITERATIONS = 10_000
KNOWN_S2_ERROR_IDS = (
    "5ae7cfe8-4d30-4d56-aef5-6721dfdc8819",
    "8f5bbe5a-9ce2-46f5-be03-7948f1445bbc",
)
BINARY_TRUTHS = {"cut", "no_cut"}
BINARY_DECISIONS = {"cortar", "nao_cortar"}
REVIEW_CANDIDATES = ("A", "B", "C", "A+B", "A+B+C")


class BenchmarkIntegrityError(ValueError):
    """Raised for an ambiguous input that cannot be joined safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def input_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _unique_index(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        identifier = str(row.get(key) or "")
        if not identifier:
            raise BenchmarkIntegrityError(f"{label}_missing_{key}")
        if identifier in result:
            raise BenchmarkIntegrityError(f"{label}_duplicate_{key}:{identifier}")
        result[identifier] = row
    return result


def _s2_correct(truth: Any, decision: Any) -> bool | None:
    if truth not in BINARY_TRUTHS or decision not in BINARY_DECISIONS:
        return None
    return decision == ("cortar" if truth == "cut" else "nao_cortar")


def _proportion(numerator: int, denominator: int) -> dict[str, Any]:
    value = numerator / denominator if denominator else None
    interval = _wilson(numerator, denominator)
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "wilson_95": interval,
    }


def _wilson(successes: int, total: int) -> dict[str, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return {"lower": max(0.0, center - half), "upper": min(1.0, center + half)}


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap_balanced_accuracy(
    pairs: Sequence[tuple[bool, bool]], *, iterations: int, seed: int
) -> dict[str, Any] | None:
    errors = [pair for pair in pairs if pair[0] is False]
    correct = [pair for pair in pairs if pair[0] is True]
    if not errors or not correct or iterations <= 0:
        return None
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(iterations):
        sampled_errors = [rng.choice(errors) for _ in errors]
        sampled_correct = [rng.choice(correct) for _ in correct]
        recall = sum(triggered for _, triggered in sampled_errors) / len(sampled_errors)
        specificity = sum(not triggered for _, triggered in sampled_correct) / len(sampled_correct)
        estimates.append((recall + specificity) / 2)
    return {
        "lower": _percentile(estimates, 0.025),
        "upper": _percentile(estimates, 0.975),
        "iterations": iterations,
        "seed": seed,
        "stratification": "s2_error/correct",
    }


def _rule_names(candidate: str) -> tuple[str, ...]:
    return ("C",) if candidate == "C" else COMBINATIONS[candidate]


def _triggered(row: Mapping[str, Any], candidate: str) -> bool:
    return any(RULES[name]["predicate"](row) for name in _rule_names(candidate))


def _confusion(rows: Sequence[Mapping[str, Any]], candidate: str) -> dict[str, int]:
    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for row in rows:
        triggered = _triggered(row, candidate)
        if row["s2_was_correct"] is False:
            counts["tp" if triggered else "fn"] += 1
        else:
            counts["fp" if triggered else "tn"] += 1
    return counts


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    candidate: str,
    *,
    bootstrap_iterations: int,
) -> dict[str, Any]:
    c = _confusion(rows, candidate)
    total = sum(c.values())
    recall = _proportion(c["tp"], c["tp"] + c["fn"])
    specificity = _proportion(c["tn"], c["tn"] + c["fp"])
    balanced = (
        (recall["value"] + specificity["value"]) / 2
        if recall["value"] is not None and specificity["value"] is not None
        else None
    )
    pairs = [(bool(row["s2_was_correct"]), _triggered(row, candidate)) for row in rows]
    return {
        "eligible_samples": total,
        "triggered_samples": c["tp"] + c["fp"],
        "confusion": c,
        "error_capture_rate": recall,
        "false_review_rate": _proportion(c["fp"], c["fp"] + c["tn"]),
        "review_precision": _proportion(c["tp"], c["tp"] + c["fp"]),
        "trigger_rate": _proportion(c["tp"] + c["fp"], total),
        "specificity": specificity,
        "balanced_accuracy": {
            "value": balanced,
            "bootstrap_95": _bootstrap_balanced_accuracy(
                pairs, iterations=bootstrap_iterations, seed=BOOTSTRAP_SEED
            ),
        },
    }


def _shadow_pass(metrics: Mapping[str, Any], technical_pass: bool) -> bool:
    def value(name: str) -> float | None:
        return _mapping(metrics.get(name)).get("value")

    c = _mapping(metrics.get("confusion"))
    return bool(
        technical_pass
        and c.get("tp", 0) >= 1
        and value("error_capture_rate") is not None
        and value("error_capture_rate") >= 0.50
        and value("false_review_rate") is not None
        and value("false_review_rate") <= 0.20
        and value("review_precision") is not None
        and value("review_precision") >= 0.33
        and value("trigger_rate") is not None
        and value("trigger_rate") <= 0.25
    )


def _loocv(
    rows: Sequence[Mapping[str, Any]], candidate: str, *, technical_pass: bool
) -> dict[str, Any]:
    tracked = ("error_capture_rate", "false_review_rate", "review_precision", "trigger_rate", "specificity", "balanced_accuracy")
    values: dict[str, list[float]] = defaultdict(list)
    gates: list[bool] = []
    for omitted in rows:
        subset = [row for row in rows if row["sample_id"] != omitted["sample_id"]]
        result = _metrics(subset, candidate, bootstrap_iterations=0)
        gates.append(_shadow_pass(result, technical_pass))
        for name in tracked:
            value = _mapping(result[name]).get("value")
            if value is not None:
                values[name].append(value)
    return {
        "folds": len(rows),
        "metric_ranges": {
            name: ({"min": min(items), "max": max(items)} if items else None)
            for name, items in values.items()
        },
        "shadow_gate_invariant": len(set(gates)) <= 1 if gates else None,
        "shadow_gate_passes_by_fold": sum(gates),
    }


def _agreement(values: Sequence[Any]) -> dict[str, Any]:
    encoded = [json.dumps(value, sort_keys=True, default=str) for value in values]
    counts = Counter(encoded)
    unanimous = len(counts) == 1 and bool(values)
    modal_encoded = counts.most_common(1)[0][0] if counts else None
    return {
        "unanimous": unanimous,
        "value": values[0] if unanimous else None,
        "modal_value": json.loads(modal_encoded) if modal_encoded is not None else None,
        "agreement_rate": counts[modal_encoded] / len(values) if values else 0.0,
        "distribution": [
            {"value": json.loads(item), "count": count}
            for item, count in sorted(counts.items())
        ],
    }


def _aggregate_soak(runs: Sequence[Mapping[str, Any]], expected: int) -> dict[str, Any]:
    fields = (
        "availability",
        "status",
        "canonical_relative_orbit",
        "temporal_status",
        "canonical_observation_count",
        "scenes_accepted",
    )
    agreements: dict[str, Any] = {}
    for field in fields:
        agreements[field] = _agreement([row.get(field) for row in runs])
    durations = [value for row in runs if (value := _finite(row.get("processing_duration_ms"))) is not None]
    periods = [row.get("analysis_period") for row in runs]
    repetitions = sorted({row.get("repetition") for row in runs if row.get("repetition") is not None})
    exact = bool(len(runs) == expected and all(item["unanimous"] for item in agreements.values()))
    return {
        "scientific_weight": 0,
        "expected_runs": expected,
        "observed_runs": len(runs),
        "repetitions": repetitions,
        "complete": len(runs) == expected,
        "exact_agreement": exact,
        "agreement_rate": sum(item["unanimous"] for item in agreements.values()) / len(fields) if runs else 0.0,
        "agreements": agreements,
        "latency_ms": {
            "min": min(durations) if durations else None,
            "median": statistics.median(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
        "analysis_periods": periods,
        "period_comparable_with_scientific_temporal": False,
        "failure_counts": dict(sum((Counter(_mapping(row.get("failure_counts"))) for row in runs), Counter())),
        "warnings": sorted({str(warning) for row in runs for warning in (row.get("warnings") or [])}),
    }


def _extract_s2(row: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_mapping(row.get("snapshot")).get("sentinel2"))
    return {
        "decision": row.get("s2_decision", source.get("recommendation_decision")),
        "confidence": row.get("s2_confidence", source.get("recommendation_confidence")),
        "observation_count": source.get("observation_count"),
        "quality_score": source.get("analysis_quality_score"),
        "quality_status": source.get("analysis_quality_status"),
        "ndvi_mean": row.get("s2_ndvi_mean", source.get("current_ndvi_mean")),
        "ndvi_median": row.get("s2_ndvi_median", source.get("current_ndvi_median")),
        "current_percentile": row.get("s2_current_percentile", source.get("current_percentile")),
        "historical_median": source.get("historical_median"),
        "historical_mean": source.get("historical_mean"),
        "historical_standard_deviation": source.get("historical_standard_deviation"),
        "recent_trend": source.get("recent_trend"),
        "recent_trend_status": source.get("recent_trend_status"),
        "last_absolute_change": source.get("last_absolute_change"),
        "last_relative_change_percentage": source.get("last_relative_change_percentage"),
        "significant_drop_detected": source.get("significant_drop_detected"),
        "significant_drop_confirmed": source.get("significant_drop_confirmed"),
        "max_observation_gap_days": source.get("max_observation_gap_days"),
    }


def _extract_static_s1(row: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_mapping(row.get("snapshot")).get("sentinel1"))
    canonical = _mapping(source.get("canonical_metrics"))
    return {
        "availability": row.get("s1_status", source.get("source_status")),
        "quality": row.get("s1_quality", source.get("quality")),
        "coverage": row.get("s1_coverage", source.get("coverage")),
        "observation_count": source.get("observation_count"),
        "calibrated_observation_count": source.get("calibrated_observation_count"),
        "relative_orbits": source.get("relative_orbits"),
        "canonical_relative_orbit": row.get("s1_canonical_relative_orbit", source.get("canonical_relative_orbit")),
        "canonical_observation_count": row.get("s1_canonical_observation_count", source.get("canonical_observation_count")),
        "calibration_status": source.get("radiometric_calibration_status"),
        "vv_sigma0_db": row.get("s1_vv_sigma0_db", canonical.get("vv_sigma0_median_db")),
        "vh_sigma0_db": row.get("s1_vh_sigma0_db", canonical.get("vh_sigma0_median_db")),
        "vh_minus_vv_db": row.get("s1_vh_minus_vv_db", canonical.get("vh_minus_vv_db_median")),
        "vh_vv_sigma0_ratio": row.get("s1_vh_vv_sigma0_ratio", canonical.get("vh_vv_sigma0_ratio_median")),
        "warnings": source.get("warnings") or [],
    }


def _extract_temporal(row: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(row or {})
    available = (
        source.get("processing_status") == "completed"
        and source.get("combined_status") in TEMPORAL_STATUSES
    )
    return {
        "available": available,
        "processing_status": source.get("processing_status", "unavailable"),
        "error": source.get("error"),
        "analysis_period": source.get("analysis_period"),
        "combined_status": source.get("combined_status", "insufficient_data"),
        "vv_status": source.get("vv_temporal_status", "insufficient_data"),
        "vh_status": source.get("vh_temporal_status", "insufficient_data"),
        "vv_slope_db_per_day": source.get("vv_slope"),
        "vh_slope_db_per_day": source.get("vh_slope"),
        "vv_modeled_change_db": source.get("vv_modeled_change_db"),
        "vh_modeled_change_db": source.get("vh_modeled_change_db"),
        "span_days": source.get("span_days"),
        "canonical_relative_orbit": source.get("canonical_relative_orbit"),
        "canonical_observation_count": source.get("canonical_observation_count"),
        "max_gap_days": source.get("max_gap_days"),
        "support_issues": source.get("support_issues") or [],
        "warnings": source.get("warnings") or [],
    }


def _partition(truth: Any, decision: Any) -> str:
    if truth == "uncertain":
        return "uncertain_ground_truth"
    if truth in BINARY_TRUTHS and decision == "inconclusivo":
        return "s2_inconclusive"
    if truth in BINARY_TRUTHS and decision in BINARY_DECISIONS:
        return "binary_evaluation"
    return "non_comparable"


def _s2_baseline(rows: Sequence[Mapping[str, Any]], *, bootstrap_iterations: int) -> dict[str, Any]:
    binary = [row for row in rows if row["evaluation_partition"] == "binary_evaluation"]
    tp = sum(row["maintenance_truth"] == "cut" and row["s2"]["decision"] == "cortar" for row in binary)
    fn = sum(row["maintenance_truth"] == "cut" and row["s2"]["decision"] == "nao_cortar" for row in binary)
    fp = sum(row["maintenance_truth"] == "no_cut" and row["s2"]["decision"] == "cortar" for row in binary)
    tn = sum(row["maintenance_truth"] == "no_cut" and row["s2"]["decision"] == "nao_cortar" for row in binary)
    recall, specificity = _proportion(tp, tp + fn), _proportion(tn, tn + fp)
    balanced = (recall["value"] + specificity["value"]) / 2 if recall["value"] is not None and specificity["value"] is not None else None
    # Baseline bootstrap is stratified by truth and deterministic.
    cut = [(True, row["s2"]["decision"] == "cortar") for row in binary if row["maintenance_truth"] == "cut"]
    no_cut = [(True, row["s2"]["decision"] == "nao_cortar") for row in binary if row["maintenance_truth"] == "no_cut"]
    bootstrap = None
    if cut and no_cut and bootstrap_iterations > 0:
        rng, values = random.Random(BOOTSTRAP_SEED), []
        for _ in range(bootstrap_iterations):
            sensitivity = sum(rng.choice(cut)[1] for _ in cut) / len(cut)
            spec = sum(rng.choice(no_cut)[1] for _ in no_cut) / len(no_cut)
            values.append((sensitivity + spec) / 2)
        bootstrap = {"lower": _percentile(values, .025), "upper": _percentile(values, .975), "iterations": bootstrap_iterations, "seed": BOOTSTRAP_SEED, "stratification": "maintenance_truth:cut/no_cut"}
    return {
        "positive_class": "cut",
        "eligible_samples": len(binary),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "accuracy": _proportion(tp + tn, len(binary)),
        "precision_cut": _proportion(tp, tp + fp),
        "recall_cut": recall,
        "specificity": specificity,
        "balanced_accuracy": {"value": balanced, "bootstrap_95": bootstrap},
    }


def _continuous_false_review_comparison(rows: Sequence[Mapping[str, Any]], candidate: str) -> dict[str, Any]:
    fields = {
        "s2_ndvi_mean": lambda r: r["s2"].get("ndvi_mean"),
        "s2_current_percentile": lambda r: r["s2"].get("current_percentile"),
        "s1_vv_sigma0_db": lambda r: r["s1_static"].get("vv_sigma0_db"),
        "s1_vh_sigma0_db": lambda r: r["s1_static"].get("vh_sigma0_db"),
        "vv_slope_db_per_day": lambda r: r["s1_temporal"].get("vv_slope_db_per_day"),
        "vh_slope_db_per_day": lambda r: r["s1_temporal"].get("vh_slope_db_per_day"),
    }
    false_rows = [r for r in rows if r["s2_was_correct"] is True and _triggered(r, candidate)]
    other_rows = [r for r in rows if r["s2_was_correct"] is True and not _triggered(r, candidate)]
    result: dict[str, Any] = {}
    for name, getter in fields.items():
        left = [v for r in false_rows if (v := _finite(getter(r))) is not None]
        right = [v for r in other_rows if (v := _finite(getter(r))) is not None]
        result[name] = (
            {
                "false_reviews": {"n": len(left), "median": statistics.median(left), "iqr": [_percentile(left, .25), _percentile(left, .75)]},
                "non_triggered_correct": {"n": len(right), "median": statistics.median(right), "iqr": [_percentile(right, .25), _percentile(right, .75)]},
            }
            if len(left) >= 3 and len(right) >= 3 else None
        )
    return result


def _false_reviews(rows: Sequence[Mapping[str, Any]], candidate: str) -> dict[str, Any]:
    selected = [row for row in rows if row["s2_was_correct"] is True and _triggered(row, candidate)]
    dimensions = {
        "maintenance_truth": lambda r: r.get("maintenance_truth"),
        "vegetation_class": lambda r: r.get("vegetation_class"),
        "s2_confidence": lambda r: r["s2"].get("confidence"),
        "canonical_relative_orbit": lambda r: r["s1_temporal"].get("canonical_relative_orbit"),
        "temporal_status": lambda r: r.get("s1_combined_status"),
    }
    return {
        "sample_ids": [row["sample_id"] for row in selected],
        "count": len(selected),
        "by_predefined_dimension": {
            name: dict(sorted(Counter(str(getter(row)) for row in selected).items()))
            for name, getter in dimensions.items()
        },
        "continuous_comparison": _continuous_false_review_comparison(rows, candidate),
        "new_rules_generated": False,
    }


def _gate(
    metrics: Mapping[str, Any],
    loocv: Mapping[str, Any],
    *,
    technical_pass: bool,
    holdout: Mapping[str, Any],
) -> dict[str, Any]:
    shadow = _shadow_pass(metrics, technical_pass)
    c = _mapping(metrics.get("confusion"))
    error_ci = _mapping(_mapping(metrics.get("error_capture_rate")).get("wilson_95"))
    false_ci = _mapping(_mapping(metrics.get("false_review_rate")).get("wilson_95"))
    precision_ci = _mapping(_mapping(metrics.get("review_precision")).get("wilson_95"))
    trigger = _mapping(metrics.get("trigger_rate")).get("value")
    review_requirements = {
        "independent_holdout": bool(holdout.get("independent")),
        "preregistered_holdout": bool(holdout.get("preregistered")),
        "at_least_50_binary_truths": metrics.get("eligible_samples", 0) >= 50,
        "at_least_10_s2_errors": c.get("tp", 0) + c.get("fn", 0) >= 10,
        "error_capture_wilson_lower_at_least_50pct": error_ci.get("lower", -1) >= .50,
        "false_review_wilson_upper_at_most_20pct": false_ci.get("upper", 2) <= .20,
        "review_precision_wilson_lower_at_least_33pct": precision_ci.get("lower", -1) >= .33,
        "trigger_rate_at_most_25pct": trigger is not None and trigger <= .25,
        "leave_one_out_invariant": loocv.get("shadow_gate_invariant") is True,
    }
    review = bool(shadow and all(review_requirements.values()))
    return {
        "status": "REVIEW_MODE_CANDIDATE" if review else ("SHADOW_REVIEW_CANDIDATE" if shadow else "NO_REVIEW_MODE_YET"),
        "shadow_pass": shadow,
        "review_pass": review,
        "review_requirements": review_requirements,
        "current_dataset_review_hard_cap_applied": metrics.get("eligible_samples", 0) < 50 or c.get("tp", 0) + c.get("fn", 0) < 10,
    }


def validate_multisensor_benchmark_v2_artifact(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise BenchmarkIntegrityError("artifact_must_be_an_object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkIntegrityError("unsupported_schema_version")
    required = (
        "dataset", "input_versions", "s2_baseline", "sample_matrix",
        "candidate_rules", "combinations", "uncertainty", "known_s2_errors",
        "false_reviews", "soak_reproducibility", "recommendation_gate",
        "warnings", "methodology",
    )
    missing = [name for name in required if name not in document]
    if missing:
        raise BenchmarkIntegrityError("artifact_missing_fields:" + ",".join(missing))
    _json_bytes(document)  # rejects NaN/Infinity
    return document


def load_multisensor_benchmark_v2_artifact(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_multisensor_benchmark_v2_artifact(document)


def write_multisensor_benchmark_v2_artifact(path: str | Path, document: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validated = validate_multisensor_benchmark_v2_artifact(dict(document))
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(destination)


def build_validation_multisensor_benchmark_v2(
    validation_rows: Sequence[Mapping[str, Any]],
    temporal_benchmark: Mapping[str, Any],
    soak_runs: Sequence[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
    expected_soak_repetitions: int = 3,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    holdout: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Consolidate immutable inputs without assigning scientific weight to soak repeats."""

    if expected_soak_repetitions <= 0:
        raise ValueError("expected_soak_repetitions must be positive")
    validations = _unique_index(validation_rows, "sample_id", "validation")
    temporal_rows = list(temporal_benchmark.get("samples") or [])
    temporal = _unique_index(temporal_rows, "sample_id", "temporal")
    unknown_temporal = sorted(set(temporal) - set(validations))
    if unknown_temporal:
        raise BenchmarkIntegrityError("temporal_unknown_sample_ids:" + ",".join(unknown_temporal))

    grouped_soak: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_soak_keys: set[tuple[str, Any, str]] = set()
    for raw in soak_runs:
        run = dict(raw)
        identifier = str(run.get("aoi_id") or "")
        if not identifier:
            raise BenchmarkIntegrityError("soak_missing_aoi_id")
        period_key = json.dumps(run.get("analysis_period"), sort_keys=True)
        unique_key = (identifier, run.get("repetition"), period_key)
        if unique_key in seen_soak_keys:
            raise BenchmarkIntegrityError(f"soak_duplicate_run:{identifier}:{run.get('repetition')}")
        seen_soak_keys.add(unique_key)
        grouped_soak[identifier].append(run)
    unknown_soak = sorted(set(grouped_soak) - set(validations))
    if unknown_soak:
        raise BenchmarkIntegrityError("soak_unknown_aoi_ids:" + ",".join(unknown_soak))

    integrity_issues: list[str] = []
    matrix: list[dict[str, Any]] = []
    for sample_id, validation in sorted(validations.items()):
        temporal_source = temporal.get(sample_id)
        if temporal_source is None:
            integrity_issues.append(f"temporal_missing:{sample_id}")
        sample_soak = sorted(grouped_soak.get(sample_id, []), key=lambda item: (item.get("repetition", 0), item.get("run_index", 0)))
        if len(sample_soak) != expected_soak_repetitions:
            integrity_issues.append(f"soak_run_count:{sample_id}:{len(sample_soak)}/{expected_soak_repetitions}")
        s2 = _extract_s2(validation)
        s1_temporal = _extract_temporal(temporal_source)
        truth, decision = validation.get("maintenance_truth"), s2.get("decision")
        compact = {
            "sample_id": sample_id,
            "vegetation_class": validation.get("vegetation_class"),
            "maintenance_truth": truth,
            "reference_date": validation.get("reference_date"),
            "evaluation_partition": _partition(truth, decision),
            "s2": s2,
            "s2_decision": decision,
            "s1_static": _extract_static_s1(validation),
            "s1_temporal": s1_temporal,
            "s1_combined_status": (
                s1_temporal["combined_status"]
                if s1_temporal["available"]
                else "insufficient_data"
            ),
            "s2_was_correct": _s2_correct(truth, decision),
            "soak_reproducibility": _aggregate_soak(sample_soak, expected_soak_repetitions),
        }
        compact["rules_triggered"] = [name for name in ("A", "B", "C") if RULES[name]["predicate"](compact)] if compact["evaluation_partition"] == "binary_evaluation" else []
        matrix.append(compact)

    binary = [row for row in matrix if row["evaluation_partition"] == "binary_evaluation"]
    per_protocol = [row for row in binary if row["s1_temporal"]["available"]]
    exact_clusters = sum(row["soak_reproducibility"]["exact_agreement"] for row in matrix)
    scientific_temporal_available = sum(row["s1_temporal"]["available"] for row in matrix)
    soak_complete = bool(matrix) and all(row["soak_reproducibility"]["complete"] for row in matrix)
    exact_rate = exact_clusters / len(matrix) if matrix else 0.0
    temporal_rate = scientific_temporal_available / len(matrix) if matrix else 0.0
    technical_requirements = {
        "soak_complete": soak_complete,
        "soak_exact_agreement_rate_at_least_95pct": exact_rate >= .95,
        "scientific_temporal_availability_at_least_90pct": temporal_rate >= .90,
        "join_and_schema_integrity": not integrity_issues,
    }
    technical_pass = all(technical_requirements.values())
    holdout_document = {"independent": False, "preregistered": False, **dict(holdout or {})}

    evaluations: dict[str, Any] = {}
    false_reviews: dict[str, Any] = {}
    for candidate in REVIEW_CANDIDATES:
        primary = _metrics(binary, candidate, bootstrap_iterations=bootstrap_iterations)
        protocol = _metrics(per_protocol, candidate, bootstrap_iterations=bootstrap_iterations)
        leave_one_out = _loocv(binary, candidate, technical_pass=technical_pass)
        evaluations[candidate] = {
            "rules": list(_rule_names(candidate)),
            "primary_intention_to_review": primary,
            "secondary_s1_available_only": protocol,
            "leave_one_sample_out": leave_one_out,
            "gate": _gate(primary, leave_one_out, technical_pass=technical_pass, holdout=holdout_document),
        }
        false_reviews[candidate] = _false_reviews(binary, candidate)

    candidate_rules = {
        name: {
            "description": RULES[name]["description"],
            "signal": RULES[name]["signal"],
            **evaluations[name],
        }
        for name in ("A", "B", "C")
    }
    combinations = {name: evaluations[name] for name in ("A+B", "A+B+C")}

    d_eligible = [row for row in matrix if row["maintenance_truth"] in BINARY_TRUTHS and row["s2"]["decision"] == "inconclusivo"]
    complementary = {
        "D": {
            "description": RULES["D"]["description"],
            "signal": "complementary_evidence",
            "eligible_samples": len(d_eligible),
            "with_temporal_evidence": sum(row["s1_temporal"]["available"] for row in d_eligible),
            "creates_decision": False,
        }
    }
    all_gates = [candidate_rules[name]["gate"]["status"] for name in ("A", "B", "C")] + [combinations[name]["gate"]["status"] for name in combinations]
    overall = "REVIEW_MODE_CANDIDATE" if "REVIEW_MODE_CANDIDATE" in all_gates else ("SHADOW_REVIEW_CANDIDATE" if "SHADOW_REVIEW_CANDIDATE" in all_gates else "NO_REVIEW_MODE_YET")

    known_errors = []
    by_id = {row["sample_id"]: row for row in matrix}
    for sample_id in KNOWN_S2_ERROR_IDS:
        row = by_id.get(sample_id)
        known_errors.append({
            "sample_id": sample_id,
            "found": row is not None,
            **({
                "maintenance_truth": row["maintenance_truth"], "s2": row["s2"],
                "s1_static": row["s1_static"], "s1_temporal": row["s1_temporal"],
                "rules_triggered": row["rules_triggered"],
                "soak_reproducibility": row["soak_reproducibility"],
            } if row else {}),
        })

    truth_counts = Counter(str(row["maintenance_truth"]) for row in matrix)
    partition_counts = Counter(str(row["evaluation_partition"]) for row in matrix)
    warnings = list(integrity_issues)
    if len(binary) < 50:
        warnings.append("review_mode_hard_cap:binary_truths_below_50")
    error_count = sum(row["s2_was_correct"] is False for row in binary)
    if error_count < 10:
        warnings.append("review_mode_hard_cap:s2_errors_below_10")
    if error_count == 2:
        warnings.append("small_error_sample:error_capture_changes_in_50_percentage_point_steps")

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "experimental": True,
        "input_fingerprints": {
            "validation_samples_sha256": input_sha256(list(validation_rows)),
            "temporal_benchmark_sha256": input_sha256(dict(temporal_benchmark)),
            "sentinel1_soak_runs_sha256": input_sha256(list(soak_runs)),
        },
        "input_versions": {
            "validation_schema_versions": sorted({row.get("schema_version") for row in validation_rows if row.get("schema_version") is not None}),
            "temporal_experimental": temporal_benchmark.get("experimental"),
            "soak_schema_versions": sorted({str(row.get("schema_version")) for row in soak_runs}),
            "source_timestamps": {
                "validation_created_at_min": min((str(row.get("created_at")) for row in validation_rows if row.get("created_at")), default=None),
                "validation_created_at_max": max((str(row.get("created_at")) for row in validation_rows if row.get("created_at")), default=None),
                "temporal_generated_at": temporal_benchmark.get("generated_at"),
                "soak_started_at_min": min((str(row.get("started_at")) for row in soak_runs if row.get("started_at")), default=None),
                "soak_finished_at_max": max((str(row.get("finished_at")) for row in soak_runs if row.get("finished_at")), default=None),
            },
        },
        "dataset": {
            "scientific_unit": "validation_sample/aoi",
            "sample_count": len(matrix),
            "scientific_sample_count": len(matrix),
            "soak_run_count": len(soak_runs),
            "soak_scientific_weight": 0,
            "counts_by_maintenance_truth": dict(sorted(truth_counts.items())),
            "partitions": dict(sorted(partition_counts.items())),
            "binary_s2_error_count": error_count,
        },
        "s2_baseline": _s2_baseline(matrix, bootstrap_iterations=bootstrap_iterations),
        "sample_matrix": matrix,
        "candidate_rules": candidate_rules,
        "combinations": combinations,
        "complementary_evidence": complementary,
        "uncertainty": {
            "wilson_confidence_level": .95,
            "bootstrap_iterations": bootstrap_iterations,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "leave_one_sample_out": True,
            "small_sample_warning": "Only two observed S2 errors; error capture moves in 50 percentage point steps." if error_count == 2 else None,
        },
        "known_s2_errors": known_errors,
        "false_reviews": false_reviews,
        "soak_reproducibility": {
            "run_count": len(soak_runs), "cluster_count": len(matrix),
            "expected_repetitions_per_aoi": expected_soak_repetitions,
            "complete": soak_complete, "exact_agreement_aoi_count": exact_clusters,
            "exact_agreement_rate": exact_rate, "scientific_weight": 0,
            "period_comparable_with_scientific_temporal": False,
        },
        "recommendation_gate": {
            "overall_status": overall,
            "technical_requirements": technical_requirements,
            "technical_pass": technical_pass,
            "holdout": holdout_document,
            "maximum_status_without_independent_preregistered_holdout": "SHADOW_REVIEW_CANDIDATE",
        },
        "warnings": sorted(set(warnings)),
        "methodology": {
            "purpose": "Offline conservative review benchmark; not operational fusion.",
            "primary_analysis": "intention_to_review; unavailable S1 means no trigger",
            "secondary_analysis": "per_protocol:s1_available_only",
            "candidate_set_predefined": ["A", "B", "C", "A+B", "A+B+C"],
            "threshold_optimization_performed": False,
            "new_rules_generated": False,
            "soak_repetitions_are_independent_scientific_samples": False,
            "uncertain_ground_truth_used_for_supervised_metrics": False,
            "runtime_fusion_applied": False,
            "recommendation_changed": False,
            "physical_interpretation": "none",
            "risks": [
                "rule_selection_and_evaluation_on_the_same_dataset", "only_two_s2_error_events",
                "class_imbalance", "possible_spatial_correlation_between_aois",
                "scientific_and_soak_s1_periods_differ", "multiple_candidate_comparisons",
                "leave_one_out_instability", "temporal_status_sensitivity_to_period",
                "pseudo_replication_if_soak_runs_are_misused",
            ],
        },
    }
    return validate_multisensor_benchmark_v2_artifact(artifact)
