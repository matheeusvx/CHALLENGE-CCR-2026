from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import date

import pytest

from apps.api.app.validation.holdout_benchmark import (
    build_validation_holdout_benchmark_v1,
    create_preregistration,
    freeze_preregistration,
)
from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.multisource.operational_fusion import (
    OperationalFusionAuthorization,
    attach_operational_fusion_audit,
)
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig


def _config(tmp_path, *, mode="operational", path=None, **flags):
    return MonitoringConfig(
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]},
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        multisource_enabled=flags.get("multisource_enabled", True),
        sentinel1_enabled=flags.get("sentinel1_enabled", True),
        sentinel1_temporal=Sentinel1TemporalConfig(
            enabled=flags.get("temporal_enabled", True)
        ),
        multisource_fusion_mode=mode,
        validation_holdout_benchmark_path=path,
        output_root=tmp_path,
    )


def _rows(count=60):
    rows = []
    for index in range(count):
        if index < 10:
            truth, decision = "no_cut", "cortar"
        elif index < 35:
            truth, decision = "cut", "cortar"
        else:
            truth, decision = "no_cut", "nao_cortar"
        rows.append({
            "sample_id": f"sample-{index:03d}",
            "analysis_id": f"analysis-{index:03d}",
            "aoi_fingerprint": f"aoi-{index:03d}",
            "cohort": "holdout",
            "schema_version": 1,
            "created_at": "2026-09-01T00:00:00+00:00",
            "vegetation_class": "mixed",
            "maintenance_truth": truth,
            "reference_date": "2026-09-01",
            "s2_decision": decision,
            "s2_confidence": "high",
        })
    return rows


def _artifact(rows, *, false_reviews=0):
    prereg = freeze_preregistration(
        create_preregistration(created_at="2026-08-01T00:00:00+00:00"),
        rows,
        frozen_at="2026-08-02T00:00:00+00:00",
    )
    temporal = []
    correct_cut_seen = 0
    for row in rows:
        is_error = row["maintenance_truth"] == "no_cut" and row["s2_decision"] == "cortar"
        status = "mixed" if is_error else "stable"
        if not is_error and row["s2_decision"] == "cortar" and correct_cut_seen < false_reviews:
            status = "mixed"
            correct_cut_seen += 1
        temporal.append({
            "sample_id": row["sample_id"],
            "processing_status": "completed",
            "combined_status": status,
            "canonical_relative_orbit": 53,
        })
    return build_validation_holdout_benchmark_v1(
        rows,
        prereg,
        {"samples": temporal},
        generated_at="2026-09-07T00:00:00+00:00",
    )


def _write(tmp_path, artifact, name="holdout.json"):
    path = tmp_path / name
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_non_operational_modes_are_not_authorized(tmp_path, mode):
    result = OperationalFusionAuthorization().authorize(_config(tmp_path, mode=mode))
    assert result["requested"] is False
    assert result["authorized"] is False
    assert result["authorization_status"] == "not_requested"


def test_operational_missing_and_corrupt_artifacts_fail_closed(tmp_path):
    missing = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=tmp_path / "missing.json")
    )
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{broken", encoding="utf-8")
    corrupt = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=corrupt_path)
    )
    assert missing["authorization_reason"] == "holdout_artifact_not_found"
    assert corrupt["authorization_reason"] == "holdout_artifact_invalid"
    assert not missing["authorized"] and not corrupt["authorized"]


@pytest.mark.parametrize(
    ("rows", "false_reviews", "expected_gate"),
    [
        (_rows(49), 0, "INSUFFICIENT_HOLDOUT_DATA"),
        (_rows(), 15, "SHADOW_REVIEW_CANDIDATE"),
    ],
)
def test_non_review_holdout_status_never_authorizes(
    tmp_path, rows, false_reviews, expected_gate
):
    artifact = _artifact(rows, false_reviews=false_reviews)
    assert artifact["recommendation_gate"]["status"] == expected_gate
    result = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, artifact, expected_gate + ".json"))
    )
    assert result["authorized"] is False
    assert result["authorization_reason"] == "holdout_gate_not_satisfied"
    assert result["holdout_gate_status"] == expected_gate


def test_synthetic_review_candidate_authorizes_layer_but_no_policy_or_override(tmp_path):
    artifact = _artifact(_rows())
    assert artifact["recommendation_gate"]["status"] == "REVIEW_MODE_CANDIDATE"
    authorization = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, artifact))
    )
    recommendation = {"recommendation": "cortar", "confidence": "high"}
    original = deepcopy(recommendation)
    audit = attach_operational_fusion_audit(
        {"fusion_mode": "operational", "sources": []},
        recommendation,
        authorization,
    )
    assert authorization["authorized"] is True
    assert audit["operational_fusion"]["policy_available"] is False
    assert audit["operational_fusion"]["original_recommendation"] == "cortar"
    assert audit["operational_fusion"]["final_recommendation"] == "cortar"
    assert audit["operational_fusion"]["official_recommendation_changed"] is False
    assert recommendation == original


@pytest.mark.parametrize(
    "flags",
    [
        {"multisource_enabled": False},
        {"sentinel1_enabled": False},
        {"temporal_enabled": False},
    ],
)
def test_operational_requires_all_feature_flags(tmp_path, flags):
    result = OperationalFusionAuthorization().authorize(_config(tmp_path, **flags))
    assert result["requested"] is True
    assert result["authorized"] is False
    assert result["authorization_reason"] == "required_feature_flags_not_enabled"


def test_tampered_fingerprint_is_rejected(tmp_path):
    artifact = _artifact(_rows())
    artifact["input_fingerprints"]["validation_holdout_sha256"] = "0" * 64
    result = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, artifact))
    )
    assert result["authorized"] is False
    assert result["authorization_reason"] == "holdout_artifact_invalid"


def test_textual_review_status_cannot_bypass_recomputed_metrics(tmp_path):
    artifact = _artifact(_rows(), false_reviews=15)
    artifact["recommendation_gate"]["status"] = "REVIEW_MODE_CANDIDATE"
    artifact["recommendation_gate"]["requirements"] = {
        key: True for key in artifact["recommendation_gate"]["requirements"]
    }
    result = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, artifact))
    )
    assert result["authorized"] is False
    assert result["authorization_reason"] == "holdout_gate_not_satisfied"


def test_integrity_warning_blocks_even_an_otherwise_valid_gate(tmp_path):
    artifact = _artifact(_rows())
    artifact["warnings"].append("integrity_check_failed")
    result = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, artifact))
    )
    assert result["authorized"] is False
    assert result["authorization_reason"] == "holdout_integrity_warning"


def test_empty_real_holdout_shape_cannot_change_recommendation(tmp_path):
    authorization = OperationalFusionAuthorization().authorize(
        _config(tmp_path, path=_write(tmp_path, _artifact([])))
    )
    result = attach_operational_fusion_audit(
        {"fusion_mode": "operational", "sources": []},
        {"recommendation": "nao_cortar"},
        authorization,
    )
    assert authorization["authorized"] is False
    assert authorization["holdout_gate_status"] == "INSUFFICIENT_HOLDOUT_DATA"
    assert result["operational_fusion"]["final_recommendation"] == "nao_cortar"
    assert result["operational_fusion"]["official_recommendation_changed"] is False


def test_operational_audit_emits_structured_observability(caplog):
    with caplog.at_level(
        logging.INFO,
        logger="src.satellite_monitoring.multisource.operational_fusion",
    ):
        attach_operational_fusion_audit(
            {"fusion_mode": "operational", "sources": []},
            {"recommendation": "inconclusivo"},
            {
                "requested": True,
                "authorized": False,
                "authorization_status": "denied",
                "authorization_reason": "holdout_gate_not_satisfied",
                "holdout_schema_version": "1.0",
                "holdout_gate_status": "INSUFFICIENT_HOLDOUT_DATA",
                "candidate_rule": "B",
            },
        )
    record = caplog.records[-1]
    assert record.operational_fusion_requested is True
    assert record.operational_fusion_authorized is False
    assert record.policy_available is False
    assert record.original_recommendation == record.final_recommendation == "inconclusivo"
    assert record.official_recommendation_changed is False
