"""Regras provisorias de qualidade e inclusao na serie temporal."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCL_CONTAMINATION_CLASSES = {
    "nodata",
    "saturated_or_defective",
    "cloud_shadow",
    "cloud_medium_probability",
    "cloud_high_probability",
    "cirrus",
    "snow_or_ice",
}


@dataclass(frozen=True)
class QualityAssessment:
    quality_status: str
    quality_reasons: tuple[str, ...]
    accepted_for_timeseries: bool
    scene_quality_score: float
    local_valid_pixel_percentage: float
    local_invalid_pixel_percentage: float
    min_pixel_requirement_met: bool
    valid_pixel_percentage_requirement_met: bool
    aoi_coverage_requirement_met: bool
    scl_required_met: bool


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


def calculate_scene_quality_score(
    *,
    valid_pixel_percentage: float,
    valid_pixel_count: int,
    min_valid_pixel_count: int,
    aoi_coverage_percentage: float,
    has_scl: bool,
    scl_class_percentages: dict[str, float] | None,
    cloud_cover: float | None,
) -> float:
    """Pontua qualidade local com componentes explicaveis, de 0 a 100.

    Pesos: validade local 30, quantidade absoluta 15, cobertura da AOI 20,
    presenca de SCL 15, baixa contaminacao SCL 10 e nuvens globais 10.
    """
    valid_score = min(100.0, max(0.0, float(valid_pixel_percentage)))
    count_score = min(100.0, max(0.0, valid_pixel_count / min_valid_pixel_count * 100.0))
    coverage_score = min(100.0, max(0.0, float(aoi_coverage_percentage)))
    scl_score = 100.0 if has_scl else 0.0
    contamination = sum(
        max(0.0, float((scl_class_percentages or {}).get(name, 0.0)))
        for name in SCL_CONTAMINATION_CLASSES
    )
    contamination_score = max(0.0, 100.0 - min(100.0, contamination)) if has_scl else 0.0
    cloud_score = (
        max(0.0, 100.0 - min(100.0, float(cloud_cover)))
        if cloud_cover is not None
        else 0.0
    )
    score = (
        valid_score * 0.30
        + count_score * 0.15
        + coverage_score * 0.20
        + scl_score * 0.15
        + contamination_score * 0.10
        + cloud_score * 0.10
    )
    return round(min(100.0, max(0.0, score)), 2)


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
    valid_pixel_count: int = 1,
    min_valid_pixel_count: int = 1,
    aoi_coverage_percentage: float = 100.0,
    min_aoi_coverage_percentage: float = 0.0,
    scl_class_percentages: dict[str, float] | None = None,
) -> QualityAssessment:
    """Avalia motivos de qualidade e decide inclusao, sem interpretar tendencia."""
    reasons: list[str] = []
    if not has_valid_ndvi_pixels:
        reasons.append("no_valid_ndvi_pixels")
    if valid_pixel_percentage < min_valid_pixel_percentage:
        reasons.append("insufficient_valid_pixels")
    min_pixel_requirement_met = valid_pixel_count >= min_valid_pixel_count
    if not min_pixel_requirement_met:
        reasons.append("insufficient_valid_pixel_count")
    if not has_scl:
        reasons.append("missing_scl")
    excessive_cloud = cloud_cover is not None and cloud_cover > max_cloud_cover
    if excessive_cloud:
        reasons.append("excessive_global_cloud_cover")
    if partial_raster_coverage:
        reasons.append("partial_raster_coverage")
    aoi_coverage_requirement_met = (
        aoi_coverage_percentage >= min_aoi_coverage_percentage
    )
    if not aoi_coverage_requirement_met:
        reasons.append("insufficient_aoi_coverage")

    meets_pixel_threshold = valid_pixel_percentage >= min_valid_pixel_percentage
    score = calculate_scene_quality_score(
        valid_pixel_percentage=valid_pixel_percentage,
        valid_pixel_count=valid_pixel_count,
        min_valid_pixel_count=min_valid_pixel_count,
        aoi_coverage_percentage=aoi_coverage_percentage,
        has_scl=has_scl,
        scl_class_percentages=scl_class_percentages,
        cloud_cover=cloud_cover,
    )
    accepted = (
        has_valid_ndvi_pixels
        and not excessive_cloud
        and (meets_pixel_threshold or include_low_quality_scenes)
        and min_pixel_requirement_met
        and has_scl
        and aoi_coverage_requirement_met
        and not partial_raster_coverage
    )
    return QualityAssessment(
        quality_status=classify_quality(
            valid_pixel_percentage,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
        ),
        quality_reasons=tuple(reasons),
        accepted_for_timeseries=accepted,
        scene_quality_score=score,
        local_valid_pixel_percentage=valid_pixel_percentage,
        local_invalid_pixel_percentage=max(0.0, 100.0 - valid_pixel_percentage),
        min_pixel_requirement_met=min_pixel_requirement_met,
        valid_pixel_percentage_requirement_met=meets_pixel_threshold,
        aoi_coverage_requirement_met=aoi_coverage_requirement_met,
        scl_required_met=has_scl,
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


def select_quality_assessed_observations(
    records: list[dict[str, Any]],
    *,
    max_scenes: int,
    scene_order: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aplica o limite temporal somente a observacoes aprovadas localmente."""
    if max_scenes <= 0:
        raise ValueError("A quantidade maxima de cenas deve ser maior que zero.")
    if scene_order not in {"newest", "oldest"}:
        raise ValueError("A ordem das cenas deve ser 'newest' ou 'oldest'.")

    def observed_at(record: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(str(record["datetime"]).replace("Z", "+00:00"))

    accepted = [record for record in records if record.get("accepted_for_timeseries")]
    prioritized = sorted(
        accepted,
        key=lambda record: (observed_at(record), str(record.get("item_id", ""))),
        reverse=scene_order == "newest",
    )
    selected = prioritized[:max_scenes]
    discarded = prioritized[max_scenes:]
    chronological = lambda record: (observed_at(record), str(record.get("item_id", "")))
    return sorted(selected, key=chronological), sorted(discarded, key=chronological)


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
