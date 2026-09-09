from __future__ import annotations

from copy import deepcopy
import logging

import pytest

from src.satellite_monitoring.multisource.shadow_review import (
    SHADOW_REVIEW_RULE,
    attach_shadow_review,
    evaluate_shadow_review,
)


def _source(
    temporal_status: str = "mixed",
    *,
    availability: str = "available",
    temporal_analysis_status: str = "completed",
    temporal_enabled: bool = True,
    calibrated_observation_count: int = 4,
) -> dict:
    return {
        "source": "sentinel-1",
        "status": availability,
        "metrics": {
            "calibrated_observation_count": calibrated_observation_count,
            "temporal_analysis": {
                "enabled": temporal_enabled,
                "status": temporal_analysis_status,
                "combined_status": temporal_status,
            },
        },
    }


def _evaluate(decision: str, temporal_status: str = "mixed", **source_options):
    return evaluate_shadow_review(
        fusion_mode="shadow",
        official_recommendation={"recommendation": decision},
        sources=[_source(temporal_status, **source_options)],
    )


def test_rule_b_triggers_for_s2_cut_and_s1_mixed_without_mutation() -> None:
    recommendation = {"recommendation": "cortar", "confidence": "high"}
    sources = [_source("mixed")]
    original_recommendation = deepcopy(recommendation)
    original_sources = deepcopy(sources)

    result = evaluate_shadow_review(
        fusion_mode="shadow",
        official_recommendation=recommendation,
        sources=sources,
    )

    assert result == {
        "schema_version": "1.0",
        "review_mode": "shadow",
        "review_evaluable": True,
        "review_evaluated": True,
        "review_recommended": True,
        "review_rule": "B",
        "review_reason": "sentinel1_temporal_mixed_with_sentinel2_cut",
        "review_source": "sentinel1",
        "sentinel1_temporal_status": "mixed",
        "review_not_evaluable_reason": None,
        "official_recommendation_changed": False,
    }
    assert recommendation == original_recommendation
    assert sources == original_sources


@pytest.mark.parametrize("temporal_status", ["increasing", "stable", "decreasing"])
def test_rule_a_and_rule_c_statuses_do_not_trigger(temporal_status: str) -> None:
    result = _evaluate("cortar", temporal_status)
    assert result["review_evaluable"] is True
    assert result["review_recommended"] is False
    assert result["review_rule"] is None


def test_s2_no_cut_with_mixed_s1_does_not_trigger() -> None:
    result = _evaluate("nao_cortar", "mixed")
    assert result["review_evaluable"] is True
    assert result["review_recommended"] is False


@pytest.mark.parametrize(
    ("source_options", "reason"),
    [
        ({"availability": "unavailable"}, "sentinel1_unavailable"),
        ({"availability": "error"}, "sentinel1_error"),
        (
            {"temporal_status": "insufficient_data", "temporal_analysis_status": "insufficient_data"},
            "sentinel1_temporal_insufficient_data",
        ),
        ({"calibrated_observation_count": 0}, "sentinel1_calibration_unavailable"),
    ],
)
def test_s1_failure_modes_are_not_evaluable_and_never_trigger(
    source_options: dict, reason: str
) -> None:
    temporal_status = source_options.pop("temporal_status", "mixed")
    result = _evaluate("cortar", temporal_status, **source_options)
    assert result["review_evaluable"] is False
    assert result["review_evaluated"] is False
    assert result["review_recommended"] is False
    assert result["review_not_evaluable_reason"] == reason
    assert result["official_recommendation_changed"] is False


def test_disabled_feature_preserves_non_operational_behavior() -> None:
    result = evaluate_shadow_review(
        fusion_mode="disabled",
        official_recommendation={"recommendation": "cortar"},
        sources=[_source("mixed")],
    )
    assert result["review_evaluated"] is False
    assert result["review_recommended"] is False
    assert result["review_not_evaluable_reason"] == "fusion_mode_not_shadow"


def test_attachment_is_additive_and_runtime_contains_only_rule_b() -> None:
    multisource = {"fusion_mode": "shadow", "sources": [_source("mixed")]}
    original = deepcopy(multisource)
    result = attach_shadow_review(multisource, {"recommendation": "cortar"})
    assert multisource == original
    assert result["review"]["review_rule"] == SHADOW_REVIEW_RULE == "B"
    assert "A+B" not in repr(result)
    assert result["official_recommendation_changed"] is False


def test_review_emits_structured_observability(caplog) -> None:
    with caplog.at_level(
        logging.INFO,
        logger="src.satellite_monitoring.multisource.shadow_review",
    ):
        _evaluate("cortar", "mixed")
    record = caplog.records[-1]
    assert record.message == "multisource_shadow_review"
    assert record.review_evaluated is True
    assert record.review_triggered is True
    assert record.review_rule == "B"
    assert record.review_not_evaluable_reason is None
    assert record.official_recommendation_changed is False
