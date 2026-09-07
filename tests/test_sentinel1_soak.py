from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.satellite_monitoring.multisource.models import (
    CollectionPeriod,
    EvidenceObservation,
    EvidenceStatus,
    SourceEvidence,
)
from src.satellite_monitoring.sentinel1_evidence import (
    SENTINEL1_EVIDENCE_SCHEMA_VERSION,
    build_sentinel1_evidence_summary,
)
from src.satellite_monitoring.sentinel1_soak import (
    SOAK_SCHEMA_VERSION,
    SoakInterrupted,
    Sentinel1SoakRunner,
    SoakAoi,
    format_duration,
    load_aoi_dataset,
    load_existing_soak_runs,
    load_validation_aois,
    percentile,
    progress_snapshot,
    run_incremental_soak,
    summarize_soak,
    write_soak_outputs,
)
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[-47.0, -23.0], [-46.9, -23.0], [-46.9, -22.9], [-47.0, -23.0]]
    ],
}
PERIOD = CollectionPeriod(date(2026, 8, 1), date(2026, 8, 31))


def _evidence(
    days=(0, 6, 18, 24),
    *,
    calibration_failures: int = 0,
    failure_counts: dict[str, int] | None = None,
) -> SourceEvidence:
    observations = tuple(
        EvidenceObservation(
            observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
            + timedelta(days=day),
            metrics={
                "relative_orbit": 53,
                "vv_sigma0_median_db": -10.0 + day / 100,
                "vh_sigma0_median_db": -16.0 + day / 100,
                "vv_radiometric_calibration_status": "calibrated",
                "vh_radiometric_calibration_status": "calibrated",
            },
        )
        for day in days
    )
    count = len(observations)
    return SourceEvidence(
        source="sentinel-1",
        status=EvidenceStatus.AVAILABLE,
        observations=observations,
        quality=91.0,
        metrics={
            "scenes_found": count + 1,
            "scenes_attempted": count,
            "scenes_accepted": count,
            "scenes_rejected": 1,
            "processing_duration_ms": 120.0,
            "stac_discovery_duration_ms": 10.0,
            "raster_processing_duration_ms": 80.0,
            "calibration_duration_ms": 30.0,
            "calibration_success_count": count - calibration_failures,
            "calibration_failure_count": calibration_failures,
            "canonical_relative_orbit": 53,
            "canonical_observation_count": count,
            "calibrated_observation_count": count - calibration_failures,
            "radiometric_calibration_status": (
                "partial" if calibration_failures else "calibrated"
            ),
            "failure_counts": failure_counts or {},
        },
    )


class QueueProvider:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    def collect_evidence(self, geometry, period):
        self.calls += 1
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def _clock():
    state = {"value": 0.0}

    def tick():
        state["value"] += 0.001
        return state["value"]

    return tick


def _runner(provider):
    return Sentinel1SoakRunner(
        provider,
        temporal_config=Sentinel1TemporalConfig(enabled=True),
        now=lambda: datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        clock=_clock(),
    )


def test_loads_multiple_external_aois_in_deterministic_order(tmp_path):
    dataset = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "b", "properties": {}, "geometry": GEOMETRY},
            {
                "type": "Feature",
                "properties": {"aoi_id": "a"},
                "geometry": GEOMETRY,
            },
        ],
    }
    path = tmp_path / "aois.geojson"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    aois = load_aoi_dataset(path)
    records = _runner(QueueProvider([_evidence(), _evidence()])).run(aois, PERIOD)

    assert [aoi.aoi_id for aoi in aois] == ["b", "a"]
    assert [record["aoi_id"] for record in records] == ["b", "a"]
    assert [record["run_index"] for record in records] == [1, 2]


def test_one_aoi_failure_is_isolated_between_successful_runs():
    provider = QueueProvider([_evidence(), RuntimeError("secret"), _evidence()])
    aois = [SoakAoi(str(index), GEOMETRY) for index in range(3)]

    records = _runner(provider).run(aois, PERIOD)

    assert provider.calls == 3
    assert [record["status"] for record in records] == [
        "successful",
        "failed",
        "successful",
    ]
    assert records[1]["failure_counts"] == {"unexpected_error": 1}
    assert "secret" not in json.dumps(records[1])


def test_summary_rates_distributions_failures_and_percentiles():
    records = [
        {
            "status": "successful",
            "availability": "available",
            "processing_duration_ms": 10,
            "calibration_success_count": 3,
            "calibration_failure_count": 0,
            "temporal_status": "stable",
            "canonical_relative_orbit": 53,
            "max_gap_days": 6,
            "series_rejected_by_excessive_gap": 0,
            "failure_counts": {},
        },
        {
            "status": "partial",
            "availability": "available",
            "processing_duration_ms": 20,
            "calibration_success_count": 1,
            "calibration_failure_count": 1,
            "temporal_status": "insufficient_data",
            "canonical_relative_orbit": 126,
            "max_gap_days": 28,
            "series_rejected_by_excessive_gap": 2,
            "would_be_temporally_usable_without_gap_rule": True,
            "failure_counts": {"calibration_failed": 1},
        },
        {
            "status": "failed",
            "availability": "error",
            "processing_duration_ms": 30,
            "calibration_success_count": 0,
            "calibration_failure_count": 0,
            "temporal_status": "insufficient_data",
            "canonical_relative_orbit": None,
            "max_gap_days": 0,
            "series_rejected_by_excessive_gap": 0,
            "failure_counts": {"stac_unavailable": 1},
        },
    ]

    summary = summarize_soak(records)

    assert summary["successful_runs"] == 1
    assert summary["partial_runs"] == 1
    assert summary["failed_runs"] == 1
    assert summary["availability_rate"] == pytest.approx(200 / 3)
    assert summary["calibration_success_rate"] == 80
    assert summary["temporal_availability_rate"] == pytest.approx(100 / 3)
    assert summary["latency_ms"] == {
        "min": 10,
        "median": 20,
        "p90": 28,
        "p95": 29,
        "max": 30,
    }
    assert summary["canonical_relative_orbit_distribution"] == {
        "126": 1,
        "53": 1,
        "unknown": 1,
    }
    assert summary["failure_categories"][0]["category"] == "calibration_failed"
    assert summary["gap_analysis"]["series_rejected_by_excessive_gap"] == 2
    assert summary["gap_analysis"]["runs_temporally_usable_without_gap_rule"] == 1


def test_percentile_and_zero_run_summary_are_defined():
    assert percentile([0, 10, 20, 30], 0.90) == pytest.approx(27)
    summary = summarize_soak([])
    assert summary["schema_version"] == SOAK_SCHEMA_VERSION
    assert summary["total_runs"] == 0
    assert summary["availability_rate"] == 0
    assert summary["latency_ms"]["p95"] is None


def test_summary_with_zero_valid_runs_remains_well_formed():
    summary = summarize_soak(
        [
            {
                "status": "failed",
                "availability": "error",
                "processing_duration_ms": 4,
                "calibration_success_count": 0,
                "calibration_failure_count": 0,
                "temporal_status": "insufficient_data",
                "canonical_relative_orbit": None,
                "failure_counts": {"stac_unavailable": 1},
            }
        ]
    )

    assert summary["successful_runs"] == 0
    assert summary["availability_rate"] == 0
    assert summary["calibration_success_rate"] == 0
    assert summary["temporal_availability_rate"] == 0


def test_partial_calibration_and_temporal_unavailable_are_partial():
    records = _runner(
        QueueProvider(
            [
                _evidence(calibration_failures=1, failure_counts={"calibration_failed": 1}),
                _evidence(days=(0, 1, 2, 3)),
            ]
        )
    ).run([SoakAoi("partial", GEOMETRY), SoakAoi("short", GEOMETRY)], PERIOD)

    assert records[0]["status"] == "partial"
    assert records[0]["calibration_failure_count"] == 1
    assert records[0]["failure_counts"]["calibration_failed"] == 1
    assert records[1]["status"] == "partial"
    assert records[1]["temporal_status"] == "insufficient_data"
    assert records[1]["failure_counts"]["insufficient_temporal_support"] == 1


def test_gap_report_is_descriptive_and_does_not_change_configuration():
    config = Sentinel1TemporalConfig(enabled=True, max_gap_days=24)
    runner = Sentinel1SoakRunner(
        QueueProvider([_evidence(days=(0, 6, 12, 40))]),
        temporal_config=config,
        now=lambda: datetime(2026, 9, 6, tzinfo=timezone.utc),
        clock=_clock(),
    )

    record = runner.run([SoakAoi("gap", GEOMETRY)], PERIOD)[0]

    assert record["series_rejected_by_excessive_gap"] == 2
    assert record["would_be_temporally_usable_without_gap_rule"] is True
    assert record["max_gap_days"] == 28
    assert runner.temporal_config.max_gap_days == 24


def test_csv_and_json_outputs_are_deterministic_for_same_records(tmp_path):
    records = _runner(QueueProvider([_evidence()])).run(
        [SoakAoi("one", GEOMETRY)], PERIOD
    )
    first = write_soak_outputs(tmp_path / "first", records)
    second = write_soak_outputs(tmp_path / "second", records)

    assert first["summary_json"].read_bytes() == second["summary_json"].read_bytes()
    assert first["runs_json"].read_bytes() == second["runs_json"].read_bytes()
    assert first["runs_csv"].read_bytes() == second["runs_csv"].read_bytes()
    with first["runs_csv"].open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["aoi_id"] == "one"
    assert json.loads(row["failure_counts"]) == {}
    assert row["analysis_period_start"] == "2026-08-01"


def test_validation_loader_reads_snapshot_geometry_without_ground_truth(tmp_path):
    database = tmp_path / "validation.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE validation_samples ("
        "sample_id TEXT, analysis_id TEXT, snapshot_json TEXT, secret_truth TEXT)"
    )
    connection.execute(
        "INSERT INTO validation_samples VALUES (?, ?, ?, ?)",
        (
            "sample-2",
            "analysis-2",
            json.dumps({"aoi": {"geometry_geojson": GEOMETRY}}),
            "must-not-be-read",
        ),
    )
    connection.commit()
    connection.close()

    aois = load_validation_aois(database)

    assert aois == [SoakAoi("sample-2", GEOMETRY)]


def test_validation_loader_recovers_legacy_geometry_from_analysis_artifact(tmp_path):
    database = tmp_path / "validation.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE validation_samples ("
        "sample_id TEXT, analysis_id TEXT, snapshot_json TEXT)"
    )
    connection.execute(
        "INSERT INTO validation_samples VALUES (?, ?, ?)",
        ("sample-1", "analysis-1", "{}"),
    )
    connection.commit()
    connection.close()
    artifact = tmp_path / "outputs" / "run"
    artifact.mkdir(parents=True)
    (artifact / "summary.json").write_text(
        json.dumps({"analysis_id": "analysis-1"}), encoding="utf-8"
    )
    (artifact / "aoi.geojson").write_text(json.dumps(GEOMETRY), encoding="utf-8")

    aois = load_validation_aois(
        database,
        analysis_output_root=tmp_path / "outputs",
    )

    assert aois == [SoakAoi("sample-1", GEOMETRY)]


def test_evidence_summary_contract_version_and_required_metrics():
    source = _evidence()
    source = SourceEvidence(
        **{
            **source.__dict__,
            "metrics": {
                **source.metrics,
                "processing_duration_ms": 123.5,
                "temporal_usable_observation_count": 4,
                "temporal_analysis": {
                    "combined_status": "stable",
                    "vv": {"modeled_change_db": 0.2},
                    "vh": {"modeled_change_db": -0.1},
                    "warnings": [],
                },
            },
        }
    )

    summary = build_sentinel1_evidence_summary(source)

    assert summary["schema_version"] == SENTINEL1_EVIDENCE_SCHEMA_VERSION == "1.0"
    assert summary["temporal_usable_observation_count"] == 4
    assert summary["processing_duration_ms"] == 123.5
    assert summary["vv_change_db"] == 0.2
    assert summary["vh_change_db"] == -0.1


def test_progress_output_does_not_change_scientific_records(tmp_path):
    aois = [SoakAoi("one", GEOMETRY), SoakAoi("two", GEOMETRY)]
    direct = _runner(QueueProvider([_evidence(), _evidence()])).run(aois, PERIOD)
    messages = []

    incremental = run_incremental_soak(
        _runner(QueueProvider([_evidence(), _evidence()])),
        aois,
        PERIOD,
        tmp_path,
        printer=messages.append,
        progress_clock=_clock(),
    )

    assert incremental == direct
    assert messages[0] == "[1/2] AOI one | repetição 1/1 | iniciando..."
    assert any("temporal=stable" in message for message in messages)
    assert any(message == "Progresso: 2/2" for message in messages)
    assert any(message.startswith("ETA: ~") for message in messages)


def test_outputs_are_incremental_and_partial_summary_is_visible(tmp_path):
    class InspectSecondCallProvider:
        calls = 0

        def collect_evidence(self, geometry, period):
            self.calls += 1
            if self.calls == 2:
                runs = json.loads(
                    (tmp_path / "sentinel1_soak_runs.json").read_text(encoding="utf-8")
                )
                summary = json.loads(
                    (tmp_path / "sentinel1_soak_summary.json").read_text(
                        encoding="utf-8"
                    )
                )
                with (tmp_path / "sentinel1_soak_runs.csv").open(
                    encoding="utf-8", newline=""
                ) as stream:
                    csv_rows = list(csv.DictReader(stream))
                assert len(runs) == 1
                assert len(csv_rows) == 1
                assert summary["total_runs"] == 1
                assert summary["complete"] is False
            return _evidence()

    records = run_incremental_soak(
        _runner(InspectSecondCallProvider()),
        [SoakAoi("one", GEOMETRY), SoakAoi("two", GEOMETRY)],
        PERIOD,
        tmp_path,
        quiet=True,
    )

    assert len(records) == 2
    final_summary = json.loads(
        (tmp_path / "sentinel1_soak_summary.json").read_text(encoding="utf-8")
    )
    assert final_summary["complete"] is True
    assert final_summary["total_runs"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_resume_skips_completed_run_and_does_not_duplicate(tmp_path):
    first_provider = QueueProvider([_evidence()])
    run_incremental_soak(
        _runner(first_provider),
        [SoakAoi("one", GEOMETRY)],
        PERIOD,
        tmp_path,
        quiet=True,
    )
    resumed_provider = QueueProvider([_evidence()])

    records = run_incremental_soak(
        _runner(resumed_provider),
        [SoakAoi("one", GEOMETRY), SoakAoi("two", GEOMETRY)],
        PERIOD,
        tmp_path,
        resume=True,
        quiet=True,
    )

    assert resumed_provider.calls == 1
    assert [(row["aoi_id"], row["repetition"]) for row in records] == [
        ("one", 1),
        ("two", 1),
    ]
    assert len(load_existing_soak_runs(tmp_path)) == 2


def test_without_resume_refuses_to_overwrite_existing_outputs(tmp_path):
    write_soak_outputs(tmp_path, [])

    with pytest.raises(FileExistsError, match="--resume"):
        run_incremental_soak(
            _runner(QueueProvider([_evidence()])),
            [SoakAoi("one", GEOMETRY)],
            PERIOD,
            tmp_path,
            quiet=True,
        )


def test_keyboard_interrupt_preserves_completed_runs_and_partial_summary(tmp_path):
    class InterruptingProvider:
        calls = 0

        def collect_evidence(self, geometry, period):
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt
            return _evidence()

    messages = []
    with pytest.raises(SoakInterrupted) as captured:
        run_incremental_soak(
            _runner(InterruptingProvider()),
            [SoakAoi("one", GEOMETRY), SoakAoi("two", GEOMETRY)],
            PERIOD,
            tmp_path,
            printer=messages.append,
        )

    assert captured.value.completed_runs == 1
    assert len(load_existing_soak_runs(tmp_path)) == 1
    summary = json.loads(
        (tmp_path / "sentinel1_soak_summary.json").read_text(encoding="utf-8")
    )
    assert summary["complete"] is False
    assert messages[-1] == "Soak interrompido. 1/2 execuções preservadas."


def test_eta_is_defined_before_any_run_and_quiet_suppresses_progress(tmp_path):
    progress = progress_snapshot([], 69, 0)
    assert progress["average_seconds_per_run"] == 0
    assert progress["eta_seconds"] == 0
    assert format_duration(progress["eta_seconds"]) == "0s"
    messages = []

    run_incremental_soak(
        _runner(QueueProvider([_evidence()])),
        [SoakAoi("quiet", GEOMETRY)],
        PERIOD,
        tmp_path,
        quiet=True,
        printer=messages.append,
    )

    assert messages == []
