"""Versioned experimental fusion policy over Sentinel-2 and Sentinel-1.

The policy changes only the additive multisource recommendation. It has no
operational authority and never mutates the official Sentinel-2 recommendation.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping


logger = logging.getLogger(__name__)

EXPERIMENTAL_FUSION_SCHEMA_VERSION = "1.0"
EXPERIMENTAL_FUSION_POLICY = "experimental_v1"
EXPERIMENTAL_FUSION_POLICY_VERSION = "1.0"
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
    """Conservative rule-B policy for research and demonstration only."""

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
            "multisource_recommendation": s2_decision,
            "sentinel1_influenced_decision": False,
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
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_evidence_missing",
            )
        availability = str(sentinel1.get("status") or "unavailable")
        if availability != "available":
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason=f"sentinel1_{availability}",
            )

        metrics = _mapping(sentinel1.get("metrics"))
        calibrated_count = metrics.get("calibrated_observation_count")
        if isinstance(calibrated_count, (int, float)) and calibrated_count <= 0:
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
            return self._finish(
                base,
                evaluated=True,
                evaluable=False,
                not_evaluable_reason="sentinel1_temporal_insufficient_data",
            )

        triggered = s2_decision == "cortar" and temporal_status == "mixed"
        if triggered:
            base.update(
                {
                    "multisource_recommendation": "inconclusivo",
                    "sentinel1_influenced_decision": True,
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
                    "sentinel1_influenced_decision"
                ],
                "experimental_fusion_rule": result["fusion_rule"],
                "sentinel2_recommendation": result["sentinel2_recommendation"],
                "multisource_recommendation": result[
                    "multisource_recommendation"
                ],
                "sentinel1_influenced_decision": result[
                    "sentinel1_influenced_decision"
                ],
            },
        )
        return result


def attach_experimental_fusion(
    multisource: Mapping[str, Any],
    official_recommendation: Mapping[str, Any] | None,
    *,
    policy: ExperimentalFusionPolicyV1 | None = None,
) -> dict[str, Any]:
    """Attach an experimental decision without changing Sentinel-2 output."""
    result = dict(multisource)
    evaluator = policy or ExperimentalFusionPolicyV1()
    result["experimental_fusion"] = evaluator.evaluate(
        fusion_mode=str(result.get("fusion_mode") or "disabled"),
        official_recommendation=official_recommendation,
        sources=result.get("sources"),
    )
    result["official_recommendation_changed"] = False
    return result

