"""Exploratory canonical-orbit radiometric change; no physical attribution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timezone
from itertools import combinations
import math
import os
from statistics import median

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .multisource.models import SourceEvidence


@dataclass(frozen=True)
class Sentinel1TemporalConfig:
    enabled: bool = False
    min_observations: int = 4
    min_span_days: float = 18.0
    min_total_change_db: float = 1.0
    residual_mad_multiplier: float = 2.0
    max_gap_days: float = 24.0

    def __post_init__(self):
        if isinstance(self.min_observations, bool) or not isinstance(self.min_observations, int) or self.min_observations < 2:
            raise ValueError("Temporal min_observations must be an integer >= 2")
        for name in (
            "min_span_days",
            "min_total_change_db",
            "residual_mad_multiplier",
            "max_gap_days",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Temporal {name} must be positive and finite")

    @classmethod
    def from_environment(cls):
        raw = os.getenv("SENTINEL1_TEMPORAL_ENABLED", "false").strip().lower()
        if raw not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise ValueError("SENTINEL1_TEMPORAL_ENABLED must be boolean")
        return cls(
            enabled=raw in {"true", "1", "yes", "on"},
            min_observations=int(os.getenv("SENTINEL1_TEMPORAL_MIN_OBSERVATIONS", "4")),
            min_span_days=float(os.getenv("SENTINEL1_TEMPORAL_MIN_SPAN_DAYS", "18")),
            min_total_change_db=float(os.getenv("SENTINEL1_TEMPORAL_MIN_TOTAL_CHANGE_DB", "1.0")),
            residual_mad_multiplier=float(os.getenv("SENTINEL1_TEMPORAL_RESIDUAL_MAD_MULTIPLIER", "2.0")),
            max_gap_days=float(os.getenv("SENTINEL1_TEMPORAL_MAX_GAP_DAYS", "24")),
        )


def _channel(series, channel, config):
    points = [(row["day"], row[f"{channel}_sigma0_median_db"]) for row in series
              if row[f"{channel}_sigma0_median_db"] is not None]
    span = (points[-1][0] - points[0][0]).days if points else 0
    gaps = [
        (current[0] - previous[0]).days
        for previous, current in zip(points, points[1:])
    ]
    max_gap = max(gaps, default=0)
    support_issues = []
    if len(points) < config.min_observations:
        support_issues.append("insufficient_observations")
    if span < config.min_span_days:
        support_issues.append("insufficient_span")
    if max_gap > config.max_gap_days:
        support_issues.append("excessive_gap")
    result = dict(status="insufficient_data", observation_count=len(points), span_days=span,
                  max_gap_days=max_gap, support_issues=support_issues,
                  first_db=points[0][1] if points else None,
                  latest_db=points[-1][1] if points else None,
                  endpoint_delta_db=points[-1][1] - points[0][1] if points else None,
                  theil_sen_slope_db_per_day=None, modeled_change_db=None,
                  residual_mad_db=None, effective_change_threshold_db=None,
                  pairwise_direction_agreement=None)
    if result["endpoint_delta_db"] is not None and not math.isfinite(result["endpoint_delta_db"]):
        result["endpoint_delta_db"] = None
        return result
    if support_issues:
        return result
    x = [(day - points[0][0]).days for day, _ in points]
    y = [value for _, value in points]
    slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i, j in combinations(range(len(x)), 2)]
    slope = median(slopes)
    intercept = median(value - slope * day for day, value in zip(x, y))
    residuals = [value - (intercept + slope * day) for day, value in zip(x, y)]
    center = median(residuals)
    mad = median(abs(value - center) for value in residuals)
    threshold = max(config.min_total_change_db, config.residual_mad_multiplier * mad)
    change = slope * span
    status = "stable" if abs(change) < threshold else "increasing" if change > 0 else "decreasing"
    sign = lambda value: (value > 0) - (value < 0)
    result.update(status=status, theil_sen_slope_db_per_day=slope,
                  modeled_change_db=change, residual_mad_db=mad,
                  effective_change_threshold_db=threshold,
                  pairwise_direction_agreement=sum(sign(value) == sign(slope) for value in slopes) / len(slopes))
    if any(isinstance(value, float) and not math.isfinite(value) for value in result.values()):
        return {key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                for key, value in {**result, "status": "insufficient_data"}.items()}
    return result


def analyze_sentinel1_temporal(evidence: SourceEvidence, config: Sentinel1TemporalConfig) -> dict:
    """Use the already selected canonical orbit; never select a replacement orbit."""
    orbit = evidence.metrics.get("canonical_relative_orbit")
    output = dict(enabled=config.enabled, status="disabled", relative_orbit=orbit,
                  method="theil_sen", daily_aggregation="median_by_utc_day",
                  configuration=asdict(config), vv=None, vh=None,
                  combined_status="disabled", series=[], warnings=[],
                  interpretation_scope="radiometric_change_only",
                  provenance=dict(input="calibrated_sigma0_db",
                                  selection="canonical_relative_orbit_only",
                                  raw_amplitude_used=False, cross_orbit_temporal_mixing=False,
                                  physical_attribution="none"),
                  methodology=dict(intercept="median(y - slope*x)",
                                   residual_mad="median(abs(residual - median(residual))); unscaled",
                                   pairwise_direction_agreement="fraction of all daily pair slopes with the same sign as the Theil-Sen slope; ties count only when slope is zero",
                                   observation_count="valid UTC days per channel"))
    if not config.enabled:
        return output
    days = {}
    warnings = []
    seen_timestamps = set()
    if orbit is None:
        warnings.append("canonical_relative_orbit_unavailable")
    for obs in evidence.observations:
        if orbit is None or obs.metrics.get("relative_orbit") != orbit:
            continue
        if obs.observed_at.tzinfo is None:
            warnings.append("observation_excluded:timezone_missing")
            continue
        timestamp = obs.observed_at.astimezone(timezone.utc)
        if timestamp in seen_timestamps:
            warnings.append("duplicate_timestamp_aggregated")
        seen_timestamps.add(timestamp)
        day = timestamp.date()
        row = days.setdefault(day, {"day": day, "item_count": 0, "vv": [], "vh": []})
        row["item_count"] += 1
        for channel in ("vv", "vh"):
            value = obs.metrics.get(f"{channel}_sigma0_median_db")
            if obs.metrics.get(f"{channel}_radiometric_calibration_status") != "calibrated":
                warnings.append(f"{channel}:observation_excluded:calibration_not_valid")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                warnings.append(f"{channel}:observation_excluded:db_not_finite")
            else:
                row[channel].append(float(value))
    series = []
    for day, row in sorted(days.items()):
        daily = {"day": day, "item_count": row["item_count"]}
        for channel in ("vv", "vh"):
            daily[f"{channel}_item_count"] = len(row[channel])
            daily[f"{channel}_sigma0_median_db"] = median(row[channel]) if row[channel] else None
            value = daily[f"{channel}_sigma0_median_db"]
            if value is not None and not math.isfinite(value):
                daily[f"{channel}_sigma0_median_db"] = None
                warnings.append(f"{channel}:daily_median_not_finite")
        series.append(daily)
    vv, vh = (_channel(series, channel, config) for channel in ("vv", "vh"))
    for channel, result in (("vv", vv), ("vh", vh)):
        if result["status"] == "insufficient_data":
            warnings.append(f"{channel}:insufficient_data")
            for issue in result["support_issues"]:
                warnings.append(f"{channel}:{issue}")
        if result["max_gap_days"] > config.max_gap_days:
            warnings.append(f"{channel}:excessive_gap")
    combined = ("insufficient_data" if "insufficient_data" in {vv["status"], vh["status"]}
                else vv["status"] if vv["status"] == vh["status"] else "mixed")
    output.update(status="completed" if combined != "insufficient_data" else "insufficient_data",
                  combined_status=combined, vv=vv, vh=vh,
                  usable_observation_count=min(vv["observation_count"], vh["observation_count"]),
                  series=[{**row, "day": row["day"].isoformat()} for row in series],
                  warnings=sorted(
                      set(warnings)
                      | (
                          {"sentinel1_failure:insufficient_temporal_support"}
                          if combined == "insufficient_data"
                          else set()
                      )
                  ))
    return output
