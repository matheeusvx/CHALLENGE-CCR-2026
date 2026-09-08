from dataclasses import replace
from datetime import datetime, timedelta, timezone

from apps.api.app.dependencies import get_analysis_service
from apps.api.app.main import app
from apps.api.tests.conftest import make_result
from src.satellite_monitoring.multisource.models import EvidenceObservation, EvidenceStatus, SourceEvidence
from src.satellite_monitoring.multisource.runtime import collect_multisource_evidence
from src.satellite_monitoring.multisource.shadow_review import attach_shadow_review
from src.satellite_monitoring.multisource.operational_fusion import (
    attach_operational_fusion_audit,
)
from src.satellite_monitoring.sentinel1_temporal import Sentinel1TemporalConfig


def test_temporal_flag_api_serialization_and_identical_recommendation(client, valid_payload):
    source = SourceEvidence(source="sentinel-1", status=EvidenceStatus.AVAILABLE,
        metrics={"canonical_relative_orbit": 53}, observations=tuple(
            EvidenceObservation(datetime(2026, 7, 10, tzinfo=timezone.utc) + timedelta(days=day), {
                "relative_orbit": 53,
                "vv_radiometric_calibration_status": "calibrated",
                "vh_radiometric_calibration_status": "calibrated",
                "vv_sigma0_median_db": -12 + day / 10,
                "vh_sigma0_median_db": -18 + day / 10,
            }) for day in [0, 6, 18, 24]))

    class Provider:
        def __init__(self, **kwargs):
            pass

        def collect_evidence(self, *args):
            return source

    responses = []
    for enabled in [False, True]:
        def service(config, *, analysis_id, **kwargs):
            config = replace(config, multisource_enabled=True, sentinel1_enabled=True,
                multisource_fusion_mode="shadow", sentinel1_temporal=Sentinel1TemporalConfig(enabled=enabled))
            result = make_result(analysis_id)
            result.multisource = collect_multisource_evidence(config, {}, provider_factory=Provider)
            return result

        app.dependency_overrides[get_analysis_service] = lambda: service
        response = client.post("/api/analyses/run", json=valid_payload)
        assert response.status_code == 200
        responses.append(response.json())
    assert responses[0]["recommendation"] == responses[1]["recommendation"]
    multisource = responses[1]["multisource"]
    assert multisource["fusion_mode"] == "shadow"
    assert multisource["official_recommendation_changed"] is False
    assert multisource["sources"][0]["metrics"]["temporal_analysis"]["combined_status"] == "increasing"


def test_shadow_review_rule_b_is_additive_in_api(client, valid_payload):
    source = SourceEvidence(
        source="sentinel-1",
        status=EvidenceStatus.AVAILABLE,
        metrics={"canonical_relative_orbit": 53, "calibrated_observation_count": 4},
        observations=tuple(
            EvidenceObservation(
                datetime(2026, 7, 10, tzinfo=timezone.utc) + timedelta(days=day),
                {
                    "relative_orbit": 53,
                    "vv_radiometric_calibration_status": "calibrated",
                    "vh_radiometric_calibration_status": "calibrated",
                    "vv_sigma0_median_db": -12 + day / 10,
                    "vh_sigma0_median_db": -18 - day / 10,
                },
            )
            for day in [0, 6, 18, 24]
        ),
    )

    class Provider:
        def __init__(self, **kwargs):
            pass

        def collect_evidence(self, *args):
            return source

    def service(config, *, analysis_id, **kwargs):
        config = replace(
            config,
            multisource_enabled=True,
            sentinel1_enabled=True,
            multisource_fusion_mode="shadow",
            sentinel1_temporal=Sentinel1TemporalConfig(enabled=True),
        )
        result = make_result(analysis_id)
        result.recommendation["recommendation"] = "cortar"
        evidence = collect_multisource_evidence(config, {}, provider_factory=Provider)
        result.multisource = attach_shadow_review(evidence, result.recommendation)
        return result

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json=valid_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"]["decision"] == "cortar"
    assert body["multisource"]["review"]["review_recommended"] is True
    assert body["multisource"]["review"]["review_rule"] == "B"
    assert body["multisource"]["review"]["official_recommendation_changed"] is False


def test_operational_fusion_audit_is_additive_and_api_compatible(client, valid_payload):
    def service(config, *, analysis_id, **kwargs):
        result = make_result(analysis_id)
        result.multisource = attach_operational_fusion_audit(
            {
                "enabled": True,
                "fusion_mode": "operational",
                "official_recommendation_changed": False,
                "generated_at": "2026-09-08T00:00:00+00:00",
                "configuration": {},
                "sources": [],
            },
            result.recommendation,
            {
                "requested": True,
                "authorized": False,
                "authorization_status": "denied",
                "authorization_reason": "holdout_gate_not_satisfied",
                "holdout_schema_version": "1.0",
                "holdout_gate_status": "SHADOW_REVIEW_CANDIDATE",
                "candidate_rule": "B",
            },
        )
        return result

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json=valid_payload)

    assert response.status_code == 200
    body = response.json()
    audit = body["multisource"]["operational_fusion"]
    assert audit["requested"] is True
    assert audit["authorized"] is False
    assert audit["policy_available"] is False
    assert audit["original_recommendation"] == body["recommendation"]["decision"]
    assert audit["final_recommendation"] == body["recommendation"]["decision"]
    assert audit["official_recommendation_changed"] is False
