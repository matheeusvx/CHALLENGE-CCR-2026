"""Operational Alert API contract and lifecycle tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import get_alert_now
from apps.api.app.main import app
from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    Analysis,
    MonitoredSection,
    session_scope,
)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [-47.0, -23.0], [-46.98, -23.0], [-46.98, -22.98],
        [-47.0, -22.98], [-47.0, -23.0],
    ]],
}


@pytest.fixture(autouse=True)
def clean_alert_api_database():
    with session_scope() as session:
        session.query(AlertEvent).delete()
        session.query(Alert).delete()
        session.query(MonitoredSection).delete()
        session.query(Analysis).delete()
    yield


def _analysis(
    analysis_id: str,
    at: datetime,
    decision: str,
    *,
    geometry: dict | None = None,
) -> Analysis:
    return Analysis(
        id=analysis_id,
        created_at=at,
        status="completed",
        decision=decision,
        confidence="medium",
        experimental=True,
        subject_kind="road_section",
        subject_key="roadside:v1:sp-330:axis-a:section-10",
        spatial_key="roadside:v1:sp-330:axis-a:section-10",
        road_id="road-330",
        road_ref="SP-330",
        road_name="Rodovia Anhanguera",
        axis_id="axis-a",
        section_id="section-10",
        section_index=10,
        geometry=geometry,
        centroid_longitude=-46.99 if geometry else None,
        centroid_latitude=-22.99 if geometry else None,
        payload={"private_internal_rule": "B", "traceback": "must-not-leak"},
    )


def _seed_alert(
    *,
    severity: str = "high",
    status: str = "new",
    alert_type: str = "RECOMMENDATION_CHANGED",
    at: datetime | None = None,
    road_ref: str = "SP-330",
    road_name: str = "Rodovia Anhanguera",
    section_id: str = "section-10",
    with_history: bool = False,
) -> str:
    at = at or datetime(2026, 8, 1, 10)
    origin_id = str(uuid4())
    previous_id = str(uuid4()) if with_history else None
    latest_id = str(uuid4()) if with_history else origin_id
    alert_id = str(uuid4())
    with session_scope() as session:
        analyses = []
        if previous_id:
            analyses.append(_analysis(previous_id, at - timedelta(days=1), "nao_cortar"))
        analyses.append(_analysis(origin_id, at, "cortar"))
        if latest_id != origin_id:
            analyses.append(
                _analysis(latest_id, at + timedelta(days=1), "cortar", geometry=GEOMETRY)
            )
        session.add_all(analyses)
        session.flush()
        alert = Alert(
            id=alert_id,
            type=alert_type,
            severity=severity,
            status=status,
            subject_kind="road_section",
            subject_key=f"roadside:v1:{road_ref}:{section_id}:{alert_id}",
            spatial_key=f"roadside:v1:{road_ref}:{section_id}",
            road_id="road-330",
            road_ref=road_ref,
            road_name=road_name,
            axis_id="axis-a",
            section_id=section_id,
            section_index=10,
            analysis_id=origin_id,
            previous_analysis_id=previous_id,
            last_analysis_id=latest_id,
            current_recommendation="cortar",
            previous_recommendation="nao_cortar" if previous_id else None,
            first_detected_at=at,
            last_seen_at=at,
            acknowledged_at=at if status in {"seen", "monitoring", "resolved"} else None,
            resolved_at=at if status == "resolved" else None,
            updated_at=at,
            open_key=None if status == "resolved" else f"open:{alert_id}",
            version=1,
            metadata_json={"transition": "nao_cortar:cortar"},
        )
        session.add(alert)
        session.flush()
        session.add(
            AlertEvent(
                alert_id=alert_id,
                event_type="created",
                occurred_at=at,
                analysis_id=origin_id,
                new_status="new",
                severity=severity,
                metadata_json={},
            )
        )
    return alert_id


def test_list_paginates_counts_active_and_orders(client: TestClient):
    base = datetime(2026, 8, 1, 10)
    resolved = _seed_alert(severity="critical", status="resolved", at=base + timedelta(days=3))
    medium = _seed_alert(severity="medium", status="new", at=base + timedelta(days=2))
    high_old = _seed_alert(severity="high", status="monitoring", at=base)
    high_new = _seed_alert(severity="high", status="seen", at=base + timedelta(days=1))

    response = client.get("/api/alerts?limit=2&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["active_count"] == 3
    assert body["limit"] == 2 and body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [high_new, high_old]

    second = client.get("/api/alerts?limit=2&offset=2").json()
    assert [item["id"] for item in second["items"]] == [medium, resolved]


def test_list_filters_status_severity_type_road_and_section(client: TestClient):
    wanted = _seed_alert(
        severity="low",
        status="monitoring",
        alert_type="SUPPORT_DIVERGENCE",
        road_ref="SP-330",
        road_name="Rodovia Anhanguera",
        section_id="s-1",
    )
    _seed_alert(
        severity="high",
        alert_type="CUT_PENDING",
        road_ref="SP-348",
        road_name="Rodovia dos Bandeirantes",
        section_id="s-2",
    )
    response = client.get(
        "/api/alerts",
        params={
            "status": "monitoring",
            "severity": "low",
            "type": "SUPPORT_DIVERGENCE",
            "road": "anhanguera",
            "section_id": "s-1",
        },
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [wanted]
    assert response.json()["active_count"] == 1


def test_list_validates_pagination_and_enums(client: TestClient):
    assert client.get("/api/alerts?limit=0").status_code == 422
    assert client.get("/api/alerts?limit=201").status_code == 422
    assert client.get("/api/alerts?status=invalid").status_code == 422


def test_detail_contains_timeline_analysis_refs_road_and_map_target(client: TestClient):
    alert_id = _seed_alert(with_history=True)
    response = client.get(f"/api/alerts/{alert_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == alert_id
    assert body["metadata"] == {"transition": "nao_cortar:cortar"}
    assert [event["event_type"] for event in body["timeline"]] == ["created"]
    assert body["origin_analysis"]["decision"] == "cortar"
    assert body["previous_analysis"]["decision"] == "nao_cortar"
    assert body["latest_analysis"]["decision"] == "cortar"
    assert body["road_metadata"]["road_ref"] == "SP-330"
    target = body["map_target"]
    assert target["geometry"] == GEOMETRY
    assert target["bounds"] == {
        "west": -47.0, "south": -23.0, "east": -46.98, "north": -22.98
    }
    assert target["centroid"] == {"longitude": -46.99, "latitude": -22.99}
    assert target["section_id"] == "section-10"
    serialized = response.text
    assert "private_internal_rule" not in serialized
    assert "traceback" not in serialized


def test_detail_404(client: TestClient):
    response = client.get(f"/api/alerts/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ALERT_NOT_FOUND"


def test_patch_valid_transition_sets_timestamps_version_and_event(client: TestClient):
    alert_id = _seed_alert()
    now = datetime(2026, 8, 5, 12)
    app.dependency_overrides[get_alert_now] = lambda: now
    response = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "seen", "version": 1}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "seen"
    assert body["version"] == 2
    assert body["acknowledged_at"] == "2026-08-05T12:00:00"
    assert body["resolved_at"] is None
    with session_scope() as session:
        event = session.query(AlertEvent).order_by(AlertEvent.id.desc()).first()
        assert event is not None
        assert event.event_type == "status_changed"
        assert event.previous_status == "new" and event.new_status == "seen"


def test_patch_stale_version_returns_409_without_mutation(client: TestClient):
    alert_id = _seed_alert()
    response = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "monitoring", "version": 99}
    )
    assert response.status_code == 409
    with session_scope() as session:
        alert = session.get(Alert, alert_id)
        assert alert is not None and alert.status == "new" and alert.version == 1
        assert session.query(AlertEvent).count() == 1


def test_patch_allowed_status_chain_and_resolution(client: TestClient):
    alert_id = _seed_alert()
    first = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "seen", "version": 1}
    )
    second = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "monitoring", "version": 2}
    )
    third = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "resolved", "version": 3}
    )
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert third.json()["version"] == 4
    assert third.json()["resolved_at"] is not None
    with session_scope() as session:
        alert = session.get(Alert, alert_id)
        assert alert is not None and alert.open_key is None


@pytest.mark.parametrize(
    ("initial", "target"),
    [("seen", "seen"), ("monitoring", "seen"), ("resolved", "seen")],
)
def test_patch_rejects_invalid_transitions(client: TestClient, initial: str, target: str):
    alert_id = _seed_alert(status=initial)
    response = client.patch(
        f"/api/alerts/{alert_id}", json={"status": target, "version": 1}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ALERT_TRANSITION"


def test_patch_schema_prohibits_new_and_unrelated_fields(client: TestClient):
    alert_id = _seed_alert()
    assert client.patch(
        f"/api/alerts/{alert_id}", json={"status": "new", "version": 1}
    ).status_code == 422
    assert client.patch(
        f"/api/alerts/{alert_id}",
        json={"status": "seen", "version": 1, "severity": "critical"},
    ).status_code == 422


def test_gets_are_side_effect_free_and_patch_preserves_scientific_decision(
    client: TestClient,
):
    alert_id = _seed_alert(with_history=True)
    with session_scope() as session:
        before_alert = session.get(Alert, alert_id)
        assert before_alert is not None
        before = (before_alert.version, before_alert.updated_at, session.query(AlertEvent).count())
        origin_id = before_alert.analysis_id
        decision = session.get(Analysis, origin_id).decision

    assert client.get("/api/alerts").status_code == 200
    assert client.get(f"/api/alerts/{alert_id}").status_code == 200
    with session_scope() as session:
        after_alert = session.get(Alert, alert_id)
        assert after_alert is not None
        assert (after_alert.version, after_alert.updated_at, session.query(AlertEvent).count()) == before

    response = client.patch(
        f"/api/alerts/{alert_id}", json={"status": "resolved", "version": 1}
    )
    assert response.status_code == 200
    with session_scope() as session:
        assert session.get(Analysis, origin_id).decision == decision == "cortar"
