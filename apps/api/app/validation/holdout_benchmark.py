"""Independent, preregistered holdout infrastructure for shadow review rule B."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.satellite_monitoring.multisource.holdout_contract import (
    HOLDOUT_CANDIDATE_RULE,
    HOLDOUT_ENGINEERING_GATES,
    HOLDOUT_EXCLUSION_CRITERIA,
    HOLDOUT_INCLUSION_CRITERIA,
    HOLDOUT_RULE_DEFINITION,
    HOLDOUT_SCHEMA_VERSION,
)


SCHEMA_VERSION = HOLDOUT_SCHEMA_VERSION
CANDIDATE_RULE = HOLDOUT_CANDIDATE_RULE
VALID_TEMPORAL_STATUSES = {"increasing", "decreasing", "stable", "mixed"}
PREREGISTRATION_STATUSES = {"draft", "collecting", "frozen", "evaluated"}
FROZEN_STATUSES = {"frozen", "evaluated"}
RULE_DEFINITION = HOLDOUT_RULE_DEFINITION
ENGINEERING_GATES = HOLDOUT_ENGINEERING_GATES
INCLUSION_CRITERIA = HOLDOUT_INCLUSION_CRITERIA
EXCLUSION_CRITERIA = HOLDOUT_EXCLUSION_CRITERIA


class HoldoutIntegrityError(ValueError):
    """Raised when preregistration, cohort identity, or artifact integrity fails."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sample_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": row.get("sample_id"),
        "analysis_id": row.get("analysis_id"),
        "aoi_fingerprint": row.get("aoi_fingerprint"),
        "cohort": row.get("cohort", "development"),
        "schema_version": row.get("schema_version"),
        "created_at": row.get("created_at"),
        "vegetation_class": row.get("vegetation_class"),
        "maintenance_truth": row.get("maintenance_truth"),
        "reference_date": row.get("reference_date"),
        "s2_decision": row.get("s2_decision"),
        "s2_confidence": row.get("s2_confidence"),
    }


def _preregistration_core(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": document.get("schema_version"),
        "created_at": document.get("created_at"),
        "frozen_at": document.get("frozen_at"),
        "independent_holdout": document.get("independent_holdout"),
        "preregistered": document.get("preregistered"),
        "candidate_rule": document.get("candidate_rule"),
        "rule_definition": document.get("rule_definition"),
        "engineering_gates": document.get("engineering_gates"),
        "dataset_inclusion_criteria": document.get("dataset_inclusion_criteria"),
        "dataset_exclusion_criteria": document.get("dataset_exclusion_criteria"),
        "methodology_notes": document.get("methodology_notes"),
        "frozen_dataset": document.get("frozen_dataset"),
    }


def create_preregistration(
    *,
    created_at: str | None = None,
    methodology_notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    notes = list(methodology_notes or [])
    if any(
        not isinstance(note, str) or not note.strip() or len(note) > 1000 or "\x00" in note
        for note in notes
    ):
        raise ValueError("methodology notes must be non-empty plain strings up to 1000 characters")
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "frozen_at": None,
        "evaluated_at": None,
        "status": "draft",
        "independent_holdout": True,
        "preregistered": False,
        "candidate_rule": CANDIDATE_RULE,
        "rule_definition": dict(RULE_DEFINITION),
        "engineering_gates": dict(ENGINEERING_GATES),
        "dataset_inclusion_criteria": list(INCLUSION_CRITERIA),
        "dataset_exclusion_criteria": list(EXCLUSION_CRITERIA),
        "methodology_notes": notes,
        "fingerprints": None,
        "frozen_dataset": None,
    }


def validate_preregistration(
    document: Any,
    *,
    require_frozen: bool = False,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HoldoutIntegrityError("preregistration_must_be_an_object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise HoldoutIntegrityError("unsupported_preregistration_schema_version")
    if document.get("status") not in PREREGISTRATION_STATUSES:
        raise HoldoutIntegrityError("invalid_preregistration_status")
    if document.get("independent_holdout") is not True:
        raise HoldoutIntegrityError("holdout_not_declared_independent")
    expected_preregistered = document.get("status") in FROZEN_STATUSES
    if document.get("preregistered") is not expected_preregistered:
        raise HoldoutIntegrityError("invalid_preregistered_state")
    if document.get("candidate_rule") != CANDIDATE_RULE:
        raise HoldoutIntegrityError("candidate_rule_changed")
    if document.get("rule_definition") != RULE_DEFINITION:
        raise HoldoutIntegrityError("rule_definition_changed")
    if document.get("engineering_gates") != ENGINEERING_GATES:
        raise HoldoutIntegrityError("engineering_gates_changed")
    if document.get("dataset_inclusion_criteria") != INCLUSION_CRITERIA:
        raise HoldoutIntegrityError("inclusion_criteria_changed")
    if document.get("dataset_exclusion_criteria") != EXCLUSION_CRITERIA:
        raise HoldoutIntegrityError("exclusion_criteria_changed")
    notes = document.get("methodology_notes")
    if not isinstance(notes, list) or any(
        not isinstance(note, str) or not note.strip() or len(note) > 1000 or "\x00" in note
        for note in notes
    ):
        raise HoldoutIntegrityError("invalid_methodology_notes")
    if require_frozen and document.get("status") not in FROZEN_STATUSES:
        raise HoldoutIntegrityError("preregistration_not_frozen")
    if document.get("status") in FROZEN_STATUSES:
        fingerprints = _mapping(document.get("fingerprints"))
        expected = fingerprint(_preregistration_core(document))
        if fingerprints.get("preregistration_sha256") != expected:
            raise HoldoutIntegrityError("frozen_preregistration_fingerprint_mismatch")
        frozen_dataset = _mapping(document.get("frozen_dataset"))
        if fingerprints.get("holdout_dataset_sha256") != frozen_dataset.get(
            "dataset_sha256"
        ):
            raise HoldoutIntegrityError("frozen_holdout_fingerprint_mismatch")
    try:
        _canonical(document)
    except (TypeError, ValueError) as exc:
        raise HoldoutIntegrityError("preregistration_not_json_safe") from exc
    return document


def mark_preregistration_collecting(document: Mapping[str, Any]) -> dict[str, Any]:
    current = validate_preregistration(dict(document))
    if current["status"] != "draft":
        raise HoldoutIntegrityError("only_draft_can_enter_collecting")
    return {**current, "status": "collecting"}


def mark_preregistration_evaluated(
    document: Mapping[str, Any], *, evaluated_at: str | None = None
) -> dict[str, Any]:
    current = validate_preregistration(dict(document), require_frozen=True)
    if current["status"] == "evaluated":
        raise HoldoutIntegrityError("preregistration_already_evaluated")
    result = {
        **current,
        "status": "evaluated",
        "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat(),
    }
    return validate_preregistration(result, require_frozen=True)


def _validate_unique_identities(rows: Sequence[Mapping[str, Any]]) -> None:
    for field in ("sample_id", "analysis_id", "aoi_fingerprint"):
        seen: dict[str, str] = {}
        for row in rows:
            value = row.get(field)
            if value in (None, ""):
                if field == "aoi_fingerprint":
                    if row.get("cohort", "development") == "holdout":
                        raise HoldoutIntegrityError(
                            f"holdout_aoi_fingerprint_missing:{row.get('sample_id')}"
                        )
                    continue
                raise HoldoutIntegrityError(f"validation_{field}_missing")
            text = str(value)
            if text in seen:
                if field != "aoi_fingerprint" or row.get("cohort") == "holdout":
                    raise HoldoutIntegrityError(
                        f"duplicate_{field}:{seen[text]}:{row.get('sample_id')}"
                    )
                previous = next(
                    item for item in rows if str(item.get("sample_id")) == seen[text]
                )
                if previous.get("cohort", "development") == "holdout":
                    raise HoldoutIntegrityError(
                        f"duplicate_{field}:{seen[text]}:{row.get('sample_id')}"
                    )
                continue
            seen[text] = str(row.get("sample_id"))


def freeze_preregistration(
    document: Mapping[str, Any],
    validation_rows: Sequence[Mapping[str, Any]],
    *,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    current = validate_preregistration(dict(document))
    if current["status"] not in {"draft", "collecting"}:
        raise HoldoutIntegrityError("preregistration_already_frozen")
    _validate_unique_identities(validation_rows)
    holdout = sorted(
        (dict(row) for row in validation_rows if row.get("cohort", "development") == "holdout"),
        key=lambda row: str(row["sample_id"]),
    )
    identities = [_sample_identity(row) for row in holdout]
    frozen_dataset = {
        "sample_count": len(holdout),
        "sample_ids": [str(row["sample_id"]) for row in holdout],
        "sample_fingerprints": {
            str(row["sample_id"]): fingerprint(identity)
            for row, identity in zip(holdout, identities)
        },
        "dataset_sha256": fingerprint(identities),
    }
    result = {
        **current,
        "status": "frozen",
        "preregistered": True,
        "frozen_at": frozen_at or datetime.now(timezone.utc).isoformat(),
        "frozen_dataset": frozen_dataset,
    }
    result["fingerprints"] = {
        "holdout_dataset_sha256": frozen_dataset["dataset_sha256"],
        "preregistration_sha256": fingerprint(_preregistration_core(result)),
    }
    return validate_preregistration(result, require_frozen=True)


def verify_frozen_holdout(
    preregistration: Mapping[str, Any],
    validation_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    frozen = validate_preregistration(dict(preregistration), require_frozen=True)
    _validate_unique_identities(validation_rows)
    holdout = sorted(
        (dict(row) for row in validation_rows if row.get("cohort", "development") == "holdout"),
        key=lambda row: str(row["sample_id"]),
    )
    frozen_dataset = _mapping(frozen.get("frozen_dataset"))
    sample_ids = [str(row["sample_id"]) for row in holdout]
    if sample_ids != frozen_dataset.get("sample_ids"):
        raise HoldoutIntegrityError("frozen_holdout_membership_changed")
    current_fingerprints = {
        str(row["sample_id"]): fingerprint(_sample_identity(row)) for row in holdout
    }
    if current_fingerprints != frozen_dataset.get("sample_fingerprints"):
        raise HoldoutIntegrityError("frozen_holdout_sample_changed")
    if fingerprint([_sample_identity(row) for row in holdout]) != frozen_dataset.get("dataset_sha256"):
        raise HoldoutIntegrityError("frozen_holdout_dataset_fingerprint_mismatch")
    return holdout


def _wilson(successes: int, total: int) -> dict[str, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return {"lower": max(0.0, center - half), "upper": min(1.0, center + half)}


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "wilson_95": _wilson(numerator, denominator),
    }


def _is_s2_correct(row: Mapping[str, Any]) -> bool:
    return row["s2_decision"] == (
        "cortar" if row["maintenance_truth"] == "cut" else "nao_cortar"
    )


def _review_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for row in rows:
        triggered = bool(row["rule_b_triggered"])
        correct = bool(row["s2_was_correct"])
        key = ("fp" if triggered else "tn") if correct else ("tp" if triggered else "fn")
        counts[key] += 1
    total = sum(counts.values())
    capture = _rate(counts["tp"], counts["tp"] + counts["fn"])
    specificity = _rate(counts["tn"], counts["tn"] + counts["fp"])
    balanced = (
        (capture["value"] + specificity["value"]) / 2
        if capture["value"] is not None and specificity["value"] is not None
        else None
    )
    return {
        "eligible_samples": total,
        "triggered_samples": counts["tp"] + counts["fp"],
        "confusion": counts,
        "error_capture_rate": capture,
        "false_review_rate": _rate(counts["fp"], counts["fp"] + counts["tn"]),
        "review_precision": _rate(counts["tp"], counts["tp"] + counts["fp"]),
        "trigger_rate": _rate(counts["tp"] + counts["fp"], total),
        "specificity": specificity,
        "balanced_accuracy": balanced,
    }


def _performance_gate(metrics: Mapping[str, Any]) -> bool:
    capture = _mapping(_mapping(metrics.get("error_capture_rate")).get("wilson_95"))
    false_review = _mapping(_mapping(metrics.get("false_review_rate")).get("wilson_95"))
    precision = _mapping(_mapping(metrics.get("review_precision")).get("wilson_95"))
    trigger = _mapping(metrics.get("trigger_rate")).get("value")
    return bool(
        capture.get("lower", -1) >= ENGINEERING_GATES["error_capture_wilson_lower_min"]
        and false_review.get("upper", 2) <= ENGINEERING_GATES["false_review_rate_wilson_upper_max"]
        and precision.get("lower", -1) >= ENGINEERING_GATES["review_precision_wilson_lower_min"]
        and trigger is not None
        and trigger <= ENGINEERING_GATES["trigger_rate_max"]
    )


def _leave_one_out(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = (
        "error_capture_rate", "false_review_rate", "review_precision",
        "trigger_rate", "specificity",
    )
    values: dict[str, list[float]] = defaultdict(list)
    gates: list[bool] = []
    for index in range(len(rows)):
        metrics = _review_metrics([*rows[:index], *rows[index + 1 :]])
        gates.append(_performance_gate(metrics))
        for name in names:
            value = _mapping(metrics[name]).get("value")
            if value is not None:
                values[name].append(value)
    return {
        "fold_count": len(rows),
        "performance_gate_invariant": len(set(gates)) <= 1 if gates else None,
        "performance_gate_passes": sum(gates),
        "metric_ranges": {
            name: ({"min": min(items), "max": max(items)} if items else None)
            for name, items in values.items()
        },
    }


def _s2_baseline(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tp = sum(row["maintenance_truth"] == "cut" and row["s2_decision"] == "cortar" for row in rows)
    fn = sum(row["maintenance_truth"] == "cut" and row["s2_decision"] == "nao_cortar" for row in rows)
    fp = sum(row["maintenance_truth"] == "no_cut" and row["s2_decision"] == "cortar" for row in rows)
    tn = sum(row["maintenance_truth"] == "no_cut" and row["s2_decision"] == "nao_cortar" for row in rows)
    recall, specificity = _rate(tp, tp + fn), _rate(tn, tn + fp)
    return {
        "positive_class": "cut",
        "eligible_samples": len(rows),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "accuracy": _rate(tp + tn, len(rows)),
        "precision_cut": _rate(tp, tp + fp),
        "recall_cut": recall,
        "specificity": specificity,
        "balanced_accuracy": (
            (recall["value"] + specificity["value"]) / 2
            if recall["value"] is not None and specificity["value"] is not None
            else None
        ),
    }


def _temporal_index(temporal_benchmark: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in temporal_benchmark.get("samples") or []:
        row = _mapping(source)
        sample_id = str(row.get("sample_id") or "")
        if not sample_id:
            raise HoldoutIntegrityError("temporal_sample_id_missing")
        if sample_id in result:
            raise HoldoutIntegrityError(f"temporal_sample_duplicate:{sample_id}")
        result[sample_id] = row
    return result


def build_validation_holdout_benchmark_v1(
    validation_rows: Sequence[Mapping[str, Any]],
    preregistration: Mapping[str, Any],
    temporal_benchmark: Mapping[str, Any],
    *,
    soak_runs: Sequence[Mapping[str, Any]] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    holdout = verify_frozen_holdout(preregistration, validation_rows)
    temporal = _temporal_index(temporal_benchmark)
    matrix: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in holdout:
        sample_id = str(row["sample_id"])
        temporal_row = temporal.get(sample_id)
        if temporal_row is None:
            warnings.append(f"temporal_sample_missing:{sample_id}")
            temporal_row = {}
        status = temporal_row.get("combined_status", "insufficient_data")
        evaluable = (
            temporal_row.get("processing_status") == "completed"
            and status in VALID_TEMPORAL_STATUSES
        )
        truth, decision = row.get("maintenance_truth"), row.get("s2_decision")
        binary = truth in {"cut", "no_cut"} and decision in {"cortar", "nao_cortar"}
        matrix.append({
            "sample_id": sample_id,
            "vegetation_class": row.get("vegetation_class"),
            "maintenance_truth": truth,
            "s2_decision": decision,
            "s2_confidence": row.get("s2_confidence"),
            "supervised_eligible": binary,
            "s1_available": temporal_row.get("processing_status") == "completed",
            "review_evaluable": evaluable,
            "s1_temporal_status": status,
            "canonical_relative_orbit": temporal_row.get("canonical_relative_orbit"),
            "rule_b_triggered": bool(evaluable and decision == "cortar" and status == "mixed"),
            "s2_was_correct": _is_s2_correct({"maintenance_truth": truth, "s2_decision": decision}) if binary else None,
        })

    binary_rows = [row for row in matrix if row["supervised_eligible"]]
    available_rows = [row for row in binary_rows if row["review_evaluable"]]
    primary = _review_metrics(binary_rows)
    per_protocol = _review_metrics(available_rows)
    robustness = _leave_one_out(binary_rows)
    errors = primary["confusion"]["tp"] + primary["confusion"]["fn"]
    enough_n = len(binary_rows) >= ENGINEERING_GATES["minimum_binary_truths"]
    enough_errors = errors >= ENGINEERING_GATES["minimum_s2_errors"]
    loo_pass = robustness["performance_gate_invariant"] is True
    performance_pass = _performance_gate(primary)
    if not enough_n or not enough_errors:
        status = "INSUFFICIENT_HOLDOUT_DATA"
    elif performance_pass and loo_pass:
        status = "REVIEW_MODE_CANDIDATE"
    else:
        status = "SHADOW_REVIEW_CANDIDATE"

    truth_counts = Counter(str(row.get("maintenance_truth")) for row in matrix)
    vegetation = Counter(str(row.get("vegetation_class")) for row in matrix)
    orbits = Counter(str(row.get("canonical_relative_orbit")) for row in matrix)
    soak_aoi_ids = {str(row.get("aoi_id")) for row in soak_runs if row.get("aoi_id")}
    if soak_runs:
        warnings.append("soak_runs_have_scientific_weight_zero")
    if truth_counts.get("uncertain", 0):
        warnings.append("uncertain_ground_truth_excluded_from_supervised_metrics")
    if not enough_n:
        warnings.append("minimum_binary_truths_not_met")
    if not enough_errors:
        warnings.append("minimum_s2_errors_not_met")

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "experimental": True,
        "input_fingerprints": {
            "validation_holdout_sha256": fingerprint([_sample_identity(row) for row in holdout]),
            "preregistration_sha256": _mapping(preregistration.get("fingerprints")).get("preregistration_sha256"),
            "temporal_benchmark_sha256": fingerprint(dict(temporal_benchmark)),
            "soak_runs_sha256": fingerprint(list(soak_runs)),
        },
        "preregistration": {
            key: preregistration.get(key)
            for key in (
                "schema_version", "created_at", "frozen_at", "status",
                "independent_holdout", "preregistered",
                "candidate_rule", "rule_definition", "engineering_gates",
                "dataset_inclusion_criteria", "dataset_exclusion_criteria",
                "methodology_notes", "fingerprints", "frozen_dataset",
            )
        },
        "dataset": {
            "total_samples": len(matrix),
            "binary_samples": len(binary_rows),
            "cut_samples": truth_counts.get("cut", 0),
            "no_cut_samples": truth_counts.get("no_cut", 0),
            "uncertain_samples": truth_counts.get("uncertain", 0),
            "s2_errors": errors,
            "s1_available_samples": sum(row["s1_available"] for row in matrix),
            "s1_availability_rate": _rate(sum(row["s1_available"] for row in matrix), len(matrix)),
            "review_evaluable_samples": len(available_rows),
            "review_evaluable_rate": _rate(len(available_rows), len(binary_rows)),
            "distribution_by_vegetation_class": dict(sorted(vegetation.items())),
            "distribution_by_canonical_relative_orbit": dict(sorted(orbits.items())),
            "soak_run_count": len(soak_runs),
            "soak_aoi_cluster_count": len(soak_aoi_ids),
            "soak_scientific_weight": 0,
        },
        "s2_baseline": _s2_baseline(binary_rows),
        "rule_b": {
            "definition": dict(RULE_DEFINITION),
            "intention_to_review": primary,
            "s1_available_only": per_protocol,
        },
        "uncertainty": {
            "method": "Wilson score interval",
            "confidence_level": 0.95,
            "small_sample_denominators_reported": True,
        },
        "robustness": robustness,
        "recommendation_gate": {
            "status": status,
            "requirements": {
                "independent_holdout": preregistration.get("independent_holdout") is True,
                "preregistered_holdout": preregistration.get("preregistered") is True,
                "minimum_binary_truths_met": enough_n,
                "minimum_s2_errors_met": enough_errors,
                "performance_wilson_gates_met": performance_pass,
                "leave_one_out_gate_invariant": loo_pass,
                "all_requirements_met": status == "REVIEW_MODE_CANDIDATE",
            },
            "missing_data_is_approval": False,
            "operational_change_applied": False,
        },
        "sample_audit": matrix,
        "warnings": sorted(set(warnings)),
        "methodology": {
            "candidate_rules_evaluated": ["B"],
            "primary_analysis": "intention_to_review; unavailable S1 is a non-trigger",
            "secondary_analysis": "s1_available_only",
            "uncertain_used_in_supervised_metrics": False,
            "soak_repetitions_are_scientific_samples": False,
            "geometry_in_artifact": False,
            "personal_notes_in_artifact": False,
            "raster_payload_in_artifact": False,
            "recommendation_changed": False,
            "runtime_fusion_applied": False,
        },
    }
    return validate_holdout_artifact(artifact)


def validate_holdout_artifact(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HoldoutIntegrityError("artifact_must_be_an_object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise HoldoutIntegrityError("unsupported_holdout_artifact_schema_version")
    required = (
        "generated_at", "experimental", "input_fingerprints", "preregistration",
        "dataset", "s2_baseline", "rule_b", "uncertainty", "robustness",
        "recommendation_gate", "sample_audit", "warnings", "methodology",
    )
    missing = [name for name in required if name not in document]
    if missing:
        raise HoldoutIntegrityError("artifact_missing_fields:" + ",".join(missing))
    validate_preregistration(
        _mapping(document.get("preregistration")), require_frozen=True
    )
    if _mapping(document.get("methodology")).get("candidate_rules_evaluated") != ["B"]:
        raise HoldoutIntegrityError("artifact_contains_unregistered_candidate_rules")
    if _mapping(document.get("recommendation_gate")).get("status") not in {
        "INSUFFICIENT_HOLDOUT_DATA",
        "SHADOW_REVIEW_CANDIDATE",
        "REVIEW_MODE_CANDIDATE",
    }:
        raise HoldoutIntegrityError("invalid_holdout_gate_status")
    forbidden = {"geometry", "geometry_geojson", "raster", "raster_payload", "observations"}

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            present = forbidden.intersection(value)
            if present:
                raise HoldoutIntegrityError(
                    "artifact_contains_forbidden_payload:" + ",".join(sorted(present))
                )
            for nested in value.values():
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(document)
    try:
        _canonical(document)
    except (TypeError, ValueError) as exc:
        raise HoldoutIntegrityError("artifact_not_json_safe") from exc
    return document


def load_holdout_artifact(path: str | Path) -> dict[str, Any]:
    return validate_holdout_artifact(json.loads(Path(path).read_text(encoding="utf-8")))


def write_json_atomic(path: str | Path, document: Mapping[str, Any], *, overwrite: bool = False) -> None:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
