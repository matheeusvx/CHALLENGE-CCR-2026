"""On-demand Sentinel-1 temporal benchmark over immutable validation samples."""

from __future__ import annotations

import calendar
import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.satellite_monitoring.multisource.models import CollectionPeriod
from src.satellite_monitoring.multisource.providers.sentinel1 import Sentinel1Provider
from src.satellite_monitoring.sentinel1_temporal import (
    Sentinel1TemporalConfig,
    analyze_sentinel1_temporal,
)

from .models import MaintenanceTruth, VegetationClass


TEMPORAL_STATUSES = (
    "increasing",
    "decreasing",
    "stable",
    "mixed",
    "insufficient_data",
)
TRACKED_ORBITS = (53, 126)
SMALL_GROUP_MINIMUM = 3


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _previous_calendar_month(value: date) -> date:
    month = 12 if value.month == 1 else value.month - 1
    year = value.year - 1 if value.month == 1 else value.year
    return value.replace(
        year=year,
        month=month,
        day=min(value.day, calendar.monthrange(year, month)[1]),
    )


def resolve_sample_period(row: Mapping[str, Any]) -> tuple[CollectionPeriod, str, list[str]]:
    snapshot = _mapping(row.get("snapshot"))
    raw = snapshot.get("analysis_period")
    if isinstance(raw, str) and "/" in raw:
        start_raw, end_raw = raw.split("/", 1)
    else:
        period = _mapping(raw)
        start_raw, end_raw = period.get("start_date"), period.get("end_date")
    if isinstance(start_raw, str) and isinstance(end_raw, str):
        try:
            return (
                CollectionPeriod(date.fromisoformat(start_raw), date.fromisoformat(end_raw)),
                "snapshot",
                [],
            )
        except ValueError:
            invalid_warning = ["analysis_period_invalid_in_snapshot"]
    else:
        invalid_warning = []

    reference = date.fromisoformat(str(row["reference_date"]))
    return (
        CollectionPeriod(_previous_calendar_month(reference), reference),
        "reference_date_fallback",
        [*invalid_warning, "analysis_period_fallback:reference_date_previous_calendar_month"],
    )


def _geometry_from_document(document: Any) -> dict[str, Any] | None:
    value = _mapping(document)
    if value.get("type") in {"Polygon", "MultiPolygon"} and isinstance(
        value.get("coordinates"), list
    ):
        return value
    if value.get("type") == "Feature":
        return _geometry_from_document(value.get("geometry"))
    return None


class AnalysisArtifactGeometryLookup:
    """Read-only recovery for legacy snapshots that accidentally stored AOI metadata."""

    def __init__(self, output_root: str | Path | None) -> None:
        self.output_root = Path(output_root).resolve() if output_root else None
        self._index: dict[str, Path] | None = None

    def _build_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        if self.output_root is None or not self.output_root.is_dir():
            return index
        for summary_path in self.output_root.rglob("summary.json"):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            analysis_id = summary.get("analysis_id")
            aoi_path = summary_path.with_name("aoi.geojson")
            if isinstance(analysis_id, str) and aoi_path.is_file():
                index[analysis_id] = aoi_path
        return index

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        if self._index is None:
            self._index = self._build_index()
        path = self._index.get(analysis_id)
        if path is None:
            return None
        try:
            return _geometry_from_document(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None


def _distribution(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    counts = Counter(
        row.get("combined_status")
        if row.get("combined_status") in TEMPORAL_STATUSES
        else "insufficient_data"
        for row in materialized
    )
    total = len(materialized)
    return {
        "sample_count": total,
        "statuses": {
            status: {
                "count": counts[status],
                "percentage": (counts[status] / total * 100.0 if total else 0.0),
            }
            for status in TEMPORAL_STATUSES
        },
    }


def _s2_disagrees(row: Mapping[str, Any]) -> bool:
    expected = {"cut": "cortar", "no_cut": "nao_cortar"}.get(
        str(row.get("maintenance_truth"))
    )
    return expected is not None and row.get("s2_decision") in {
        "cortar",
        "nao_cortar",
    } and row.get("s2_decision") != expected


def _channel_value(temporal: Mapping[str, Any], channel: str, key: str) -> Any:
    return _mapping(temporal.get(channel)).get(key)


class ValidationTemporalBenchmarkRunner:
    def __init__(
        self,
        provider: Sentinel1Provider,
        *,
        temporal_config: Sentinel1TemporalConfig | None = None,
        output_root: str | Path | None = None,
        now=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.provider = provider
        base_config = temporal_config or Sentinel1TemporalConfig.from_environment()
        self.temporal_config = replace(base_config, enabled=True)
        self.geometry_lookup = AnalysisArtifactGeometryLookup(output_root)
        self.now = now

    def _geometry(self, row: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        snapshot = _mapping(row.get("snapshot"))
        geometry = _geometry_from_document(
            _mapping(snapshot.get("aoi")).get("geometry_geojson")
        )
        if geometry is not None:
            return geometry, []
        recovered = self.geometry_lookup.get(str(row.get("analysis_id", "")))
        if recovered is not None:
            return recovered, ["geometry_recovered_from_analysis_artifact"]
        return None, ["geometry_unavailable"]

    def _run_sample(self, row: Mapping[str, Any]) -> dict[str, Any]:
        period, period_source, warnings = resolve_sample_period(row)
        geometry, geometry_warnings = self._geometry(row)
        warnings.extend(geometry_warnings)
        base = {
            "sample_id": row.get("sample_id"),
            "vegetation_class": row.get("vegetation_class"),
            "maintenance_truth": row.get("maintenance_truth"),
            "s2_decision": row.get("s2_decision"),
            "analysis_period": {
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "source": period_source,
            },
        }
        if geometry is None:
            return {
                **base,
                "processing_status": "error",
                "error": "validation_sample_geometry_unavailable",
                "canonical_relative_orbit": None,
                "canonical_observation_count": 0,
                "vv_temporal_status": "insufficient_data",
                "vh_temporal_status": "insufficient_data",
                "combined_status": "insufficient_data",
                "vv_slope": None,
                "vh_slope": None,
                "vv_modeled_change_db": None,
                "vh_modeled_change_db": None,
                "span_days": 0,
                "warnings": sorted(set(warnings)),
            }
        try:
            evidence = self.provider.collect_evidence(geometry, period)
            temporal = analyze_sentinel1_temporal(evidence, self.temporal_config)
            vv_span = _channel_value(temporal, "vv", "span_days") or 0
            vh_span = _channel_value(temporal, "vh", "span_days") or 0
            return {
                **base,
                "processing_status": "completed",
                "error": None,
                "canonical_relative_orbit": evidence.metrics.get(
                    "canonical_relative_orbit"
                ),
                "canonical_observation_count": evidence.metrics.get(
                    "canonical_observation_count", 0
                ),
                "vv_temporal_status": _channel_value(temporal, "vv", "status")
                or "insufficient_data",
                "vh_temporal_status": _channel_value(temporal, "vh", "status")
                or "insufficient_data",
                "combined_status": temporal.get("combined_status", "insufficient_data"),
                "vv_slope": _channel_value(
                    temporal, "vv", "theil_sen_slope_db_per_day"
                ),
                "vh_slope": _channel_value(
                    temporal, "vh", "theil_sen_slope_db_per_day"
                ),
                "vv_modeled_change_db": _channel_value(
                    temporal, "vv", "modeled_change_db"
                ),
                "vh_modeled_change_db": _channel_value(
                    temporal, "vh", "modeled_change_db"
                ),
                "span_days": max(vv_span, vh_span),
                "warnings": sorted(
                    set(warnings)
                    | set(evidence.warnings)
                    | set(temporal.get("warnings") or [])
                ),
            }
        except Exception as exc:
            return {
                **base,
                "processing_status": "error",
                "error": f"sentinel1_temporal_processing_failed:{type(exc).__name__}",
                "canonical_relative_orbit": None,
                "canonical_observation_count": 0,
                "vv_temporal_status": "insufficient_data",
                "vh_temporal_status": "insufficient_data",
                "combined_status": "insufficient_data",
                "vv_slope": None,
                "vh_slope": None,
                "vv_modeled_change_db": None,
                "vh_modeled_change_db": None,
                "span_days": 0,
                "warnings": sorted(set(warnings) | {"isolated_sample_failure"}),
            }

    def run(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        samples = [self._run_sample(row) for row in rows]
        by_truth = {
            truth.value: _distribution(
                row for row in samples if row["maintenance_truth"] == truth.value
            )
            for truth in MaintenanceTruth
        }
        by_class = {
            item.value: _distribution(
                row for row in samples if row["vegetation_class"] == item.value
            )
            for item in VegetationClass
        }
        disagreements = [
            {
                "sample_id": row["sample_id"],
                "ground_truth": row["maintenance_truth"],
                "s2_decision": row["s2_decision"],
                "s1_temporal_status": row["combined_status"],
                "vv_slope": row["vv_slope"],
                "vh_slope": row["vh_slope"],
                "vv_modeled_change_db": row["vv_modeled_change_db"],
                "vh_modeled_change_db": row["vh_modeled_change_db"],
            }
            for row in samples
            if _s2_disagrees(row)
        ]
        orbit_groups: dict[str, Any] = {}
        orbit_warnings: list[str] = []
        represented_orbits = sorted(
            {row["canonical_relative_orbit"] for row in samples}
            - {None},
            key=int,
        )
        for orbit in sorted(set(represented_orbits) | set(TRACKED_ORBITS)):
            group = [row for row in samples if row["canonical_relative_orbit"] == orbit]
            details = _distribution(group)
            details["small_group"] = len(group) < SMALL_GROUP_MINIMUM
            orbit_groups[str(orbit)] = details
            if details["small_group"]:
                orbit_warnings.append(f"canonical_orbit_group_small:{orbit}")
        return {
            "generated_at": self.now().isoformat(),
            "experimental": True,
            "samples": samples,
            "summary": {
                "total_samples": len(samples),
                "reprocessed_samples": sum(
                    row["processing_status"] == "completed" for row in samples
                ),
                "temporal_valid_samples": sum(
                    row["processing_status"] == "completed"
                    and row["combined_status"] != "insufficient_data"
                    for row in samples
                ),
                "by_maintenance_truth": by_truth,
                "by_vegetation_class": by_class,
            },
            "s2_disagreements": disagreements,
            "orbit_check": {
                "by_canonical_relative_orbit": orbit_groups,
                "small_group_minimum": SMALL_GROUP_MINIMUM,
                "warnings": orbit_warnings,
                "orbital_correction_applied": False,
            },
            "methodology": {
                "mode": "on_demand",
                "sentinel1_inputs": ["canonical_relative_orbit", "calibrated_sigma0", "VV", "VH"],
                "temporal_analysis": "current",
                "fusion_applied": False,
                "recommendation_changed": False,
                "physical_interpretation": "none",
            },
        }
