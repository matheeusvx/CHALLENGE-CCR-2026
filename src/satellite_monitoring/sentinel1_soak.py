"""Reproducible, fail-soft Sentinel-1 operational soak runner."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Mapping

from .multisource.models import CollectionPeriod, EvidenceStatus, SourceEvidence
from .multisource.providers.sentinel1 import Sentinel1Provider
from .sentinel1_temporal import Sentinel1TemporalConfig, analyze_sentinel1_temporal


SOAK_SCHEMA_VERSION = "1.0"
TEMPORAL_USABLE_STATUSES = {"increasing", "decreasing", "stable", "mixed"}
CSV_FIELDS = (
    "run_index",
    "repetition",
    "aoi_id",
    "status",
    "availability",
    "started_at",
    "finished_at",
    "analysis_period_start",
    "analysis_period_end",
    "processing_duration_ms",
    "stac_discovery_duration_ms",
    "raster_processing_duration_ms",
    "calibration_duration_ms",
    "temporal_analysis_duration_ms",
    "scenes_found",
    "scenes_attempted",
    "scenes_accepted",
    "scenes_rejected",
    "canonical_relative_orbit",
    "canonical_observation_count",
    "calibrated_observation_count",
    "temporal_usable_observation_count",
    "temporal_status",
    "calibration_success_count",
    "calibration_failure_count",
    "vv_max_gap_days",
    "vh_max_gap_days",
    "max_gap_days",
    "series_rejected_by_excessive_gap",
    "would_be_temporally_usable_without_gap_rule",
    "failure_counts",
    "warnings",
)


@dataclass(frozen=True)
class SoakAoi:
    aoi_id: str
    geometry: dict[str, Any]


class SoakInterrupted(KeyboardInterrupt):
    def __init__(self, completed_runs: int, total_runs: int) -> None:
        super().__init__(f"Soak interrupted after {completed_runs}/{total_runs} runs")
        self.completed_runs = completed_runs
        self.total_runs = total_runs


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _geometry(document: Any) -> dict[str, Any] | None:
    value = _mapping(document)
    if value.get("type") in {"Polygon", "MultiPolygon"} and isinstance(
        value.get("coordinates"), list
    ):
        return {"type": value["type"], "coordinates": value["coordinates"]}
    if value.get("type") == "Feature":
        return _geometry(value.get("geometry"))
    return None


def _validated_aois(rows: Iterable[tuple[Any, Any]]) -> list[SoakAoi]:
    aois: list[SoakAoi] = []
    seen: set[str] = set()
    for raw_id, raw_geometry in rows:
        aoi_id = str(raw_id or "").strip()
        geometry = _geometry(raw_geometry)
        if not aoi_id:
            raise ValueError("Every AOI must have a non-empty aoi_id")
        if aoi_id in seen:
            raise ValueError(f"Duplicate aoi_id: {aoi_id}")
        if geometry is None:
            raise ValueError(f"AOI {aoi_id} must contain a Polygon or MultiPolygon")
        seen.add(aoi_id)
        aois.append(SoakAoi(aoi_id, geometry))
    return aois


def load_aoi_dataset(path: str | Path) -> list[SoakAoi]:
    """Load a GeoJSON FeatureCollection or a JSON list of AOI records."""

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(document, dict) and document.get("type") == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list):
            raise ValueError("GeoJSON FeatureCollection.features must be a list")
        return _validated_aois(
            (
                feature.get("id") or _mapping(feature.get("properties")).get("aoi_id"),
                feature.get("geometry"),
            )
            for feature in features
            if isinstance(feature, dict)
        )
    if isinstance(document, list):
        return _validated_aois(
            (row.get("aoi_id") or row.get("id"), row.get("geometry"))
            for row in document
            if isinstance(row, dict)
        )
    raise ValueError("AOI dataset must be a FeatureCollection or JSON list")


def _snapshot_geometry(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    return _geometry(_mapping(snapshot.get("aoi")).get("geometry_geojson"))


def _analysis_geometry_index(output_root: str | Path | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if output_root is None:
        return index
    root = Path(output_root).resolve()
    if not root.is_dir():
        return index
    for summary_path in sorted(root.rglob("summary.json")):
        aoi_path = summary_path.with_name("aoi.geojson")
        if not aoi_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            geometry = _geometry(json.loads(aoi_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        analysis_id = _mapping(summary).get("analysis_id")
        if isinstance(analysis_id, str) and geometry is not None:
            index[analysis_id] = geometry
    return index


def load_validation_aois(
    database_path: str | Path,
    *,
    analysis_output_root: str | Path | None = None,
) -> list[SoakAoi]:
    """Read only identifiers and geometry snapshots from validation SQLite."""

    path = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT sample_id, analysis_id, snapshot_json "
            "FROM validation_samples ORDER BY sample_id"
        ).fetchall()
    finally:
        connection.close()
    artifact_geometries = _analysis_geometry_index(analysis_output_root)
    candidates = []
    for sample_id, analysis_id, snapshot_json in rows:
        snapshot = json.loads(snapshot_json)
        geometry = _snapshot_geometry(_mapping(snapshot)) or artifact_geometries.get(
            str(analysis_id)
        )
        if geometry is None:
            raise ValueError(f"Validation sample {sample_id} has no snapshot geometry")
        candidates.append((sample_id, geometry))
    return _validated_aois(candidates)


def percentile(values: Iterable[float], probability: float) -> float | None:
    """Return a deterministic linearly interpolated percentile (type 7)."""

    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if not 0.0 <= probability <= 1.0:
        raise ValueError("Percentile probability must be between zero and one")
    rank = (len(ordered) - 1) * probability
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(values: Iterable[Any], *, unknown: str = "unknown") -> dict[str, int]:
    counts = Counter(unknown if value is None else str(value) for value in values)
    return dict(sorted(counts.items()))


def _latency(values: Iterable[float]) -> dict[str, float | None]:
    samples = sorted(float(value) for value in values if math.isfinite(float(value)))
    return {
        "min": min(samples) if samples else None,
        "median": median(samples) if samples else None,
        "p90": percentile(samples, 0.90),
        "p95": percentile(samples, 0.95),
        "max": max(samples) if samples else None,
    }


def _channel(temporal: Mapping[str, Any], name: str) -> dict[str, Any]:
    return _mapping(temporal.get(name))


def _gap_metrics(temporal: Mapping[str, Any]) -> dict[str, Any]:
    channels = [_channel(temporal, name) for name in ("vv", "vh")]
    issues = [set(channel.get("support_issues") or []) for channel in channels]
    excessive = ["excessive_gap" in channel_issues for channel_issues in issues]
    gaps = [float(channel.get("max_gap_days") or 0.0) for channel in channels]
    return {
        "vv_max_gap_days": gaps[0],
        "vh_max_gap_days": gaps[1],
        "max_gap_days": max(gaps, default=0.0),
        "series_rejected_by_excessive_gap": sum(excessive),
        "would_be_temporally_usable_without_gap_rule": bool(any(excessive))
        and all(channel_issues <= {"excessive_gap"} for channel_issues in issues),
    }


def _run_status(availability: str, metrics: Mapping[str, Any], temporal_status: str) -> str:
    if availability != EvidenceStatus.AVAILABLE.value:
        return "failed"
    if int(metrics.get("calibration_failure_count", 0) or 0) > 0:
        return "partial"
    if temporal_status not in TEMPORAL_USABLE_STATUSES:
        return "partial"
    return "successful"


class Sentinel1SoakRunner:
    def __init__(
        self,
        provider: Sentinel1Provider,
        *,
        temporal_config: Sentinel1TemporalConfig | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        clock: Callable[[], float] = time.perf_counter,
        temporal_analyzer: Callable[
            [SourceEvidence, Sentinel1TemporalConfig], dict[str, Any]
        ] = analyze_sentinel1_temporal,
    ) -> None:
        self.provider = provider
        base = temporal_config or Sentinel1TemporalConfig.from_environment()
        self.temporal_config = replace(base, enabled=True)
        self.now = now
        self.clock = clock
        self.temporal_analyzer = temporal_analyzer

    def _run_one(
        self,
        aoi: SoakAoi,
        period: CollectionPeriod,
        *,
        run_index: int,
        repetition: int,
    ) -> dict[str, Any]:
        started_at = self.now()
        started = self.clock()
        try:
            evidence = self.provider.collect_evidence(aoi.geometry, period)
            temporal_started = self.clock()
            temporal = self.temporal_analyzer(evidence, self.temporal_config)
            temporal_duration_ms = (self.clock() - temporal_started) * 1000.0
            metrics = evidence.metrics
            temporal_status = str(
                temporal.get("combined_status") or "insufficient_data"
            )
            failure_counts = dict(metrics.get("failure_counts") or {})
            if temporal_status == "insufficient_data":
                failure_counts["insufficient_temporal_support"] = max(
                    1, int(failure_counts.get("insufficient_temporal_support", 0))
                )
            warnings = sorted(
                set(evidence.warnings) | set(temporal.get("warnings") or [])
            )
            gap_metrics = _gap_metrics(temporal)
            availability = evidence.status.value
            status = _run_status(availability, metrics, temporal_status)
            values = {
                key: metrics.get(key, 0)
                for key in (
                    "scenes_found",
                    "scenes_attempted",
                    "scenes_accepted",
                    "scenes_rejected",
                    "canonical_observation_count",
                    "calibrated_observation_count",
                    "temporal_usable_observation_count",
                    "calibration_success_count",
                    "calibration_failure_count",
                    "stac_discovery_duration_ms",
                    "raster_processing_duration_ms",
                    "calibration_duration_ms",
                )
            }
            values["temporal_usable_observation_count"] = int(
                temporal.get("usable_observation_count", 0) or 0
            )
            canonical_orbit = metrics.get("canonical_relative_orbit")
        except Exception as exc:
            temporal_duration_ms = 0.0
            temporal_status = "insufficient_data"
            failure_counts = {"unexpected_error": 1}
            warnings = [
                "sentinel1_failure:unexpected_error",
                f"soak_run_failed:{type(exc).__name__}",
            ]
            gap_metrics = {
                "vv_max_gap_days": 0.0,
                "vh_max_gap_days": 0.0,
                "max_gap_days": 0.0,
                "series_rejected_by_excessive_gap": 0,
                "would_be_temporally_usable_without_gap_rule": False,
            }
            availability = EvidenceStatus.ERROR.value
            status = "failed"
            values = {
                key: 0
                for key in (
                    "scenes_found",
                    "scenes_attempted",
                    "scenes_accepted",
                    "scenes_rejected",
                    "canonical_observation_count",
                    "calibrated_observation_count",
                    "temporal_usable_observation_count",
                    "calibration_success_count",
                    "calibration_failure_count",
                    "stac_discovery_duration_ms",
                    "raster_processing_duration_ms",
                    "calibration_duration_ms",
                )
            }
            canonical_orbit = None
        finished_at = self.now()
        return {
            "schema_version": SOAK_SCHEMA_VERSION,
            "run_index": run_index,
            "repetition": repetition,
            "aoi_id": aoi.aoi_id,
            "status": status,
            "availability": availability,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "analysis_period": {
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
            },
            "processing_duration_ms": max(0.0, (self.clock() - started) * 1000.0),
            **values,
            "temporal_analysis_duration_ms": max(0.0, temporal_duration_ms),
            "canonical_relative_orbit": canonical_orbit,
            "temporal_status": temporal_status,
            **gap_metrics,
            "failure_counts": dict(sorted(failure_counts.items())),
            "warnings": warnings,
        }

    def run(
        self,
        aois: Iterable[SoakAoi],
        period: CollectionPeriod,
        *,
        repetitions: int = 1,
        completed_keys: set[tuple[str, int, str, str]] | None = None,
        on_start: Callable[[int, int, SoakAoi, int], None] | None = None,
        on_complete: Callable[[dict[str, Any], int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        if repetitions <= 0:
            raise ValueError("repetitions must be positive")
        materialized_aois = list(aois)
        total_runs = len(materialized_aois) * repetitions
        completed = completed_keys or set()
        records = []
        run_index = 0
        for aoi in materialized_aois:
            for repetition in range(1, repetitions + 1):
                run_index += 1
                if soak_run_key(aoi.aoi_id, repetition, period) in completed:
                    continue
                if on_start is not None:
                    on_start(run_index, total_runs, aoi, repetition)
                record = self._run_one(
                    aoi,
                    period,
                    run_index=run_index,
                    repetition=repetition,
                )
                records.append(record)
                if on_complete is not None:
                    on_complete(record, run_index, total_runs)
        return records


def soak_run_key(
    aoi_id: str,
    repetition: int,
    period: CollectionPeriod,
) -> tuple[str, int, str, str]:
    return (
        str(aoi_id),
        int(repetition),
        period.start_date.isoformat(),
        period.end_date.isoformat(),
    )


def record_run_key(record: Mapping[str, Any]) -> tuple[str, int, str, str]:
    period = _mapping(record.get("analysis_period"))
    return (
        str(record.get("aoi_id", "")),
        int(record.get("repetition", 0) or 0),
        str(period.get("start_date", "")),
        str(period.get("end_date", "")),
    )


def summarize_soak(
    records: Iterable[Mapping[str, Any]],
    *,
    complete: bool = True,
) -> dict[str, Any]:
    runs = list(records)
    total = len(runs)
    calibration_successes = sum(
        int(run.get("calibration_success_count", 0) or 0) for run in runs
    )
    calibration_failures = sum(
        int(run.get("calibration_failure_count", 0) or 0) for run in runs
    )
    calibration_total = calibration_successes + calibration_failures
    failures: Counter[str] = Counter()
    for run in runs:
        failures.update(
            {
                str(category): int(count)
                for category, count in _mapping(run.get("failure_counts")).items()
            }
        )
    gaps = [float(run.get("max_gap_days", 0.0) or 0.0) for run in runs]
    return {
        "schema_version": SOAK_SCHEMA_VERSION,
        "complete": complete,
        "total_runs": total,
        "successful_runs": sum(run.get("status") == "successful" for run in runs),
        "failed_runs": sum(run.get("status") == "failed" for run in runs),
        "partial_runs": sum(run.get("status") == "partial" for run in runs),
        "availability_rate": (
            sum(run.get("availability") == "available" for run in runs) / total * 100.0
            if total
            else 0.0
        ),
        "calibration_success_rate": (
            calibration_successes / calibration_total * 100.0
            if calibration_total
            else 0.0
        ),
        "temporal_availability_rate": (
            sum(run.get("temporal_status") in TEMPORAL_USABLE_STATUSES for run in runs)
            / total
            * 100.0
            if total
            else 0.0
        ),
        "latency_ms": _latency(
            float(run.get("processing_duration_ms", 0.0) or 0.0) for run in runs
        ),
        "failure_categories": [
            {"category": category, "count": count}
            for category, count in sorted(
                failures.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "canonical_relative_orbit_distribution": _distribution(
            run.get("canonical_relative_orbit") for run in runs
        ),
        "temporal_status_distribution": _distribution(
            run.get("temporal_status") for run in runs
        ),
        "gap_analysis": {
            "series_rejected_by_excessive_gap": sum(
                int(run.get("series_rejected_by_excessive_gap", 0) or 0)
                for run in runs
            ),
            "runs_rejected_by_excessive_gap": sum(
                int(run.get("series_rejected_by_excessive_gap", 0) or 0) > 0
                for run in runs
            ),
            "runs_temporally_usable_without_gap_rule": sum(
                bool(run.get("would_be_temporally_usable_without_gap_rule"))
                for run in runs
            ),
            "largest_gap_days": _latency(gaps),
            "largest_gap_days_distribution": _distribution(gaps),
        },
        "units": {
            "rates": "percent",
            "latency": "milliseconds",
            "gaps": "days",
        },
        "methodology": {
            "latency_percentile": "linear_interpolation_type_7",
            "threshold_optimization": False,
            "fusion_applied": False,
            "recommendation_changed": False,
            "physical_interpretation": "none",
        },
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _json_text(payload: Any) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _csv_text(records: Iterable[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=CSV_FIELDS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        row = dict(record)
        period = _mapping(row.pop("analysis_period", {}))
        row["analysis_period_start"] = period.get("start_date")
        row["analysis_period_end"] = period.get("end_date")
        writer.writerow({key: _csv_value(row.get(key)) for key in CSV_FIELDS})
    return stream.getvalue()


def _atomic_write_text(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_soak_outputs(
    output_directory: str | Path,
    records: Iterable[Mapping[str, Any]],
    *,
    complete: bool = True,
) -> dict[str, Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    runs = [dict(record) for record in records]
    summary = summarize_soak(runs, complete=complete)
    runs_path = output / "sentinel1_soak_runs.json"
    summary_path = output / "sentinel1_soak_summary.json"
    csv_path = output / "sentinel1_soak_runs.csv"
    _atomic_write_text(runs_path, _json_text(runs))
    _atomic_write_text(csv_path, _csv_text(runs))
    # Summary is the final marker for a consistently materialized snapshot.
    _atomic_write_text(summary_path, _json_text(summary))
    return {"runs_json": runs_path, "summary_json": summary_path, "runs_csv": csv_path}


def load_existing_soak_runs(output_directory: str | Path) -> list[dict[str, Any]]:
    output = Path(output_directory)
    runs_path = output / "sentinel1_soak_runs.json"
    summary_path = output / "sentinel1_soak_summary.json"
    csv_path = output / "sentinel1_soak_runs.csv"
    if not runs_path.exists():
        if summary_path.exists() or csv_path.exists():
            raise ValueError("Cannot resume without sentinel1_soak_runs.json")
        return []
    payload = json.loads(runs_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("Existing soak runs artifact must contain a JSON list")
    return [dict(row) for row in payload]


def format_duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def progress_snapshot(
    records: Iterable[Mapping[str, Any]],
    total_runs: int,
    elapsed_seconds: float,
) -> dict[str, float | int]:
    materialized = list(records)
    completed_runs = len(materialized)
    durations = [
        float(record.get("processing_duration_ms", 0.0) or 0.0) / 1000.0
        for record in materialized
    ]
    average_seconds = sum(durations) / completed_runs if completed_runs else 0.0
    return {
        "completed_runs": completed_runs,
        "total_runs": total_runs,
        "elapsed_seconds": max(0.0, elapsed_seconds),
        "average_seconds_per_run": average_seconds,
        "eta_seconds": max(0, total_runs - completed_runs) * average_seconds,
    }


def _primary_failure_category(record: Mapping[str, Any]) -> str:
    failures = _mapping(record.get("failure_counts"))
    if not failures:
        return "unexpected_error"
    primary = {
        category: count
        for category, count in failures.items()
        if category != "insufficient_temporal_support"
    } or failures
    return min(primary, key=lambda category: (-int(primary[category]), str(category)))


def run_incremental_soak(
    runner: Sentinel1SoakRunner,
    aois: Iterable[SoakAoi],
    period: CollectionPeriod,
    output_directory: str | Path,
    *,
    repetitions: int = 1,
    resume: bool = False,
    quiet: bool = False,
    printer: Callable[[str], None] = print,
    progress_clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, Any]]:
    materialized_aois = list(aois)
    total_runs = len(materialized_aois) * repetitions
    output = Path(output_directory)
    artifact_paths = [
        output / "sentinel1_soak_runs.json",
        output / "sentinel1_soak_summary.json",
        output / "sentinel1_soak_runs.csv",
    ]
    if not resume and any(path.exists() for path in artifact_paths):
        raise FileExistsError(
            "Soak output already exists; choose another directory or use --resume"
        )
    records = load_existing_soak_runs(output) if resume else []
    planned_keys = {
        soak_run_key(aoi.aoi_id, repetition, period)
        for aoi in materialized_aois
        for repetition in range(1, repetitions + 1)
    }
    completed_keys: set[tuple[str, int, str, str]] = set()
    for record in records:
        if record.get("schema_version") != SOAK_SCHEMA_VERSION:
            raise ValueError("Existing soak run has an incompatible schema_version")
        if record.get("status") not in {"successful", "partial", "failed"}:
            raise ValueError("Existing soak artifact contains an incomplete run")
        key = record_run_key(record)
        if key not in planned_keys:
            raise ValueError("Existing soak run does not belong to the requested plan")
        if key in completed_keys:
            raise ValueError("Existing soak runs contain a duplicate run key")
        completed_keys.add(key)
    records.sort(key=lambda record: int(record.get("run_index", 0) or 0))
    session_started = progress_clock()

    if resume and records and not quiet:
        printer(f"Retomada: {len(records)}/{total_runs} execuções já preservadas.")

    def on_start(index: int, total: int, aoi: SoakAoi, repetition: int) -> None:
        if not quiet:
            printer(
                f"[{index}/{total}] AOI {aoi.aoi_id} | repetição "
                f"{repetition}/{repetitions} | iniciando..."
            )

    def on_complete(record: dict[str, Any], index: int, total: int) -> None:
        records.append(record)
        records.sort(key=lambda row: int(row.get("run_index", 0) or 0))
        write_soak_outputs(output, records, complete=False)
        if quiet:
            return
        seconds = float(record.get("processing_duration_ms", 0.0) or 0.0) / 1000.0
        if record.get("status") == "failed":
            printer(
                f"[{index}/{total}] AOI {record['aoi_id']} | falhou | "
                f"{seconds:.1f}s | {_primary_failure_category(record)}"
            )
        else:
            printer(
                f"[{index}/{total}] AOI {record['aoi_id']} | concluída | "
                f"{seconds:.1f}s | {record.get('availability')} | "
                f"temporal={record.get('temporal_status')}"
            )
        progress = progress_snapshot(
            records,
            total,
            max(
                progress_clock() - session_started,
                sum(
                    float(row.get("processing_duration_ms", 0.0) or 0.0)
                    for row in records
                )
                / 1000.0,
            ),
        )
        printer(f"Progresso: {progress['completed_runs']}/{total}")
        printer(f"Decorrido: {format_duration(float(progress['elapsed_seconds']))}")
        printer(
            f"Média: {float(progress['average_seconds_per_run']):.1f}s/run"
        )
        printer(f"ETA: ~{format_duration(float(progress['eta_seconds']))}")

    try:
        runner.run(
            materialized_aois,
            period,
            repetitions=repetitions,
            completed_keys=completed_keys,
            on_start=on_start,
            on_complete=on_complete,
        )
    except KeyboardInterrupt as exc:
        write_soak_outputs(output, records, complete=False)
        printer(
            f"Soak interrompido. {len(records)}/{total_runs} execuções preservadas."
        )
        raise SoakInterrupted(len(records), total_runs) from exc
    write_soak_outputs(output, records, complete=True)
    return records
