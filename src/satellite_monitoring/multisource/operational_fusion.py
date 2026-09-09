"""Fail-closed authorization and audit scaffolding for future operational fusion.

This module deliberately contains no operational decision policy. Authorization
is necessary but never sufficient to change the official Sentinel-2 result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..config import MonitoringConfig
from .holdout_contract import (
    HOLDOUT_CANDIDATE_RULE,
    HOLDOUT_ENGINEERING_GATES,
    HOLDOUT_EXCLUSION_CRITERIA,
    HOLDOUT_INCLUSION_CRITERIA,
    HOLDOUT_RULE_DEFINITION,
    HOLDOUT_SCHEMA_VERSION,
)


logger = logging.getLogger(__name__)
OPERATIONAL_FUSION_SCHEMA_VERSION = "1.0"


class OperationalFusionPolicy(Protocol):
    """Interface reserved for a separately preregistered decision policy."""

    version: str

    def evaluate(
        self,
        *,
        official_s2_recommendation: str,
        sentinel1_evidence: Mapping[str, Any] | None,
        temporal_status: str | None,
        authorization: Mapping[str, Any],
        policy_version: str,
    ) -> Mapping[str, Any]: ...


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


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


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


def _wilson(successes: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = z * math.sqrt(
        (proportion * (1 - proportion) + z * z / (4 * total)) / total
    ) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _confusion(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for row in rows:
        triggered = row["rule_b_triggered"]
        correct = row["s2_was_correct"]
        key = ("fp" if triggered else "tn") if correct else ("tp" if triggered else "fn")
        counts[key] += 1
    return counts


def _performance_gate(counts: Mapping[str, int]) -> bool:
    tp, fn, fp, tn = (counts[name] for name in ("tp", "fn", "fp", "tn"))
    capture = _wilson(tp, tp + fn)
    false_review = _wilson(fp, fp + tn)
    precision = _wilson(tp, tp + fp)
    total = tp + fn + fp + tn
    trigger_rate = (tp + fp) / total if total else None
    return bool(
        capture
        and false_review
        and precision
        and capture[0] >= HOLDOUT_ENGINEERING_GATES["error_capture_wilson_lower_min"]
        and false_review[1] <= HOLDOUT_ENGINEERING_GATES["false_review_rate_wilson_upper_max"]
        and precision[0] >= HOLDOUT_ENGINEERING_GATES["review_precision_wilson_lower_min"]
        and trigger_rate is not None
        and trigger_rate <= HOLDOUT_ENGINEERING_GATES["trigger_rate_max"]
    )


def _revalidate_scientific_gate(artifact: Mapping[str, Any]) -> bool:
    """Recompute the gate from the audit rows instead of trusting its label."""
    audit = artifact.get("sample_audit")
    if not isinstance(audit, list):
        raise ValueError("sample_audit_invalid")
    frozen = _mapping(_mapping(artifact.get("preregistration")).get("frozen_dataset"))
    if [str(row.get("sample_id")) for row in audit] != frozen.get("sample_ids"):
        raise ValueError("sample_audit_membership_mismatch")
    binary: list[dict[str, Any]] = []
    for value in audit:
        row = _mapping(value)
        truth = row.get("maintenance_truth")
        decision = row.get("s2_decision")
        eligible = truth in {"cut", "no_cut"} and decision in {"cortar", "nao_cortar"}
        if row.get("supervised_eligible") is not eligible:
            raise ValueError("supervised_eligibility_mismatch")
        if not eligible:
            continue
        correct = decision == ("cortar" if truth == "cut" else "nao_cortar")
        expected_trigger = bool(
            row.get("review_evaluable")
            and decision == "cortar"
            and row.get("s1_temporal_status") == "mixed"
        )
        if row.get("s2_was_correct") is not correct:
            raise ValueError("s2_correctness_mismatch")
        if row.get("rule_b_triggered") is not expected_trigger:
            raise ValueError("rule_b_trigger_mismatch")
        binary.append({"s2_was_correct": correct, "rule_b_triggered": expected_trigger})

    counts = _confusion(binary)
    errors = counts["tp"] + counts["fn"]
    dataset = _mapping(artifact.get("dataset"))
    reported = _mapping(_mapping(artifact.get("rule_b")).get("intention_to_review"))
    if dataset.get("binary_samples") != len(binary) or dataset.get("s2_errors") != errors:
        raise ValueError("dataset_counts_mismatch")
    if _mapping(reported.get("confusion")) != counts:
        raise ValueError("review_confusion_mismatch")
    if len(binary) < HOLDOUT_ENGINEERING_GATES["minimum_binary_truths"]:
        return False
    if errors < HOLDOUT_ENGINEERING_GATES["minimum_s2_errors"]:
        return False
    if not _performance_gate(counts):
        return False
    leave_one_out = [_performance_gate(_confusion(binary[:index] + binary[index + 1 :])) for index in range(len(binary))]
    invariant = bool(leave_one_out) and len(set(leave_one_out)) == 1
    robustness = _mapping(artifact.get("robustness"))
    if robustness.get("performance_gate_invariant") is not invariant:
        raise ValueError("leave_one_out_mismatch")
    return invariant


def _denied(
    reason: str,
    *,
    requested: bool,
    status: str = "denied",
    schema_version: str | None = None,
    gate_status: str | None = None,
) -> dict[str, Any]:
    return {
        "requested": requested,
        "authorized": False,
        "authorization_status": status,
        "authorization_reason": reason,
        "holdout_schema_version": schema_version,
        "holdout_gate_status": gate_status,
        "candidate_rule": HOLDOUT_CANDIDATE_RULE,
    }


class OperationalFusionAuthorization:
    """Authorizes operational evaluation only after all frozen gates pass."""

    def authorize(self, config: MonitoringConfig) -> dict[str, Any]:
        requested = config.multisource_fusion_mode == "operational"
        if not requested:
            return _denied("operational_fusion_not_requested", requested=False, status="not_requested")
        if not (
            config.multisource_enabled
            and config.sentinel1_enabled
            and config.sentinel1_temporal.enabled
        ):
            return _denied("required_feature_flags_not_enabled", requested=True)
        path = config.validation_holdout_benchmark_path
        if path is None:
            return _denied("holdout_artifact_path_not_configured", requested=True)
        artifact_path = Path(path)
        if not artifact_path.is_file():
            return _denied("holdout_artifact_not_found", requested=True)
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            return self._validate_artifact(artifact)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return _denied("holdout_artifact_invalid", requested=True)

    def _validate_artifact(self, artifact: Any) -> dict[str, Any]:
        if not isinstance(artifact, dict):
            raise ValueError("artifact_not_object")
        required = {
            "schema_version", "generated_at", "input_fingerprints",
            "preregistration", "dataset", "rule_b", "robustness",
            "recommendation_gate", "sample_audit", "warnings", "methodology",
        }
        if not required.issubset(artifact):
            raise ValueError("artifact_missing_fields")
        schema = artifact.get("schema_version")
        gate = _mapping(artifact.get("recommendation_gate"))
        gate_status = gate.get("status")
        if schema != HOLDOUT_SCHEMA_VERSION:
            raise ValueError("unsupported_schema")
        prereg = _mapping(artifact.get("preregistration"))
        if prereg.get("schema_version") != HOLDOUT_SCHEMA_VERSION:
            raise ValueError("unsupported_preregistration_schema")
        if prereg.get("status") not in {"frozen", "evaluated"}:
            raise ValueError("preregistration_not_frozen")
        if prereg.get("independent_holdout") is not True or prereg.get("preregistered") is not True:
            raise ValueError("holdout_not_preregistered")
        if prereg.get("candidate_rule") != HOLDOUT_CANDIDATE_RULE:
            raise ValueError("candidate_rule_changed")
        if prereg.get("rule_definition") != HOLDOUT_RULE_DEFINITION:
            raise ValueError("rule_definition_changed")
        if prereg.get("engineering_gates") != HOLDOUT_ENGINEERING_GATES:
            raise ValueError("engineering_gates_changed")
        if prereg.get("dataset_inclusion_criteria") != HOLDOUT_INCLUSION_CRITERIA:
            raise ValueError("inclusion_criteria_changed")
        if prereg.get("dataset_exclusion_criteria") != HOLDOUT_EXCLUSION_CRITERIA:
            raise ValueError("exclusion_criteria_changed")

        frozen_dataset = _mapping(prereg.get("frozen_dataset"))
        fingerprints = _mapping(prereg.get("fingerprints"))
        artifact_fingerprints = _mapping(artifact.get("input_fingerprints"))
        prereg_sha = _fingerprint(_preregistration_core(prereg))
        dataset_sha = frozen_dataset.get("dataset_sha256")
        if not dataset_sha or fingerprints.get("holdout_dataset_sha256") != dataset_sha:
            raise ValueError("holdout_fingerprint_mismatch")
        if fingerprints.get("preregistration_sha256") != prereg_sha:
            raise ValueError("preregistration_fingerprint_mismatch")
        if artifact_fingerprints.get("preregistration_sha256") != prereg_sha:
            raise ValueError("artifact_preregistration_fingerprint_mismatch")
        if artifact_fingerprints.get("validation_holdout_sha256") != dataset_sha:
            raise ValueError("artifact_dataset_fingerprint_mismatch")

        methodology = _mapping(artifact.get("methodology"))
        if methodology.get("candidate_rules_evaluated") != [HOLDOUT_CANDIDATE_RULE]:
            raise ValueError("unregistered_candidate_rules")
        requirements = _mapping(gate.get("requirements"))
        required_flags = {
            "independent_holdout",
            "preregistered_holdout",
            "minimum_binary_truths_met",
            "minimum_s2_errors_met",
            "performance_wilson_gates_met",
            "leave_one_out_gate_invariant",
            "all_requirements_met",
        }
        if not required_flags.issubset(requirements) or not all(requirements[key] is True for key in required_flags):
            return _denied(
                "holdout_gate_not_satisfied", requested=True,
                schema_version=str(schema), gate_status=str(gate_status) if gate_status else None,
            )
        if not _revalidate_scientific_gate(artifact):
            return _denied(
                "holdout_gate_not_satisfied", requested=True,
                schema_version=str(schema), gate_status=str(gate_status) if gate_status else None,
            )
        warnings = artifact.get("warnings")
        if not isinstance(warnings, list):
            raise ValueError("warnings_invalid")
        invalid_markers = ("integrity", "fingerprint", "duplicate", "membership", "schema")
        if any(any(marker in str(warning).lower() for marker in invalid_markers) for warning in warnings):
            return _denied(
                "holdout_integrity_warning", requested=True,
                schema_version=str(schema), gate_status=str(gate_status) if gate_status else None,
            )
        if gate_status != "REVIEW_MODE_CANDIDATE":
            return _denied(
                "holdout_gate_not_satisfied", requested=True,
                schema_version=str(schema), gate_status=str(gate_status) if gate_status else None,
            )
        return {
            "requested": True,
            "authorized": True,
            "authorization_status": "authorized",
            "authorization_reason": "independent_holdout_gate_satisfied",
            "holdout_schema_version": str(schema),
            "holdout_gate_status": str(gate_status),
            "candidate_rule": HOLDOUT_CANDIDATE_RULE,
        }


def authorize_operational_fusion(config: MonitoringConfig) -> dict[str, Any]:
    """Default injectable authorization entry point."""
    return OperationalFusionAuthorization().authorize(config)


def authorization_error_result(config: MonitoringConfig) -> dict[str, Any]:
    return _denied(
        "authorization_layer_error",
        requested=config.multisource_fusion_mode == "operational",
    )


def attach_operational_fusion_audit(
    multisource: Mapping[str, Any],
    official_recommendation: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach an additive audit block; no operational policy exists yet."""
    result = dict(multisource)
    decision = official_recommendation.get(
        "recommendation", official_recommendation.get("decision")
    )
    audit = {
        "schema_version": OPERATIONAL_FUSION_SCHEMA_VERSION,
        "requested": bool(authorization.get("requested")),
        "authorized": bool(authorization.get("authorized")),
        "policy_available": False,
        "authorization_status": authorization.get("authorization_status"),
        "authorization_reason": authorization.get("authorization_reason"),
        "holdout_schema_version": authorization.get("holdout_schema_version"),
        "holdout_gate_status": authorization.get("holdout_gate_status"),
        "candidate_rule": authorization.get("candidate_rule", HOLDOUT_CANDIDATE_RULE),
        "policy_version": None,
        "original_recommendation": decision,
        "final_recommendation": decision,
        "official_recommendation_changed": False,
    }
    result["operational_fusion"] = audit
    result["official_recommendation_changed"] = False
    logger.info(
        "multisource_operational_fusion",
        extra={
            "operational_fusion_requested": audit["requested"],
            "operational_fusion_authorized": audit["authorized"],
            "holdout_gate_status": audit["holdout_gate_status"],
            "authorization_reason": audit["authorization_reason"],
            "policy_available": False,
            "original_recommendation": decision,
            "final_recommendation": decision,
            "official_recommendation_changed": False,
        },
    )
    return result
