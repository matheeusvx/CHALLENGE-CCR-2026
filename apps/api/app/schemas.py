"""Contratos HTTP tipados da API."""

from __future__ import annotations

from datetime import date
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


class AnalysisPeriodResponse(StrictModel):
    start_date: date
    end_date: date
    timezone: str
    strategy: Literal["previous_calendar_month", "explicit"]


class AnalysisResponse(StrictModel):
    analysis_id: str
    status: str
    recommendation: RecommendationResponse
    analysis_period: AnalysisPeriodResponse
    aoi: dict[str, Any]
    summary: dict[str, Any]
    timeseries: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    artifacts: dict[str, str]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
