from __future__ import annotations

from uuid import uuid4

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
    assert body["analysis_period"] == {
        "start_date": "2026-05-01",
        "end_date": "2026-08-04",
        "timezone": "America/Sao_Paulo",
        "strategy": "explicit",
    }
    assert "output_root" not in body["summary"]["parameters"]


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
