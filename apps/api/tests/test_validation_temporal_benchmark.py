from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import (
    get_validation_repository,
    get_validation_temporal_benchmark_runner,
)
from apps.api.app.main import app
from apps.api.app.validation.repository import ValidationSampleRepository
from apps.api.app.validation.temporal_benchmark import (
    ValidationTemporalBenchmarkRunner,
    resolve_sample_period,
)
from src.satellite_monitoring.multisource.models import (
    EvidenceObservation,
    EvidenceStatus,
    SourceEvidence,
)
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig


def geometry(marker: float = 0.0) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [marker, 0.0],
                [marker + 0.1, 0.0],
                [marker + 0.1, 0.1],
                [marker, 0.0],
            ]
        ],
    }


def row(**updates) -> dict:
    value = {
        "sample_id": str(uuid4()),
        "analysis_id": str(uuid4()),
        "vegetation_class": "shrub",
        "maintenance_truth": "cut",
        "reference_date": "2026-09-06",
        "s2_decision": "cortar",
        "snapshot": {
            "analysis_period": {
                "start_date": "2026-05-01",
                "end_date": "2026-06-01",
            },
            "aoi": {"geometry_geojson": geometry()},
        },
    }
    value.update(updates)
    return value


def evidence(
    *,
    orbit: int = 53,
    vv: tuple[float, ...] = (-10.0, -9.0, -8.0, -7.0),
    vh: tuple[float, ...] = (-16.0, -15.0, -14.0, -13.0),
) -> SourceEvidence:
    first = datetime(2026, 5, 1, tzinfo=timezone.utc)
    observations = tuple(
        EvidenceObservation(
            observed_at=first + timedelta(days=index * 10),
            metrics={
                "relative_orbit": orbit,
                "vv_sigma0_median_db": vv_value,
                "vh_sigma0_median_db": vh_value,
                "vv_radiometric_calibration_status": "calibrated",
                "vh_radiometric_calibration_status": "calibrated",
            },
        )
        for index, (vv_value, vh_value) in enumerate(zip(vv, vh, strict=True))
    )
    return SourceEvidence(
        source="sentinel-1",
        status=EvidenceStatus.AVAILABLE,
        observations=observations,
        metrics={
            "canonical_relative_orbit": orbit,
            "canonical_observation_count": len(observations),
        },
    )


class FakeProvider:
    def __init__(self, outputs: list[SourceEvidence | Exception]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[dict, object]] = []

    def collect_evidence(self, sample_geometry, period):
        self.calls.append((sample_geometry, period))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def runner(provider: FakeProvider, *, output_root: Path | None = None):
    return ValidationTemporalBenchmarkRunner(
        provider,  # type: ignore[arg-type]
        temporal_config=Sentinel1TemporalConfig(enabled=False),
        output_root=output_root,
        now=lambda: datetime(2026, 9, 6, tzinfo=timezone.utc),
    )


def test_uses_original_analysis_period_and_snapshot_geometry() -> None:
    provider = FakeProvider([evidence()])
    result = runner(provider).run([row()])

    assert provider.calls[0][0] == geometry()
    assert provider.calls[0][1].start_date == date(2026, 5, 1)
    assert provider.calls[0][1].end_date == date(2026, 6, 1)
    assert result["samples"][0]["analysis_period"]["source"] == "snapshot"


def test_period_falls_back_to_reference_date_and_previous_calendar_month() -> None:
    sample = row(reference_date="2024-03-31")
    sample["snapshot"].pop("analysis_period")

    period, source, warnings = resolve_sample_period(sample)

    assert period.start_date == date(2024, 2, 29)
    assert period.end_date == date(2024, 3, 31)
    assert source == "reference_date_fallback"
    assert "analysis_period_fallback:reference_date_previous_calendar_month" in warnings


def test_legacy_geometry_is_recovered_read_only_from_analysis_artifact(
    tmp_path: Path,
) -> None:
    sample = row()
    sample["snapshot"]["aoi"]["geometry_geojson"] = {"source": "geojson_inline"}
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        '{"analysis_id": "' + sample["analysis_id"] + '"}', encoding="utf-8"
    )
    (run_dir / "aoi.geojson").write_text(
        '{"type":"Feature","properties":{},"geometry":'
        + __import__("json").dumps(geometry(2.0))
        + "}",
        encoding="utf-8",
    )
    provider = FakeProvider([evidence()])

    result = runner(provider, output_root=tmp_path).run([sample])

    assert provider.calls[0][0] == geometry(2.0)
    assert "geometry_recovered_from_analysis_artifact" in result["samples"][0]["warnings"]


def test_temporal_available_reports_both_channels_and_modeled_change() -> None:
    result = runner(FakeProvider([evidence()])).run([row()])
    sample = result["samples"][0]

    assert sample["processing_status"] == "completed"
    assert sample["canonical_relative_orbit"] == 53
    assert sample["canonical_observation_count"] == 4
    assert sample["vv_temporal_status"] == "increasing"
    assert sample["vh_temporal_status"] == "increasing"
    assert sample["combined_status"] == "increasing"
    assert sample["vv_slope"] == pytest.approx(0.1)
    assert sample["vv_modeled_change_db"] == pytest.approx(3.0)
    assert sample["span_days"] == 30
    assert result["summary"]["temporal_valid_samples"] == 1


def test_temporal_insufficient_is_counted_without_aborting() -> None:
    short = evidence(vv=(-10.0, -9.0), vh=(-16.0, -15.0))
    result = runner(FakeProvider([short])).run([row()])

    assert result["samples"][0]["combined_status"] == "insufficient_data"
    assert result["summary"]["reprocessed_samples"] == 1
    assert result["summary"]["temporal_valid_samples"] == 0


def test_isolated_failure_continues_remaining_samples() -> None:
    provider = FakeProvider([RuntimeError("isolated"), evidence()])
    result = runner(provider).run([row(), row()])

    assert result["samples"][0]["processing_status"] == "error"
    assert result["samples"][0]["error"].endswith("RuntimeError")
    assert result["samples"][1]["processing_status"] == "completed"
    assert result["summary"]["reprocessed_samples"] == 1


def test_groups_statuses_by_truth_and_vegetation_class() -> None:
    samples = [
        row(maintenance_truth="cut", vegetation_class="low_grass"),
        row(maintenance_truth="no_cut", vegetation_class="tree"),
    ]
    result = runner(
        FakeProvider(
            [
                evidence(),
                evidence(
                    vv=(-7.0, -8.0, -9.0, -10.0),
                    vh=(-13.0, -14.0, -15.0, -16.0),
                ),
            ]
        )
    ).run(samples)

    cut = result["summary"]["by_maintenance_truth"]["cut"]
    no_cut = result["summary"]["by_maintenance_truth"]["no_cut"]
    assert cut["statuses"]["increasing"] == {"count": 1, "percentage": 100.0}
    assert no_cut["statuses"]["decreasing"]["count"] == 1
    assert result["summary"]["by_vegetation_class"]["low_grass"]["sample_count"] == 1
    assert result["summary"]["by_vegetation_class"]["tree"]["statuses"]["decreasing"]["count"] == 1


def test_s2_disagreements_include_ground_truth_and_s1_temporal_metrics() -> None:
    sample = row(maintenance_truth="cut", s2_decision="nao_cortar")
    result = runner(FakeProvider([evidence()])).run([sample])

    disagreement = result["s2_disagreements"][0]
    assert disagreement["ground_truth"] == "cut"
    assert disagreement["s2_decision"] == "nao_cortar"
    assert disagreement["s1_temporal_status"] == "increasing"
    assert disagreement["vv_slope"] == pytest.approx(0.1)
    assert disagreement["vv_modeled_change_db"] == pytest.approx(3.0)


def test_orbits_53_and_126_have_separate_distributions_and_small_group_alerts() -> None:
    result = runner(FakeProvider([evidence(orbit=53), evidence(orbit=126)])).run(
        [row(), row()]
    )
    orbit = result["orbit_check"]

    assert orbit["by_canonical_relative_orbit"]["53"]["sample_count"] == 1
    assert orbit["by_canonical_relative_orbit"]["126"]["sample_count"] == 1
    assert "canonical_orbit_group_small:53" in orbit["warnings"]
    assert "canonical_orbit_group_small:126" in orbit["warnings"]
    assert orbit["orbital_correction_applied"] is False


def test_benchmark_does_not_change_or_emit_recommendation() -> None:
    sample = row(s2_decision="nao_cortar")
    original = dict(sample)

    result = runner(FakeProvider([evidence()])).run([sample])

    assert sample == original
    assert "recommendation" not in result
    assert result["methodology"]["recommendation_changed"] is False
    assert result["methodology"]["fusion_applied"] is False


def test_get_validation_temporal_benchmark_endpoint(
    client: TestClient, tmp_path: Path
) -> None:
    repository = ValidationSampleRepository(tmp_path / "validation.sqlite3")
    benchmark_runner = runner(FakeProvider([]))
    app.dependency_overrides[get_validation_repository] = lambda: repository
    app.dependency_overrides[get_validation_temporal_benchmark_runner] = (
        lambda: benchmark_runner
    )

    response = client.get("/api/validation-temporal-benchmark")

    assert response.status_code == 200
    assert response.json()["summary"]["total_samples"] == 0
    assert response.json()["methodology"]["fusion_applied"] is False
