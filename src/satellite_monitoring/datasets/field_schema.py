"""Schema retrocompativel para campanhas de ground truth metrico em campo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

FUTURE_PRIMARY_TARGET = "height_p90_cm > 30"
FUTURE_PRIMARY_TARGET_FIELD = "future_primary_target_gt30"
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

MEASUREMENT_TYPE_VALUES = (
    "exact_single",
    "multiple",
    "threshold_lower_bound",
    "visual_only",
)


@dataclass(frozen=True)
class FieldObservation:
    sample_id: str
    campaign_id: str | None = None
    road: str | None = None
    km: float | None = None
    side: str | None = None
    location_name: str | None = None
    geometry: Mapping[str, Any] | None = None
    observed_at: date | datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_accuracy_m: float | None = None
    measurement_type: str = "visual_only"
    measured_height_cm: float | None = None
    height_lower_bound_cm: float | None = None
    height_upper_bound_cm: float | None = None
    confirmed_above_lower_bound: bool = False
    height_measurements_cm: tuple[float, ...] = field(default_factory=tuple)
    # Alias legado; novos registros devem usar height_measurements_cm.
    measurements_cm: tuple[float, ...] = field(default_factory=tuple)
    measurement_count: int = 0
    height_min_cm: float | None = None
    height_mean_cm: float | None = None
    height_p50_cm: float | None = None
    height_p90_cm: float | None = None
    height_max_cm: float | None = None
    measurement_method: str | None = None
    measurement_spacing_m: float | None = None
    vegetation_cover_pct: float | None = None
    vegetation_type: str | None = None
    recent_cut: bool | None = None
    cut_date: date | None = None
    soil_condition: str | None = None
    moisture_condition: str | None = None
    shadow_condition: str | None = None
    photos: tuple[str, ...] = field(default_factory=tuple)
    photo_references: tuple[str, ...] = field(default_factory=tuple)
    video_reference: str | None = None
    video_references: tuple[str, ...] = field(default_factory=tuple)
    collector_notes: str | None = None
    measurement_quality: str = "unknown"
    source: str | None = None
    qualitative_condition: str | None = None
    recommendation_context: str | None = None
    real_class: str | None = None
    regulatory_class_30cm: str | None = None
    experimental_certainty_zone: str | None = None
    future_primary_target_gt30: bool | None = None
    ground_truth_strength: str = "non_metric"
    classification_ground_truth_strength: str = "none"
    metric_regression_eligible: bool = False
    regulatory_gt30: bool | None = None
    reference_bbox: Mapping[str, float] | None = None
    validation_warnings: tuple[str, ...] = field(default_factory=tuple)
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
        for name in (
            "photos",
            "photo_references",
            "video_references",
            "measurements_cm",
            "height_measurements_cm",
            "validation_warnings",
        ):
            if name in payload and not isinstance(payload[name], tuple):
                payload[name] = tuple(payload[name] or ())
        measurements = tuple(
            payload.get("height_measurements_cm")
            or payload.get("measurements_cm")
            or ()
        )
        payload["height_measurements_cm"] = measurements
        payload["measurements_cm"] = measurements
        photo_references = tuple(
            payload.get("photo_references") or payload.get("photos") or ()
        )
        payload["photo_references"] = photo_references
        payload["photos"] = photo_references
        video_references = tuple(payload.get("video_references") or ())
        if not video_references and payload.get("video_reference"):
            video_references = (str(payload["video_reference"]),)
        payload["video_references"] = video_references
        quality = str(payload.get("measurement_quality") or "unknown")
        if quality not in MEASUREMENT_QUALITY_VALUES:
            raise ValueError(
                "measurement_quality must be one of: "
                + ", ".join(MEASUREMENT_QUALITY_VALUES)
            )
        payload["measurement_quality"] = quality
        measurement_type = str(payload.get("measurement_type") or "").strip()
        if not measurement_type:
            if measurements:
                measurement_type = "multiple" if len(measurements) > 1 else "exact_single"
            elif payload.get("measured_height_cm") is not None:
                measurement_type = "exact_single"
            else:
                measurement_type = "visual_only"
        if measurement_type not in MEASUREMENT_TYPE_VALUES:
            raise ValueError(
                "measurement_type must be one of: "
                + ", ".join(MEASUREMENT_TYPE_VALUES)
            )
        payload["measurement_type"] = measurement_type
        lower_bound = payload.get("height_lower_bound_cm")
        upper_bound = payload.get("height_upper_bound_cm")
        lower_bound = float(lower_bound) if lower_bound not in (None, "") else None
        upper_bound = float(upper_bound) if upper_bound not in (None, "") else None
        if lower_bound is not None and lower_bound < 0:
            raise ValueError("height_lower_bound_cm must be non-negative.")
        if upper_bound is not None and upper_bound < 0:
            raise ValueError("height_upper_bound_cm must be non-negative.")
        if (
            lower_bound is not None
            and upper_bound is not None
            and lower_bound > upper_bound
        ):
            raise ValueError("height_lower_bound_cm cannot exceed height_upper_bound_cm.")
        payload["height_lower_bound_cm"] = lower_bound
        payload["height_upper_bound_cm"] = upper_bound
        if measurement_type == "threshold_lower_bound":
            if lower_bound is None:
                raise ValueError(
                    "threshold_lower_bound requires height_lower_bound_cm."
                )
            forbidden = (
                "measured_height_cm",
                "height_mean_cm",
                "height_p50_cm",
                "height_p90_cm",
            )
            if measurements or any(payload.get(name) is not None for name in forbidden):
                raise ValueError(
                    "threshold_lower_bound cannot contain exact or derived metric heights."
                )
            confirmed = payload.get("confirmed_above_lower_bound") is True
            payload["metric_regression_eligible"] = False
            if confirmed and lower_bound >= 35:
                payload["regulatory_gt30"] = True
                payload["regulatory_class_30cm"] = "gt_30_cm"
                payload["real_class"] = "gt_30_cm"
                payload["experimental_certainty_zone"] = "CLEAR_POSITIVE"
                payload["classification_ground_truth_strength"] = "strong"
        elif measurement_type in {"exact_single", "multiple"}:
            payload["metric_regression_eligible"] = bool(
                measurements or payload.get("measured_height_cm") is not None
            )
        # Protecao holdout e parte do contrato do schema, nao opcao da campanha.
        payload["training_eligible"] = False
        payload["external_validation"] = True
        return cls(sample_id=sample_id, **payload)
