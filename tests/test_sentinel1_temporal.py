"""Offline tests for calibrated canonical-orbit temporal evidence."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.multisource.models import EvidenceObservation, SourceEvidence, EvidenceStatus
from src.satellite_monitoring.multisource.runtime import collect_multisource_evidence, write_multisource_evidence
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig, analyze_sentinel1_temporal


def obs(day, vv=-10.0, vh=-16.0, orbit=53, **metrics):
    return EvidenceObservation(datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=day), {
        "relative_orbit": orbit, "vv_sigma0_median_db": vv, "vh_sigma0_median_db": vh,
        "vv_radiometric_calibration_status": "calibrated",
        "vh_radiometric_calibration_status": "calibrated", **metrics})


def evidence(observations):
    return SourceEvidence(source="sentinel-1", status=EvidenceStatus.AVAILABLE,
                          observations=tuple(observations), metrics={"canonical_relative_orbit": 53})


def analyze(observations, **config):
    return analyze_sentinel1_temporal(evidence(observations), Sentinel1TemporalConfig(enabled=True, **config))


@pytest.mark.parametrize("slope,status", [(0.1, "increasing"), (-0.1, "decreasing"), (0, "stable")])
def test_theil_sen_chronological(slope, status):
    result = analyze([obs(day, -10 + slope * day, -16 + slope * day) for day in [24, 0, 18, 6]])
    assert result["vv"]["theil_sen_slope_db_per_day"] == pytest.approx(slope)
    assert result["vv"]["modeled_change_db"] == pytest.approx(slope * 24)
    assert result["vv"]["endpoint_delta_db"] == pytest.approx(slope * 24)
    assert result["vv"]["pairwise_direction_agreement"] == 1
    assert result["combined_status"] == status
    assert result["vv"]["residual_mad_db"] == pytest.approx(0)


def test_daily_median_utc_and_item_counts():
    shifted = replace(obs(0, 6), observed_at=datetime(2026, 8, 2, 1, tzinfo=timezone(timedelta(hours=3))))
    result = analyze([obs(0, 2), obs(0, 4), shifted, obs(6), obs(18), obs(24)])
    assert result["series"][0]["day"] == "2026-08-01"
    assert result["series"][0]["vv_sigma0_median_db"] == 4
    assert result["series"][0]["item_count"] == 3
    assert result["series"][0]["vv_item_count"] == 3
    assert result["vv"]["observation_count"] == 4


def test_raw_other_orbits_and_invalid_calibration_ignored():
    base = [obs(day) for day in [0, 6, 18, 24]]
    result = analyze(base + [obs(30, 1000, 1000, orbit=126), obs(36, 1000, 1000,
        vv_radiometric_calibration_status="failed", vh_radiometric_calibration_status="unavailable",
        vv_amplitude_median=999999)])
    assert result["vv"]["span_days"] == 24
    assert result["vv"]["observation_count"] == 4
    assert result["combined_status"] == "stable"
    assert result["provenance"]["raw_amplitude_used"] is False
    assert result["provenance"]["cross_orbit_temporal_mixing"] is False
    assert len(result["series"]) == 5


def test_outlier_resistance_and_residual_mad():
    result = analyze([obs(day, value) for day, value in [(0, 0), (6, 0.6), (12, 99), (18, 1.8), (24, 2.4)]])
    assert result["vv"]["theil_sen_slope_db_per_day"] == pytest.approx(0.1)
    assert result["vv"]["residual_mad_db"] == pytest.approx(0)
    noisy = analyze([obs(day, value) for day, value in [(0, 0), (6, 2), (12, 1), (18, 3)]])
    # Pair slopes median = 1/8; residuals = [-3/8, 7/8, -7/8, 3/8].
    assert noisy["vv"]["residual_mad_db"] == pytest.approx(0.625)
    assert noisy["vv"]["effective_change_threshold_db"] == pytest.approx(1.25)


@pytest.mark.parametrize("days", [[], [0], [0, 12, 24], [0, 1, 2, 3]])
def test_insufficient_data(days):
    result = analyze([obs(day) for day in days])
    assert result["combined_status"] == "insufficient_data"
    assert result["vv"]["theil_sen_slope_db_per_day"] is None


def test_channel_status_independent_mixed_and_missing():
    rows = [obs(day, -10 + day / 10, -16 - day / 10) for day in [0, 6, 18, 24]]
    assert analyze(rows)["combined_status"] == "mixed"
    rows[0] = obs(0, vh_radiometric_calibration_status="failed")
    result = analyze(rows)
    assert result["vv"]["status"] == "increasing"
    assert result["vh"]["status"] == "insufficient_data"
    assert result["combined_status"] == "insufficient_data"


def test_nonfinite_and_missing_status_do_not_enter_series():
    result = analyze([obs(0, float("nan"), float("inf")), obs(6), obs(18), obs(24)])
    assert result["vv"]["observation_count"] == 3
    json.dumps(result, allow_nan=False)
    missing = analyze([obs(day, vv_radiometric_calibration_status=None) for day in [0, 6, 18, 24]])
    assert missing["vv"]["observation_count"] == 0


def test_disabled_and_unknown_orbit():
    source = evidence([obs(day) for day in [0, 6, 18, 24]])
    result = analyze_sentinel1_temporal(source, Sentinel1TemporalConfig())
    assert result["status"] == "disabled"
    assert result["series"] == []
    result = analyze_sentinel1_temporal(replace(source, metrics={}), Sentinel1TemporalConfig(enabled=True))
    assert result["combined_status"] == "insufficient_data"


def test_environment(monkeypatch):
    monkeypatch.setenv("SENTINEL1_TEMPORAL_ENABLED", "true")
    monkeypatch.setenv("SENTINEL1_TEMPORAL_MIN_SPAN_DAYS", "20")
    config = Sentinel1TemporalConfig.from_environment()
    assert config.enabled and config.min_span_days == 20
    with pytest.raises(ValueError):
        Sentinel1TemporalConfig(min_total_change_db=float("nan"))


def test_exact_threshold_and_numeric_overflow_are_safe():
    result = analyze([obs(day, day / 24) for day in [0, 6, 18, 24]])
    assert result["vv"]["status"] == "increasing"
    result = analyze([obs(0, -1e308), obs(1, 1e308)])
    assert result["vv"]["status"] == "insufficient_data"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_shadow_serialization_and_artifact(enabled, tmp_path, monkeypatch):
    source = evidence([obs(day) for day in [0, 6, 18, 24]])
    class Provider:
        def __init__(self, **kwargs): pass
        def collect_evidence(self, *args): return source
    config = MonitoringConfig(geometry={"type": "Polygon", "coordinates": []},
        start_date=date(2026, 8, 1), end_date=date(2026, 9, 1),
        multisource_enabled=True, multisource_fusion_mode="shadow", sentinel1_enabled=True,
        sentinel1_temporal=Sentinel1TemporalConfig(enabled=enabled))
    result = collect_multisource_evidence(config, config.geometry, provider_factory=Provider)
    assert result["official_recommendation_changed"] is False
    assert result["fusion_mode"] == "shadow"
    metrics = result["sources"][0]["metrics"]
    assert metrics["canonical_relative_orbit"] == 53
    assert metrics["temporal_analysis"]["enabled"] == enabled
    assert "temporal_analysis" not in source.metrics
    artifact = write_multisource_evidence(tmp_path, result)
    assert json.loads(artifact.read_text())["sources"][0]["metrics"] == metrics
    def fail(*args):
        raise RuntimeError("synthetic temporal error")
    monkeypatch.setattr("src.satellite_monitoring.multisource.runtime.analyze_sentinel1_temporal", fail)
    failed = collect_multisource_evidence(config, config.geometry, provider_factory=Provider)
    assert failed["sources"][0]["status"] == "available"
    assert failed["sources"][0]["observations"] == source.to_dict()["observations"]
    assert failed["sources"][0]["metrics"]["temporal_analysis"]["status"] == "error"
