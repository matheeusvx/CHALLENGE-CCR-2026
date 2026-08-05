"""Contratos HTTP tipados da API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.satellite_monitoring.config import (
    DEFAULT_DAILY_AGGREGATION,
    DEFAULT_DECISION_MIN_OBSERVATIONS,
    DEFAULT_HIGH_VEGETATION_PERCENTILE,
    DEFAULT_MAX_GAP_DAYS,
    DEFAULT_MAX_SCENES,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_MIN_VALID_PIXEL_PERCENTAGE,
    DEFAULT_RECENT_INTERVENTION_DAYS,
    DEFAULT_SCENE_ORDER,
    DEFAULT_SIGNIFICANT_DROP_ABSOLUTE,
    DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE,
    DEFAULT_TREND_WINDOW,
)


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
    decision_min_observations: int = Field(DEFAULT_DECISION_MIN_OBSERVATIONS, ge=2, le=100)
    high_vegetation_percentile: float = Field(DEFAULT_HIGH_VEGETATION_PERCENTILE, ge=0, le=100)
    significant_drop_absolute: float = Field(DEFAULT_SIGNIFICANT_DROP_ABSOLUTE, gt=0, le=2)
    significant_drop_relative_percentage: float = Field(DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE, gt=0, le=100)
    trend_window: int = Field(DEFAULT_TREND_WINDOW, ge=2, le=100)
    max_gap_days: int = Field(DEFAULT_MAX_GAP_DAYS, ge=1, le=366)
    recent_intervention_days: int = Field(DEFAULT_RECENT_INTERVENTION_DAYS, ge=1, le=366)


class AnalysisRunRequest(StrictModel):
    geometry: dict[str, Any]
    start_date: date
    end_date: date
    max_cloud_cover: float = Field(30.0, ge=0, le=100)
    max_scenes: int = Field(DEFAULT_MAX_SCENES, ge=1, le=100)
    scene_order: Literal["newest", "oldest"] = DEFAULT_SCENE_ORDER
    min_valid_pixel_percentage: float = Field(DEFAULT_MIN_VALID_PIXEL_PERCENTAGE, ge=0, le=100)
    min_observations: int = Field(DEFAULT_MIN_OBSERVATIONS, ge=1, le=100)
    daily_aggregation: Literal["best", "median", "none"] = DEFAULT_DAILY_AGGREGATION
    decision: DecisionParameters = Field(default_factory=DecisionParameters)


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


class AnalysisResponse(StrictModel):
    analysis_id: str
    status: str
    recommendation: RecommendationResponse
    aoi: dict[str, Any]
    summary: dict[str, Any]
    timeseries: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    artifacts: dict[str, str]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
