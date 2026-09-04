"""Public validation contracts and controlled ground-truth vocabularies."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VegetationClass(StrEnum):
    LOW_GRASS = "low_grass"
    TALL_DENSE_GRASS = "tall_dense_grass"
    SHRUB = "shrub"
    TREE = "tree"
    MIXED = "mixed"


class MaintenanceTruth(StrEnum):
    CUT = "cut"
    NO_CUT = "no_cut"
    UNCERTAIN = "uncertain"


class ValidationSource(StrEnum):
    VISUAL_INSPECTION = "visual_inspection"
    AERIAL_IMAGERY = "aerial_imagery"
    FIELD_INSPECTION = "field_inspection"
    MAINTENANCE_RECORD = "maintenance_record"
    OTHER = "other"


class ValidationSampleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    analysis_id: UUID
    vegetation_class: VegetationClass
    maintenance_truth: MaintenanceTruth
    validation_source: ValidationSource
    reference_date: date
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("notes")
    @classmethod
    def validate_plain_notes(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("notes must be plain text without NUL characters")
        return value


class ValidationSampleRow(BaseModel):
    sample_id: UUID
    analysis_id: UUID
    schema_version: int
    created_at: datetime
    vegetation_class: VegetationClass
    maintenance_truth: MaintenanceTruth
    validation_source: ValidationSource
    reference_date: date
    notes: str | None = None
    selected_area_m2: float | None = None
    s2_decision: str | None = None
    s2_confidence: str | None = None
    s2_ndvi_mean: float | None = None
    s2_ndvi_median: float | None = None
    s2_current_percentile: float | None = None
    s1_status: str | None = None
    s1_quality: float | None = None
    s1_coverage: float | None = None
    s1_canonical_relative_orbit: int | None = None
    s1_canonical_observation_count: int | None = None
    s1_vv_sigma0_linear: float | None = None
    s1_vh_sigma0_linear: float | None = None
    s1_vv_sigma0_db: float | None = None
    s1_vh_sigma0_db: float | None = None
    s1_vh_minus_vv_db: float | None = None
    s1_vh_vv_sigma0_ratio: float | None = None


class ValidationSampleDetail(ValidationSampleRow):
    snapshot: dict[str, Any]


class ValidationSampleList(BaseModel):
    items: list[ValidationSampleRow]
    total: int
    limit: int
    offset: int


class ValidationSummary(BaseModel):
    total_samples: int
    target_total: int
    remaining_total: int
    target_per_class: int
    counts_by_vegetation_class: dict[str, int]
    by_vegetation_class: dict[str, Any]
    counts_by_maintenance_truth: dict[str, int]
