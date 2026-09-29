from __future__ import annotations

from copy import deepcopy
from datetime import date
import logging

import pytest

from src.satellite_monitoring.multisource.experimental_fusion import (
    EXPERIMENTAL_FUSION_POLICY,
    ExperimentalFusionPolicyV1,
    attach_experimental_fusion,
)
from src.satellite_monitoring.multisource.operational_fusion import (
    OperationalFusionPolicy,
)
from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.multisource.models import EvidenceStatus, SourceEvidence
from src.satellite_monitoring.multisource.runtime import collect_multisource_evidence
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig


def _source(
    temporal_status="mixed",
    *,
    availability="available",
    temporal_analysis_status="completed",
    temporal_enabled=True,
    calibrated_observation_count=4,
):
    return {
        "source": "sentinel-1",
        "status": availability,
        "metrics": {
            "calibrated_observation_count": calibrated_observation_count,
            "temporal_analysis": {
                "enabled": temporal_enabled,
                "status": temporal_analysis_status,
                "combined_status": temporal_status,
            },
        },
    }


def _evaluate(decision, temporal_status="mixed", *, mode="experimental", **options):
    return ExperimentalFusionPolicyV1().evaluate(
        fusion_mode=mode,
        official_recommendation={"recommendation": decision},
        sources=[_source(temporal_status, **options)],
    )


def test_rule_b_conservatively_changes_cut_to_experimental_inconclusive():
    result = _evaluate("cortar", "mixed")
    assert result["fusion_policy"] == EXPERIMENTAL_FUSION_POLICY == "experimental_v1"
    assert result["sentinel2_recommendation"] == "cortar"
    assert result["multisource_recommendation"] == "inconclusivo"
    assert result["sentinel1_influenced_decision"] is True
    assert result["fusion_rule"] == "B"
    assert result["fusion_reason"] == "sentinel1_temporal_mixed_with_sentinel2_cut"
    assert result["experimental"] is True
    assert result["operationally_authorized"] is False


@pytest.mark.parametrize("temporal_status", ["increasing", "stable", "decreasing"])
def test_cut_with_any_status_outside_rule_b_remains_cut(temporal_status):
    result = _evaluate("cortar", temporal_status)
    assert result["multisource_recommendation"] == "cortar"
    assert result["sentinel1_influenced_decision"] is False
    assert result["fusion_rule"] is None


@pytest.mark.parametrize("temporal_status", ["increasing", "mixed"])
def test_no_cut_is_never_changed_by_experimental_v1(temporal_status):
    result = _evaluate("nao_cortar", temporal_status)
    assert result["multisource_recommendation"] == "nao_cortar"
    assert result["sentinel1_influenced_decision"] is False


def test_s2_inconclusive_remains_inconclusive():
    result = _evaluate("inconclusivo", "mixed")
    assert result["multisource_recommendation"] == "inconclusivo"
    assert result["sentinel1_influenced_decision"] is False


@pytest.mark.parametrize(
    ("options", "expected_reason"),
    [
        ({"availability": "unavailable"}, "sentinel1_unavailable"),
        ({"availability": "error"}, "sentinel1_error"),
        (
            {
                "temporal_analysis_status": "insufficient_data",
                "temporal_status": "insufficient_data",
            },
            "sentinel1_temporal_insufficient_data",
        ),
        ({"calibrated_observation_count": 0}, "sentinel1_calibration_unavailable"),
    ],
)
def test_s1_fail_soft_states_preserve_sentinel2(options, expected_reason):
    options = dict(options)
    temporal_status = options.pop("temporal_status", "mixed")
    result = _evaluate("cortar", temporal_status, **options)
    assert result["multisource_recommendation"] == "cortar"
    assert result["sentinel1_influenced_decision"] is False
    assert result["experimental_fusion_evaluable"] is False
    assert result["fusion_not_evaluable_reason"] == expected_reason


def test_missing_s1_preserves_sentinel2():
    result = ExperimentalFusionPolicyV1().evaluate(
        fusion_mode="experimental",
        official_recommendation={"recommendation": "cortar"},
        sources=[],
    )
    assert result["multisource_recommendation"] == "cortar"
    assert result["fusion_not_evaluable_reason"] == "sentinel1_evidence_missing"


@pytest.mark.parametrize("mode", ["disabled", "shadow", "operational"])
def test_non_experimental_modes_never_apply_experimental_policy(mode):
    result = _evaluate("cortar", "mixed", mode=mode)
    assert result["multisource_recommendation"] == "cortar"
    assert result["sentinel1_influenced_decision"] is False
    assert result["fusion_policy"] is None
    assert result["experimental"] is False
    assert result["operationally_authorized"] is False
    assert result["experimental_fusion_evaluated"] is False


def test_attachment_is_additive_and_does_not_mutate_official_recommendation():
    multisource = {"fusion_mode": "experimental", "sources": [_source("mixed")]}
    recommendation = {"recommendation": "cortar", "confidence": "high"}
    originals = deepcopy((multisource, recommendation))
    result = attach_experimental_fusion(multisource, recommendation)
    assert result["experimental_fusion"]["multisource_recommendation"] == "inconclusivo"
    assert result["official_recommendation_changed"] is False
    assert (multisource, recommendation) == originals


def test_benchmark_equivalent_false_positive_is_only_made_inconclusive():
    # Ground truth is intentionally absent from production policy inputs.
    result = _evaluate("cortar", "mixed")
    assert result["multisource_recommendation"] == "inconclusivo"
    assert result["multisource_recommendation"] != "nao_cortar"


def test_benchmark_equivalent_false_negative_is_unchanged_without_rule_a():
    result = _evaluate("nao_cortar", "increasing")
    assert result["multisource_recommendation"] == "nao_cortar"
    assert result["sentinel1_influenced_decision"] is False


def test_runtime_contains_only_rule_b_and_no_operational_policy_implementation():
    source = repr(ExperimentalFusionPolicyV1.evaluate.__code__.co_consts)
    assert "A+B" not in source
    assert ExperimentalFusionPolicyV1.__mro__[1] is object
    assert OperationalFusionPolicy not in ExperimentalFusionPolicyV1.__mro__


def test_structured_observability_contains_no_scientific_labels(caplog):
    with caplog.at_level(
        logging.INFO,
        logger="src.satellite_monitoring.multisource.experimental_fusion",
    ):
        _evaluate("cortar", "mixed")
    record = caplog.records[-1]
    assert record.experimental_fusion_evaluated is True
    assert record.experimental_fusion_triggered is True
    assert record.experimental_fusion_rule == "B"
    assert record.sentinel2_recommendation == "cortar"
    assert record.multisource_recommendation == "inconclusivo"
    assert record.sentinel1_influenced_decision is True
    assert not hasattr(record, "ground_truth")
    assert not hasattr(record, "geometry")


def test_experimental_mode_collects_sentinel1_evidence(tmp_path):
    class Provider:
        def __init__(self, **kwargs):
            pass

        def collect_evidence(self, *args):
            return SourceEvidence(
                source="sentinel-1",
                status=EvidenceStatus.UNAVAILABLE,
            )

    config = MonitoringConfig(
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]],
        },
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        output_root=tmp_path,
        multisource_enabled=True,
        sentinel1_enabled=True,
        sentinel1_temporal=Sentinel1TemporalConfig(enabled=False),
        multisource_fusion_mode="experimental",
    )
    result = collect_multisource_evidence(
        config,
        config.geometry,
        provider_factory=Provider,
    )
    assert result is not None
    assert result["fusion_mode"] == "experimental"
    assert result["sources"][0]["source"] == "sentinel-1"
