"""Offline candidate-rule simulation; never emits an operational decision."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping


BINARY_TRUTHS = {"cut", "no_cut"}
BINARY_S2_DECISIONS = {"cortar", "nao_cortar"}
TEMPORAL_STATUSES = {"increasing", "decreasing", "stable", "mixed"}


RulePredicate = Callable[[Mapping[str, Any]], bool]


def _s1_status(row: Mapping[str, Any]) -> Any:
    return row.get("s1_combined_status", row.get("combined_status"))


RULES: dict[str, dict[str, Any]] = {
    "A": {
        "description": "S2=nao_cortar and S1=increasing",
        "signal": "review_signal",
        "predicate": lambda row: row.get("s2_decision") == "nao_cortar"
        and _s1_status(row) == "increasing",
    },
    "B": {
        "description": "S2=cortar and S1=mixed",
        "signal": "review_signal",
        "predicate": lambda row: row.get("s2_decision") == "cortar"
        and _s1_status(row) == "mixed",
    },
    "C": {
        "description": "S2=cortar and S1=stable",
        "signal": "review_signal",
        "predicate": lambda row: row.get("s2_decision") == "cortar"
        and _s1_status(row) == "stable",
    },
    "D": {
        "description": "S2=inconclusivo and S1 temporal status available",
        "signal": "complementary_evidence",
        "predicate": lambda row: row.get("s2_decision") == "inconclusivo"
        and _s1_status(row) in TEMPORAL_STATUSES,
    },
}

COMBINATIONS = {
    "A": ("A",),
    "B": ("B",),
    "A+B": ("A", "B"),
    "A+B+C": ("A", "B", "C"),
}


def _s2_correct(row: Mapping[str, Any]) -> bool | None:
    truth = row.get("maintenance_truth")
    decision = row.get("s2_decision")
    if truth not in BINARY_TRUTHS or decision not in BINARY_S2_DECISIONS:
        return None
    return decision == ("cortar" if truth == "cut" else "nao_cortar")


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _triggered(row: Mapping[str, Any], rules: Iterable[str]) -> bool:
    return any(RULES[rule]["predicate"](row) for rule in rules)


def _review_metrics(
    comparable: list[dict[str, Any]], rules: tuple[str, ...]
) -> dict[str, Any]:
    triggered = [row for row in comparable if _triggered(row, rules)]
    errors = [row for row in comparable if row["s2_was_correct"] is False]
    correct = [row for row in comparable if row["s2_was_correct"] is True]
    errors_triggered = sum(row["s2_was_correct"] is False for row in triggered)
    correct_triggered = sum(row["s2_was_correct"] is True for row in triggered)
    return {
        "eligible_samples": len(comparable),
        "triggered_samples": len(triggered),
        "trigger_rate": _rate(len(triggered), len(comparable)),
        "s2_errors_triggered": errors_triggered,
        "s2_correct_cases_triggered": correct_triggered,
        "error_capture_rate": _rate(errors_triggered, len(errors)),
        "false_review_rate": _rate(correct_triggered, len(correct)),
        "precision_for_detecting_s2_error": _rate(errors_triggered, len(triggered)),
    }


def build_validation_fusion_benchmark(
    temporal_benchmark: Mapping[str, Any],
) -> dict[str, Any]:
    source_samples = list(temporal_benchmark.get("samples") or [])
    matrix: list[dict[str, Any]] = []
    for source in source_samples:
        s2_was_correct = _s2_correct(source)
        truth = source.get("maintenance_truth")
        triggered_rules = (
            [
                name
                for name, definition in RULES.items()
                if definition["predicate"](source)
            ]
            if truth in BINARY_TRUTHS
            else []
        )
        if truth == "uncertain":
            partition = "uncertain_ground_truth"
        elif source.get("s2_decision") == "inconclusivo":
            partition = "s2_inconclusive"
        elif s2_was_correct is not None:
            partition = "binary_evaluation"
        else:
            partition = "non_comparable_s2_decision"
        matrix.append(
            {
                "sample_id": source.get("sample_id"),
                "maintenance_truth": truth,
                "s2_decision": source.get("s2_decision"),
                "s1_combined_status": source.get("combined_status"),
                "vv_status": source.get("vv_temporal_status"),
                "vh_status": source.get("vh_temporal_status"),
                "canonical_relative_orbit": source.get(
                    "canonical_relative_orbit"
                ),
                "rule_triggered": triggered_rules,
                "s2_was_correct": s2_was_correct,
                "evaluation_partition": partition,
            }
        )

    comparable = [
        row
        for row in matrix
        if row["evaluation_partition"] == "binary_evaluation"
        and row["s1_combined_status"] in TEMPORAL_STATUSES
    ]
    candidate_rules = {
        name: {
            "description": definition["description"],
            "signal": definition["signal"],
            **(
                _review_metrics(comparable, (name,))
                if name != "D"
                else _complementary_metrics(matrix)
            ),
        }
        for name, definition in RULES.items()
    }
    combinations = {
        name: {"rules": list(rules), **_review_metrics(comparable, rules)}
        for name, rules in COMBINATIONS.items()
    }
    return {
        "experimental": True,
        "candidate_rules": candidate_rules,
        "combinations": combinations,
        "sample_matrix": matrix,
        "partitions": {
            "binary_evaluation_samples": len(comparable),
            "uncertain_ground_truth_samples": sum(
                row["evaluation_partition"] == "uncertain_ground_truth"
                for row in matrix
            ),
            "s2_inconclusive_samples": sum(
                row["evaluation_partition"] == "s2_inconclusive" for row in matrix
            ),
            "non_comparable_s2_decision_samples": sum(
                row["evaluation_partition"] == "non_comparable_s2_decision"
                for row in matrix
            ),
        },
        "methodology": {
            "purpose": "Offline shadow simulation of conservative review hypotheses",
            "eligible_definition": (
                "ground truth cut/no_cut, binary S2 decision, and valid S1 temporal status"
            ),
            "error_capture_rate": "triggered S2 errors / all eligible S2 errors",
            "false_review_rate": (
                "triggered S2-correct samples / all eligible S2-correct samples"
            ),
            "precision_for_detecting_s2_error": (
                "triggered S2 errors / all triggered samples"
            ),
            "threshold_optimization_performed": False,
            "runtime_fusion_applied": False,
            "recommendation_changed": False,
        },
    }


def _complementary_metrics(matrix: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        row
        for row in matrix
        if row["maintenance_truth"] in BINARY_TRUTHS
        and row["s2_decision"] == "inconclusivo"
    ]
    triggered = [row for row in eligible if "D" in row["rule_triggered"]]
    return {
        "eligible_samples": len(eligible),
        "triggered_samples": len(triggered),
        "trigger_rate": _rate(len(triggered), len(eligible)),
        "s2_errors_triggered": 0,
        "s2_correct_cases_triggered": 0,
        "error_capture_rate": None,
        "false_review_rate": None,
        "precision_for_detecting_s2_error": None,
    }
