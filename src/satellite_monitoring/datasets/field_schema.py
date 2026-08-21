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


@dataclass(frozen=True)
class FieldObservation:
    sample_id: str
    road: str | None = None
    km: float | None = None
    side: str | None = None
    geometry: Mapping[str, Any] | None = None
    observed_at: date | datetime | None = None
    gps_accuracy_m: float | None = None
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

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FieldObservation":
        """Aceita registros historicos mesmo sem os novos campos opcionais."""
        sample_id = str(value.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required.")
        known = {name for name in cls.__dataclass_fields__ if name != "sample_id"}
        payload = {name: value[name] for name in known if name in value}
        if "photos" in payload and not isinstance(payload["photos"], tuple):
            payload["photos"] = tuple(payload["photos"] or ())
        return cls(sample_id=sample_id, **payload)
