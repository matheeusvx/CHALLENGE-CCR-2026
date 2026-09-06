from dataclasses import replace
from datetime import datetime, timedelta, timezone

from apps.api.app.dependencies import get_analysis_service
from apps.api.app.main import app
from apps.api.tests.conftest import make_result
from src.satellite_monitoring.multisource.models import EvidenceObservation, EvidenceStatus, SourceEvidence
from src.satellite_monitoring.multisource.runtime import collect_multisource_evidence
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
