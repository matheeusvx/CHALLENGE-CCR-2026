"""Contratos HTTP tipados da API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeometryRequest(StrictModel):
    geometry: dict[str, Any]


class Centroid(StrictModel):
    longitude: float
    latitude: float


class GeometryValidationResponse(StrictModel):
    valid: bool
    geometry_type: str
    area_square_meters: float
    centroid: Centroid
    bounding_box: list[float]
    estimated_sentinel_pixels: int
    warnings: list[str]


class DecisionParameters(StrictModel):
    decision_min_observations: int | None = Field(None, ge=2, le=100)
    high_vegetation_percentile: float | None = Field(None, ge=0, le=100)
    significant_drop_absolute: float | None = Field(None, gt=0, le=2)
    significant_drop_relative_percentage: float | None = Field(None, gt=0, le=100)
    trend_window: int | None = Field(None, ge=2, le=100)
    max_gap_days: int | None = Field(None, ge=1, le=366)
    recent_intervention_days: int | None = Field(None, ge=1, le=366)


class AnalysisRunRequest(StrictModel):
    geometry: dict[str, Any]
    start_date: date | None = None
    end_date: date | None = None
    max_cloud_cover: float | None = Field(None, ge=0, le=100)
    max_scenes: int | None = Field(None, ge=1, le=100)
    scene_order: Literal["newest", "oldest"] | None = None
    min_valid_pixel_percentage: float | None = Field(None, ge=0, le=100)
    min_observations: int | None = Field(None, ge=1, le=100)
    daily_aggregation: Literal["best", "median", "none"] | None = None
    decision: DecisionParameters | None = None


class HealthResponse(StrictModel):
    status: Literal["ok"]
    service: str
    version: str


class RecommendationResponse(StrictModel):
    decision: Literal["cortar", "nao_cortar", "inconclusivo"]
    confidence: Literal["high", "medium", "low"]
    experimental: bool
    summary: str
    reasons: list[str]
    blocking_reasons: list[str]
    limitations: list[str]
    metrics: dict[str, Any] = Field(default_factory=dict)


class HeightEstimationResponse(StrictModel):
    status: Literal["experimental", "unavailable", "disabled"]
    estimated_class: Literal["le_30_cm", "gt_30_cm", "inconclusive"] | None
    score_gt_30_cm: float | None = Field(None, ge=0, le=1)
    probability_gt_30_cm: float | None = Field(
        None,
        ge=0,
        le=1,
        deprecated=True,
        description="Deprecated alias of score_gt_30_cm; not calibrated probability.",
    )
    calibration_status: Literal["uncalibrated"] | None = None
    vegetation_fraction: float | None = Field(None, ge=0, le=1)
    height_valid_pixel_count: int | None = Field(None, ge=0)
    height_total_pixel_count: int | None = Field(None, ge=0)
    mixed_pixel_risk: Literal["low", "medium", "high"] | None = None
    confidence: Literal["low", "medium"] | None
    reference_threshold_cm: Literal[30] = 30
    model_version: str | None
    provenance: dict[str, Any] | None = None


class AnalysisPeriodResponse(StrictModel):
    start_date: date
    end_date: date
    timezone: str
    strategy: Literal["previous_calendar_month", "explicit"]


class SpatialZoneResponse(StrictModel):
    zone_id: str
    recommendation: Literal["cortar", "nao_cortar", "inconclusivo"]
    geometry: dict[str, Any]
    area_m2: float = Field(ge=0)
    confidence: Literal["high", "medium", "low"]
    analysis_quality: Literal["high", "medium", "low"]
    reasons: list[str]
    start_distance_m: float = Field(ge=0)
    end_distance_m: float = Field(ge=0)
    road_ref: str


class SpatialSegmentationResponse(StrictModel):
    status: Literal["available", "not_applicable", "unavailable"]
    experimental: bool
    section_length_m: int = Field(gt=0)
    effective_coverage_pct: float | None = Field(None, ge=0, le=100)
    zones: list[SpatialZoneResponse]


class DecisionSupportResponse(StrictModel):
    """Evidencia de apoio vinda do historico operacional.

    Campo aditivo: nunca altera `recommendation`, que continua sendo produzida
    exclusivamente pelo motor de satelite.
    """

    status: Literal[
        "available", "stale_field_data", "insufficient_history", "unavailable"
    ]
    experimental: bool = True
    model_version: str | None = None
    calibration_status: str | None = None
    suggestion: Literal["cortar", "nao_cortar", "inconclusivo"] | None = None
    score: float | None = None
    confidence: Literal["low", "medium"] | None = None
    agreement: Literal["concorda", "diverge", "indeterminado"] | None = None
    reference_km: int | None = None
    last_survey_on: date | None = None
    days_since_survey: int | None = None
    prediction_horizon_days: int
    factors: list[str] = []
    context: dict[str, Any] = {}
class EvidenceObservationResponse(StrictModel):
    observed_at: str
    metrics: dict[str, Any]


class Sentinel1EvidenceSummaryResponse(StrictModel):
    schema_version: Literal["1.0"]
    availability: Literal[
        "available", "no_coverage", "unavailable", "error", "disabled"
    ]
    quality_score: float | None = Field(None, ge=0, le=100)
    canonical_relative_orbit: int | None = Field(None, gt=0)
    observation_count: int = Field(ge=0)
    calibrated_observation_count: int = Field(ge=0)
    temporal_usable_observation_count: int = Field(ge=0)
    temporal_status: Literal[
        "increasing",
        "decreasing",
        "stable",
        "mixed",
        "insufficient_data",
        "disabled",
    ]
    vv_change_db: float | None = None
    vh_change_db: float | None = None
    processing_duration_ms: float = Field(ge=0)
    warnings: list[str]
    limitations: list[str]


class SourceEvidenceResponse(StrictModel):
    source: str
    status: Literal["available", "no_coverage", "unavailable", "error", "disabled"]
    quality: float | None = Field(None, ge=0, le=100)
    coverage: float | None = Field(None, ge=0, le=100)
    observed_at: str | None = None
    observations: list[EvidenceObservationResponse]
    metrics: dict[str, Any]
    provenance: dict[str, Any]
    warnings: list[str]
    summary: Sentinel1EvidenceSummaryResponse | None = None


class ShadowReviewResponse(StrictModel):
    schema_version: Literal["1.0"]
    review_mode: Literal["disabled", "shadow", "experimental", "operational"]
    review_evaluable: bool
    review_evaluated: bool
    review_recommended: bool
    review_rule: Literal["B"] | None
    review_reason: Literal[
        "sentinel1_temporal_mixed_with_sentinel2_cut",
        "rule_b_conditions_not_met",
    ] | None
    review_source: Literal["sentinel1"]
    sentinel1_temporal_status: Literal[
        "increasing",
        "decreasing",
        "stable",
        "mixed",
        "insufficient_data",
        "disabled",
    ] | None
    review_not_evaluable_reason: Literal[
        "fusion_mode_not_shadow",
        "official_recommendation_unavailable",
        "sentinel1_evidence_missing",
        "sentinel1_no_coverage",
        "sentinel1_unavailable",
        "sentinel1_error",
        "sentinel1_disabled",
        "sentinel1_calibration_unavailable",
        "sentinel1_temporal_disabled",
        "sentinel1_temporal_insufficient_data",
    ] | None
    official_recommendation_changed: Literal[False]


class OperationalFusionResponse(StrictModel):
    schema_version: Literal["1.0"]
    requested: bool
    authorized: bool
    policy_available: Literal[False]
    authorization_status: Literal["not_requested", "denied", "authorized"]
    authorization_reason: str
    holdout_schema_version: str | None = None
    holdout_gate_status: str | None = None
    candidate_rule: Literal["B"]
    policy_version: None = None
    original_recommendation: Literal["cortar", "nao_cortar", "inconclusivo"]
    final_recommendation: Literal["cortar", "nao_cortar", "inconclusivo"]
    official_recommendation_changed: Literal[False]


class ExperimentalFusionResponse(StrictModel):
    schema_version: Literal["1.0"]
    fusion_mode: Literal["disabled", "shadow", "experimental", "operational"]
    fusion_policy: Literal["experimental_v1"] | None
    experimental_policy_version: Literal["1.0"] | None
    sentinel2_recommendation: Literal["cortar", "nao_cortar", "inconclusivo"]
    multisource_recommendation: Literal["cortar", "nao_cortar", "inconclusivo"]
    sentinel1_influenced_decision: bool
    fusion_rule: Literal["B"] | None
    fusion_reason: Literal[
        "sentinel1_temporal_mixed_with_sentinel2_cut"
    ] | None
    sentinel1_temporal_status: Literal[
        "increasing", "decreasing", "stable", "mixed",
        "insufficient_data", "disabled",
    ] | None
    experimental: bool
    operationally_authorized: Literal[False]
    experimental_fusion_evaluated: bool
    experimental_fusion_evaluable: bool
    fusion_not_evaluable_reason: Literal[
        "fusion_mode_not_experimental",
        "sentinel2_recommendation_unavailable",
        "sentinel1_evidence_missing",
        "sentinel1_no_coverage",
        "sentinel1_unavailable",
        "sentinel1_error",
        "sentinel1_disabled",
        "sentinel1_calibration_unavailable",
        "sentinel1_temporal_disabled",
        "sentinel1_temporal_insufficient_data",
    ] | None


class MultisourceResponse(StrictModel):
    enabled: bool
    fusion_mode: Literal["disabled", "shadow", "experimental", "operational"]
    official_recommendation_changed: Literal[False]
    generated_at: str
    configuration: dict[str, Any]
    sources: list[SourceEvidenceResponse]
    review: ShadowReviewResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )
    operational_fusion: OperationalFusionResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )
    experimental_fusion: ExperimentalFusionResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )


class AnalysisResponse(StrictModel):
    analysis_id: str
    status: str
    recommendation: RecommendationResponse
    height_estimation: HeightEstimationResponse
    analysis_period: AnalysisPeriodResponse
    selected_area_m2: float | None = Field(None, gt=0)
    effective_analysis_area_m2: float | None = Field(None, ge=0)
    effective_analysis_pct: float | None = Field(None, ge=0)
    spatial_segmentation: SpatialSegmentationResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )
    decision_support: DecisionSupportResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )
    multisource: MultisourceResponse | None = Field(
        None, exclude_if=lambda value: value is None
    )
    aoi: dict[str, Any]
    summary: dict[str, Any]
    timeseries: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    artifacts: dict[str, str]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class AnalysisHistoryItem(StrictModel):
    """Resumo de uma analise gravada no historico."""

    analysis_id: str
    created_at: datetime
    status: str
    decision: str | None = None
    confidence: str | None = None
    summary: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    selected_area_m2: float | None = None
    analysis_quality_status: str | None = None
    observation_count: int | None = None
    nearest_km: int | None = None
    centroid: Centroid | None = None


class AnalysisHistoryPage(StrictModel):
    """Pagina de resultados do historico, mais recente primeiro."""

    total: int
    limit: int
    offset: int
    items: list[AnalysisHistoryItem]


class AnalysisHistoryDetail(StrictModel):
    """Analise completa recuperada do historico."""

    analysis_id: str
    created_at: datetime
    geometry: dict[str, Any] | None = None
    result: AnalysisResponse
