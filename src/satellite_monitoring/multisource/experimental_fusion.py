"""Sentinel-2-primary multisource validation policy.

Sentinel-1 can add an auditable disagreement/review signal, but it cannot
replace an objective Sentinel-2 decision or make it inconclusive.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping


logger = logging.getLogger(__name__)

EXPERIMENTAL_FUSION_SCHEMA_VERSION = "1.0"
EXPERIMENTAL_FUSION_POLICY = "experimental_v1"
EXPERIMENTAL_FUSION_POLICY_VERSION = "1.1"
EXPERIMENTAL_FUSION_RULE = "B"
VALID_RECOMMENDATIONS = {"cortar", "nao_cortar", "inconclusivo"}
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


class ExperimentalFusionPolicyV1:
    """S2-primary policy with rule B retained only as an internal review signal."""

    name = EXPERIMENTAL_FUSION_POLICY
    version = EXPERIMENTAL_FUSION_POLICY_VERSION

    def evaluate(
        self,
        *,
        fusion_mode: str,
        official_recommendation: Mapping[str, Any] | None,
        sources: Any,
    ) -> dict[str, Any]:
        recommendation = _mapping(official_recommendation)
        s2_decision = recommendation.get(
            "recommendation", recommendation.get("decision")
        )
        base = {
            "schema_version": EXPERIMENTAL_FUSION_SCHEMA_VERSION,
            "fusion_mode": fusion_mode,
            "fusion_policy": self.name if fusion_mode == "experimental" else None,
            "experimental_policy_version": (
                self.version if fusion_mode == "experimental" else None
            ),
            "sentinel2_recommendation": s2_decision,
            "final_recommendation": s2_decision,
            "multisource_recommendation": s2_decision,
            "sentinel1_influenced_decision": False,
            "sentinel1_validation_status": "not_evaluated",
            "multisource_disagreement": False,
            "review_recommended": False,
            "review_rule": None,
            "review_reason": None,
            "fusion_rule": None,
            "fusion_reason": None,
            "sentinel1_temporal_status": None,
            "experimental": fusion_mode == "experimental",
            "operationally_authorized": False,
        }
        if fusion_mode != "experimental":
            return self._finish(
                base,
                evaluated=False,
                evaluable=False,
                not_evaluable_reason="fusion_mode_not_experimental",
            )
        if s2_decision not in VALID_RECOMMENDATIONS:
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel2_recommendation_unavailable",
            )

        sentinel1 = _sentinel1_source(sources)
        if sentinel1 is None:
            base["sentinel1_validation_status"] = "missing"
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_evidence_missing",
            )
        availability = str(sentinel1.get("status") or "unavailable")
        if availability != "available":
            base["sentinel1_validation_status"] = (
                availability
                if availability in {"no_coverage", "unavailable", "error", "disabled"}
                else "unavailable"
            )
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason=f"sentinel1_{availability}",
            )

        metrics = _mapping(sentinel1.get("metrics"))
        calibrated_count = metrics.get("calibrated_observation_count")
        if isinstance(calibrated_count, (int, float)) and calibrated_count <= 0:
            base["sentinel1_validation_status"] = "calibration_unavailable"
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_calibration_unavailable",
            )
        temporal = _mapping(metrics.get("temporal_analysis"))
        temporal_status = temporal.get("combined_status")
        base["sentinel1_temporal_status"] = temporal_status
        if temporal.get("enabled") is False or temporal_status == "disabled":
            base["sentinel1_validation_status"] = "temporal_disabled"
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_temporal_disabled",
            )
        if (
            temporal.get("status") != "completed"
            or temporal_status not in VALID_TEMPORAL_STATUSES
        ):
            base["sentinel1_validation_status"] = "temporal_insufficient_data"
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_temporal_insufficient_data",
            )

        base["sentinel1_validation_status"] = "valid"
        review_triggered = s2_decision == "cortar" and temporal_status == "mixed"
        if review_triggered:
            base.update(
                {
                    "multisource_disagreement": True,
                    "review_recommended": True,
                    "review_rule": EXPERIMENTAL_FUSION_RULE,
                    "review_reason": (
                        "sentinel1_temporal_mixed_with_sentinel2_cut"
                    ),
                    "fusion_rule": EXPERIMENTAL_FUSION_RULE,
                    "fusion_reason": (
                        "sentinel1_temporal_mixed_with_sentinel2_cut"
                    ),
                }
            )
        return self._finish(
            base,
            evaluated=True,
            evaluable=True,
            not_evaluable_reason=None,
        )

    @staticmethod
    def _finish(
        result: dict[str, Any],
        *,
        evaluated: bool,
        evaluable: bool,
        not_evaluable_reason: str | None,
    ) -> dict[str, Any]:
        # Product invariant: Sentinel-2 is the primary and final decision source.
        # S1 may enrich validation/review audit only.
        sentinel2_recommendation = result.get("sentinel2_recommendation")
        result["final_recommendation"] = sentinel2_recommendation
        result["multisource_recommendation"] = sentinel2_recommendation
        result["sentinel1_influenced_decision"] = False
        result.update(
            {
                "experimental_fusion_evaluated": evaluated,
                "experimental_fusion_evaluable": evaluable,
                "fusion_not_evaluable_reason": not_evaluable_reason,
            }
        )
        logger.info(
            "multisource_experimental_fusion",
            extra={
                "experimental_fusion_evaluated": evaluated,
                "experimental_fusion_triggered": result[
                    "review_recommended"
                ],
                "experimental_fusion_rule": result["fusion_rule"],
                "sentinel2_recommendation": result["sentinel2_recommendation"],
                "multisource_recommendation": result[
                    "multisource_recommendation"
                ],
                "sentinel1_influenced_decision": result[
                    "sentinel1_influenced_decision"
                ],
                "sentinel1_validation_status": result[
                    "sentinel1_validation_status"
                ],
                "multisource_disagreement": result[
                    "multisource_disagreement"
                ],
                "review_recommended": result["review_recommended"],
            },
        )
        return result


def attach_experimental_fusion(
    multisource: Mapping[str, Any],
    official_recommendation: Mapping[str, Any] | None,
    *,
    policy: ExperimentalFusionPolicyV1 | None = None,
) -> dict[str, Any]:
    """Attach S1 validation audit while preserving Sentinel-2 as final decision."""
    result = dict(multisource)
    evaluator = policy or ExperimentalFusionPolicyV1()
    result["experimental_fusion"] = evaluator.evaluate(
        fusion_mode=str(result.get("fusion_mode") or "disabled"),
        official_recommendation=official_recommendation,
        sources=result.get("sources"),
    )
    result["official_recommendation_changed"] = False
    return result
