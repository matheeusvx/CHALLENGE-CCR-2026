from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.dependencies import analysis_registry, get_analysis_service
from apps.api.app.main import app
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
    response = client.post("/api/analyses/run", json={"geometry": VALID_GEOMETRY})
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
    assert "output_root" not in body["summary"]["parameters"]


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
