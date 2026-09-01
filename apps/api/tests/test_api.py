from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import analysis_registry, get_analysis_service
from apps.api.app.main import app
from apps.api.app.operational_profile import DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
from apps.api.tests.conftest import VALID_GEOMETRY, make_result


def test_healthcheck(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "motiva-vegetation-api",
        "version": "0.1.0",
    }


def test_valid_geometry_uses_real_geometry_rules(client: TestClient) -> None:
    response = client.post("/api/analyses/validate-geometry", json={"geometry": VALID_GEOMETRY})
    body = response.json()
    assert response.status_code == 200
    assert body["valid"] is True
    assert body["geometry_type"] == "Polygon"
    assert body["area_square_meters"] > 0
    assert body["estimated_sentinel_pixels"] > 0


def test_invalid_geometry_has_structured_error(client: TestClient) -> None:
    response = client.post(
        "/api/analyses/validate-geometry",
        json={"geometry": {"type": "Point", "coordinates": [-46.96, -23.10]}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_GEOMETRY"


def test_rejects_reversed_date_range(client: TestClient, valid_payload: dict) -> None:
    valid_payload.update({"start_date": "2026-08-04", "end_date": "2026-05-01"})
    response = client.post("/api/analyses/run", json=valid_payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_rejects_incomplete_payload(client: TestClient) -> None:
    response = client.post("/api/analyses/run", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PARAMETERS"


def test_rejects_parameters_outside_limits(client: TestClient, valid_payload: dict) -> None:
    valid_payload["max_cloud_cover"] = 101
    response = client.post("/api/analyses/run", json=valid_payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PARAMETERS"


def test_run_uses_injected_service(
    client: TestClient,
    valid_payload: dict,
    injected_success: str,
) -> None:
    response = client.post("/api/analyses/run", json=valid_payload)
    body = response.json()
    assert response.status_code == 200
    assert body["analysis_id"]
    assert body["recommendation"]["decision"] == "nao_cortar"
    assert body["recommendation"]["confidence"] == "high"
    assert body["height_estimation"] == {
        "status": "disabled",
        "estimated_class": None,
        "score_gt_30_cm": None,
        "probability_gt_30_cm": None,
        "calibration_status": "uncalibrated",
        "vegetation_fraction": None,
        "height_valid_pixel_count": None,
        "height_total_pixel_count": None,
        "mixed_pixel_risk": None,
        "confidence": None,
        "reference_threshold_cm": 30,
        "model_version": None,
        "provenance": None,
    }
    assert body["analysis_period"] == {
        "start_date": "2026-05-01",
        "end_date": "2026-08-04",
        "timezone": "America/Sao_Paulo",
        "strategy": "explicit",
    }
    assert "output_root" not in body["summary"]["parameters"]


def test_analysis_response_exposes_optional_spatial_accounting(
    client: TestClient, valid_payload: dict
) -> None:
    def service(config, *, analysis_id: str, **__):
        result = make_result(analysis_id)
        result.selected_area_m2 = 4419.4683
        result.effective_analysis_area_m2 = 4113.2001
        result.effective_analysis_pct = 93.0700
        return result

    app.dependency_overrides[get_analysis_service] = lambda: service
    body = client.post("/api/analyses/run", json=valid_payload).json()

    assert body["selected_area_m2"] == pytest.approx(4419.4683)
    assert body["effective_analysis_area_m2"] == pytest.approx(4113.2001)
    assert body["effective_analysis_pct"] == pytest.approx(93.0700)
    assert body["recommendation"]["decision"] == "nao_cortar"


def test_analysis_response_remains_compatible_without_spatial_accounting(
    client: TestClient, valid_payload: dict, injected_success: str
) -> None:
    body = client.post("/api/analyses/run", json=valid_payload).json()

    assert body["selected_area_m2"] is None
    assert body["effective_analysis_area_m2"] is None
    assert body["effective_analysis_pct"] is None


@pytest.mark.parametrize(
    "height_estimation",
    [
        {
            "status": "experimental",
            "estimated_class": "le_30_cm",
            "score_gt_30_cm": 0.2,
            "probability_gt_30_cm": 0.2,
            "calibration_status": "uncalibrated",
            "vegetation_fraction": 0.8,
            "height_valid_pixel_count": 80,
            "height_total_pixel_count": 100,
            "mixed_pixel_risk": "low",
            "confidence": "medium",
            "reference_threshold_cm": 30,
            "model_version": "height-estimator-v0",
            "provenance": {"feature_pipeline": "height_valid_mask_v1"},
        },
        {
            "status": "experimental",
            "estimated_class": "inconclusive",
            "probability_gt_30_cm": 0.5,
            "confidence": "low",
            "reference_threshold_cm": 30,
            "model_version": "height-estimator-v0",
        },
        {
            "status": "unavailable",
            "estimated_class": None,
            "probability_gt_30_cm": None,
            "confidence": None,
            "reference_threshold_cm": 30,
            "model_version": None,
        },
        {
            "status": "disabled",
            "estimated_class": None,
            "probability_gt_30_cm": None,
            "confidence": None,
            "reference_threshold_cm": 30,
            "model_version": None,
        },
    ],
)
def test_height_estimation_contract_is_additive(
    client: TestClient, valid_payload: dict, height_estimation: dict
) -> None:
    def service(config, *, analysis_id: str, **__):
        result = make_result(analysis_id)
        result.height_estimation = height_estimation
        return result

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json=valid_payload)

    assert response.status_code == 200
    returned = response.json()["height_estimation"]
    for key, value in height_estimation.items():
        assert returned[key] == value
    assert "score_gt_30_cm" in returned
    assert "calibration_status" in returned
    assert "vegetation_fraction" in returned
    assert "mixed_pixel_risk" in returned
    assert response.json()["recommendation"]["decision"] == "nao_cortar"


def test_run_without_technical_parameters_applies_operational_profile(
    client: TestClient,
) -> None:
    captured = {}

    def service(config, *, analysis_id: str, **__):
        captured["config"] = config
        return make_result(analysis_id)

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json={"geometry": VALID_GEOMETRY})

    assert response.status_code == 200
    config = captured["config"]
    profile = DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
    assert config.start_date.isoformat() == "2026-07-10"
    assert config.end_date.isoformat() == "2026-08-10"
    assert config.max_cloud_cover == profile.max_cloud_cover == 30
    assert config.max_scenes == profile.max_scenes == 12
    assert config.max_candidate_scenes == profile.max_candidate_scenes == 40
    assert config.scene_order == profile.scene_order == "newest"
    assert config.min_valid_pixel_percentage == profile.min_valid_pixel_percentage == 70
    assert config.min_valid_pixel_count == profile.min_valid_pixel_count == 30
    assert (
        config.min_aoi_coverage_percentage
        == profile.min_aoi_coverage_percentage
        == 95
    )
    assert config.min_observations == profile.min_observations == 4
    assert config.daily_aggregation == profile.daily_aggregation == "best"
    assert config.decision_min_observations == profile.decision_min_observations == 4
    assert config.high_vegetation_percentile == profile.high_vegetation_percentile == 75
    assert config.significant_drop_absolute == profile.significant_drop_absolute == 0.06
    assert (
        config.significant_drop_relative_percentage
        == profile.significant_drop_relative_percentage
        == 15
    )
    assert config.trend_window == profile.trend_window == 3
    assert config.max_gap_days == profile.max_gap_days == 20
    assert config.recent_intervention_days == profile.recent_intervention_days == 20
    assert response.json()["analysis_period"] == {
        "start_date": "2026-07-10",
        "end_date": "2026-08-10",
        "timezone": "America/Sao_Paulo",
        "strategy": "previous_calendar_month",
    }


def test_rejects_partial_explicit_date_range(client: TestClient) -> None:
    response = client.post(
        "/api/analyses/run",
        json={"geometry": VALID_GEOMETRY, "start_date": "2026-07-10"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_pipeline_error_is_controlled(client: TestClient, valid_payload: dict) -> None:
    def failing_service(*_, **__):
        raise RuntimeError("internal provider detail")

    app.dependency_overrides[get_analysis_service] = lambda: failing_service
    response = client.post("/api/analyses/run", json=valid_payload)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PROCESSING_ERROR"
    assert "internal provider detail" not in response.text


def test_missing_artifact_and_path_traversal_are_rejected(client: TestClient) -> None:
    analysis_id = str(uuid4())
    analysis_registry.add(make_result(analysis_id))

    missing = client.get(f"/api/analyses/{analysis_id}/artifacts/chart")
    traversal = client.get(f"/api/analyses/{analysis_id}/artifacts/%2e%2e")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"
    assert traversal.status_code in {404, 422}
