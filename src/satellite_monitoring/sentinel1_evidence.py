"""Stable, additive Sentinel-1 evidence summary contract."""

from __future__ import annotations

from typing import Any, TypedDict

from .multisource.models import SourceEvidence


SENTINEL1_EVIDENCE_SCHEMA_VERSION = "1.0"


class Sentinel1EvidenceSummary(TypedDict):
    schema_version: str
    availability: str
    quality_score: float | None
    canonical_relative_orbit: int | None
    observation_count: int
    calibrated_observation_count: int
    temporal_usable_observation_count: int
    temporal_status: str
    vv_change_db: float | None
    vh_change_db: float | None
    processing_duration_ms: float
    warnings: list[str]
    limitations: list[str]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_sentinel1_evidence_summary(
    evidence: SourceEvidence,
) -> Sentinel1EvidenceSummary:
    """Build the versioned summary without removing detailed evidence metrics."""

    metrics = evidence.metrics
    temporal = _mapping(metrics.get("temporal_analysis"))
    vv = _mapping(temporal.get("vv"))
    vh = _mapping(temporal.get("vh"))
    warnings = sorted(set(evidence.warnings) | set(temporal.get("warnings") or []))
    limitations = [
        "sentinel1_shadow_only",
        "radiometric_change_only",
        "no_physical_attribution",
        "no_operational_decision",
        "terrain_correction_not_applied",
    ]
    if evidence.status.value != "available":
        limitations.append("source_unavailable")
    if metrics.get("radiometric_calibration_status") != "calibrated":
        limitations.append("calibration_not_complete")
    if temporal.get("combined_status") in {"insufficient_data", "disabled", None}:
        limitations.append("temporal_support_not_available")
    return {
        "schema_version": SENTINEL1_EVIDENCE_SCHEMA_VERSION,
        "availability": evidence.status.value,
        "quality_score": evidence.quality,
        "canonical_relative_orbit": metrics.get("canonical_relative_orbit"),
        "observation_count": int(metrics.get("observation_count", 0) or 0),
        "calibrated_observation_count": int(
            metrics.get("calibrated_observation_count", 0) or 0
        ),
        "temporal_usable_observation_count": int(
            metrics.get("temporal_usable_observation_count", 0) or 0
        ),
        "temporal_status": str(
            temporal.get("combined_status") or "insufficient_data"
        ),
        "vv_change_db": vv.get("modeled_change_db"),
        "vh_change_db": vh.get("modeled_change_db"),
        "processing_duration_ms": float(
            metrics.get("processing_duration_ms", 0.0) or 0.0
        ),
        "warnings": warnings,
        "limitations": limitations,
    }
