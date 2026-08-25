"""Schema minimo e retrocompativel para futuras coletas de ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

FUTURE_PRIMARY_TARGET = "height_p90_cm > 30"
FUTURE_UNCERTAINTY_ZONE = {
    "clear_negative": "height_p90_cm <= 25",
    "uncertainty_zone": "25 < height_p90_cm < 35",
    "clear_positive": "height_p90_cm >= 35",
}

MEASUREMENT_QUALITY_VALUES = (
    "confirmed_single_measurement",
    "confirmed_multiple_measurements",
    "visual_only",
    "approximate",
    "unknown",
)


@dataclass(frozen=True)
class FieldObservation:
    sample_id: str
    road: str | None = None
    km: float | None = None
    side: str | None = None
    location_name: str | None = None
    geometry: Mapping[str, Any] | None = None
    observed_at: date | datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_accuracy_m: float | None = None
    measured_height_cm: float | None = None
    measurements_cm: tuple[float, ...] = field(default_factory=tuple)
    height_p50_cm: float | None = None
    height_p90_cm: float | None = None
    height_max_cm: float | None = None
    vegetation_cover_pct: float | None = None
    vegetation_type: str | None = None
    recent_cut: bool | None = None
    cut_date: date | None = None
    soil_condition: str | None = None
    moisture_condition: str | None = None
    shadow_condition: str | None = None
    photos: tuple[str, ...] = field(default_factory=tuple)
    video_reference: str | None = None
    measurement_quality: str = "unknown"
    source: str | None = None
    qualitative_condition: str | None = None
    recommendation_context: str | None = None
    real_class: str | None = None
    boundary_case: bool = False
    training_eligible: bool = False
    external_validation: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FieldObservation":
        """Aceita registros historicos mesmo sem os novos campos opcionais."""
        sample_id = str(value.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required.")
        known = {name for name in cls.__dataclass_fields__ if name != "sample_id"}
        payload = {name: value[name] for name in known if name in value}
        for name in ("photos", "measurements_cm"):
            if name in payload and not isinstance(payload[name], tuple):
                payload[name] = tuple(payload[name] or ())
        quality = str(payload.get("measurement_quality") or "unknown")
        if quality not in MEASUREMENT_QUALITY_VALUES:
            raise ValueError(
                "measurement_quality must be one of: "
                + ", ".join(MEASUREMENT_QUALITY_VALUES)
            )
        payload["measurement_quality"] = quality
        return cls(sample_id=sample_id, **payload)
