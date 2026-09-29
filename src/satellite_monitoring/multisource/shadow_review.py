"""Conservative shadow-review evaluation over already collected evidence.

Only benchmark rule B exists here.  This module has no authority to change an
official recommendation and intentionally returns audit metadata only.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping


logger = logging.getLogger(__name__)

SHADOW_REVIEW_SCHEMA_VERSION = "1.0"
SHADOW_REVIEW_RULE = "B"
VALID_TEMPORAL_STATUSES = {"increasing", "decreasing", "stable", "mixed"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sentinel1_source(sources: Any) -> dict[str, Any] | None:
    if not isinstance(sources, (list, tuple)):
        return None
    for source in sources:
        candidate = _mapping(source)
        if candidate.get("source") == "sentinel-1":
            return candidate
    return None


def _not_evaluable(
    *,
    fusion_mode: str,
    reason: str,
    temporal_status: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SHADOW_REVIEW_SCHEMA_VERSION,
        "review_mode": fusion_mode,
        "review_evaluable": False,
        "review_evaluated": False,
        "review_recommended": False,
        "review_rule": None,
        "review_reason": None,
        "review_source": "sentinel1",
        "sentinel1_temporal_status": temporal_status,
        "review_not_evaluable_reason": reason,
        "official_recommendation_changed": False,
    }


def evaluate_shadow_review(
    *,
    fusion_mode: str,
    official_recommendation: Mapping[str, Any] | None,
    sources: Any,
) -> dict[str, Any]:
    """Evaluate rule B without mutating either input or producing a decision."""

    if fusion_mode != "shadow":
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="fusion_mode_not_shadow",
        )
        _log_review(result)
        return result

    recommendation = _mapping(official_recommendation)
    s2_decision = recommendation.get("recommendation", recommendation.get("decision"))
    if s2_decision not in {"cortar", "nao_cortar", "inconclusivo"}:
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="official_recommendation_unavailable",
        )
        _log_review(result)
        return result

    sentinel1 = _sentinel1_source(sources)
    if sentinel1 is None:
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="sentinel1_evidence_missing",
        )
        _log_review(result)
        return result
    availability = str(sentinel1.get("status") or "unavailable")
    if availability != "available":
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason=f"sentinel1_{availability}",
        )
        _log_review(result)
        return result

    metrics = _mapping(sentinel1.get("metrics"))
    calibrated_count = metrics.get("calibrated_observation_count")
    if isinstance(calibrated_count, (int, float)) and calibrated_count <= 0:
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="sentinel1_calibration_unavailable",
        )
        _log_review(result)
        return result

    temporal = _mapping(metrics.get("temporal_analysis"))
    temporal_status = temporal.get("combined_status")
    if temporal.get("enabled") is False or temporal_status == "disabled":
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="sentinel1_temporal_disabled",
            temporal_status=str(temporal_status) if temporal_status is not None else None,
        )
        _log_review(result)
        return result
    if temporal.get("status") != "completed" or temporal_status not in VALID_TEMPORAL_STATUSES:
        result = _not_evaluable(
            fusion_mode=fusion_mode,
            reason="sentinel1_temporal_insufficient_data",
            temporal_status=str(temporal_status) if temporal_status is not None else None,
        )
        _log_review(result)
        return result

    triggered = s2_decision == "cortar" and temporal_status == "mixed"
    result = {
        "schema_version": SHADOW_REVIEW_SCHEMA_VERSION,
        "review_mode": "shadow",
        "review_evaluable": True,
        "review_evaluated": True,
        "review_recommended": triggered,
        "review_rule": SHADOW_REVIEW_RULE if triggered else None,
        "review_reason": (
            "sentinel1_temporal_mixed_with_sentinel2_cut"
            if triggered
            else "rule_b_conditions_not_met"
        ),
        "review_source": "sentinel1",
        "sentinel1_temporal_status": temporal_status,
        "review_not_evaluable_reason": None,
        "official_recommendation_changed": False,
    }
    _log_review(result)
    return result


def attach_shadow_review(
    multisource: Mapping[str, Any] | None,
    official_recommendation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return an additive copy of the multisource envelope with review audit."""

    result = _mapping(multisource)
    result["official_recommendation_changed"] = False
    result["review"] = evaluate_shadow_review(
        fusion_mode=str(result.get("fusion_mode") or "disabled"),
        official_recommendation=official_recommendation,
        sources=result.get("sources"),
    )
    return result


def _log_review(result: Mapping[str, Any]) -> None:
    logger.info(
        "multisource_shadow_review",
        extra={
            "review_evaluated": result.get("review_evaluated"),
            "review_triggered": result.get("review_recommended"),
            "review_rule": result.get("review_rule"),
            "review_not_evaluable_reason": result.get(
                "review_not_evaluable_reason"
            ),
            "official_recommendation_changed": False,
        },
    )
