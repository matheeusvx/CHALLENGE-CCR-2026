"""Regras provisorias de qualidade e inclusao na serie temporal."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualityAssessment:
    quality_status: str
    quality_reasons: tuple[str, ...]
    accepted_for_timeseries: bool


def classify_quality(
    valid_pixel_percentage: float,
    medium_threshold: float = 70.0,
    high_threshold: float = 85.0,
) -> str:
    """Classifica a qualidade somente pelo percentual de pixels validos."""
    if valid_pixel_percentage >= high_threshold:
        return "high"
    if valid_pixel_percentage >= medium_threshold:
        return "medium"
    return "low"


def assess_scene_quality(
    *,
    valid_pixel_percentage: float,
    min_valid_pixel_percentage: float,
    medium_threshold: float,
    high_threshold: float,
    has_scl: bool,
    cloud_cover: float | None,
    max_cloud_cover: float,
    partial_raster_coverage: bool,
    has_valid_ndvi_pixels: bool,
    include_low_quality_scenes: bool,
) -> QualityAssessment:
    """Avalia motivos de qualidade e decide inclusao, sem interpretar tendencia."""
    reasons: list[str] = []
    if not has_valid_ndvi_pixels:
        reasons.append("no_valid_ndvi_pixels")
    if valid_pixel_percentage < min_valid_pixel_percentage:
        reasons.append("insufficient_valid_pixels")
    if not has_scl:
        reasons.append("missing_scl")
    excessive_cloud = cloud_cover is not None and cloud_cover > max_cloud_cover
    if excessive_cloud:
        reasons.append("excessive_global_cloud_cover")
    if partial_raster_coverage:
        reasons.append("partial_raster_coverage")

    meets_pixel_threshold = valid_pixel_percentage >= min_valid_pixel_percentage
    accepted = (
        has_valid_ndvi_pixels
        and not excessive_cloud
        and (meets_pixel_threshold or include_low_quality_scenes)
    )
    return QualityAssessment(
        quality_status=classify_quality(
            valid_pixel_percentage,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
        ),
        quality_reasons=tuple(reasons),
        accepted_for_timeseries=accepted,
    )


def summarize_scene_quality(scene_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume cenas aceitas/rejeitadas e agrega os motivos de rejeicao."""
    accepted = [record for record in scene_records if record.get("accepted_for_timeseries")]
    rejected = [record for record in scene_records if not record.get("accepted_for_timeseries")]
    rejection_counts: Counter[str] = Counter()
    rejected_scenes: list[dict[str, Any]] = []

    for record in rejected:
        reasons = list(record.get("quality_reasons") or [])
        if record.get("processing_status") == "failed" and "processing_error" not in reasons:
            reasons.append("processing_error")
        rejection_counts.update(reasons)
        rejected_scenes.append(
            {
                "item_id": record.get("item_id"),
                "datetime": record.get("datetime"),
                "quality_status": record.get("quality_status"),
                "quality_reasons": reasons,
                "processing_status": record.get("processing_status"),
                "error": record.get("error"),
            }
        )

    return {
        "accepted_scene_count": len(accepted),
        "rejected_scene_count": len(rejected),
        "rejected_scenes": rejected_scenes,
        "rejection_reasons": dict(sorted(rejection_counts.items())),
    }


def determine_overall_status(
    *,
    fatal_error: str | None,
    processed_scene_count: int,
    accepted_scene_count: int,
    min_observations: int,
) -> str:
    """Determina o estado geral sem produzir uma interpretacao de tendencia."""
    if fatal_error is not None or processed_scene_count == 0:
        return "failed"
    if accepted_scene_count < min_observations:
        return "insufficient_observations"
    return "success"
