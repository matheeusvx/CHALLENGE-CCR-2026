"""Real SQLite smoke flow from persisted analysis through the Alert API."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.automatic_analysis import (
    AutomaticAnalysisCoordinator,
    AutomaticAnalysisPolicy,
    AutomaticAnalysisRepository,
)
from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    AlertType,
    Analysis,
    init_database,
    reset_engine,
    save_analysis,
    session_scope,
)


def _geometry(offset: float) -> dict:
    west, south = -47.0 + offset, -23.0 + offset
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [west + 0.01, south],
            [west + 0.01, south + 0.01], [west, south],
        ]],
    }


def _persist(
    geometry: dict,
    decision: str,
    created_at: datetime,
    *,
    analysis_id: str | None = None,
    support: str | None = None,
) -> str:
    identifier = analysis_id or str(uuid4())
    payload = {
        "analysis_id": identifier,
        "status": "completed",
        "recommendation": {
            "decision": decision,
            "confidence": "medium",
            "experimental": True,
            "metrics": {"observation_count": 1},
        },
        "analysis_period": {},
        "aoi": {"centroid": {"longitude": -46.99, "latitude": -22.99}},
        "summary": {},
        "timeseries": [{
            "datetime": created_at.date().isoformat(),
            "ndvi_mean": 0.5,
        }],
    }
    if support:
        payload["decision_support"] = {
            "status": "available",
            "agreement": support,
            "model_version": "regrowth-support-v0",
            "suggestion": "nao_cortar",
            "confidence": "low",
        }
    with session_scope() as session:
        save_analysis(
            session,
            payload,
            geometry=geometry,
            created_at=created_at,
            alert_now=created_at,
        )
    return identifier


def test_automatic_cache_hit_does_not_reexecute_or_duplicate_alerts(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'automatic-alerts.db').as_posix()}"
    )
    reset_engine()
    init_database()

    class InlineExecutor:
        def submit(self, function, *args):
            future = Future()
            try:
                future.set_result(function(*args))
            except Exception as exc:  # pragma: no cover - surfaced by coordinator
                future.set_exception(exc)
            return future

    clock = lambda: datetime(2026, 8, 10, 15, tzinfo=timezone.utc)
    coordinator = AutomaticAnalysisCoordinator(
        AutomaticAnalysisRepository(tmp_path / "automatic-cache.db"),
        AutomaticAnalysisPolicy(
            enabled=True,
            min_zoom=14,
            max_zoom=22,
            canonical_tile_zoom=17,
            max_area_km2=1000,
            min_dimension_meters=20,
            max_dimension_meters=50000,
            cache_ttl_seconds=60,
            in_progress_ttl_seconds=1800,
            failure_ttl_seconds=30,
            force_refresh_cooldown_seconds=10,
            max_concurrent=1,
        ),
        executor=InlineExecutor(),
        clock=clock,
    )
    executions = 0

    def execute(geometry, analysis_id, **_kwargs):
        nonlocal executions
        executions += 1
        started = datetime(2026, 8, 1, 10)
        _persist(geometry, "nao_cortar", started)
        _persist(geometry, "cortar", started + timedelta(days=1), analysis_id=analysis_id)
        return {"analysis_id": analysis_id, "status": "completed"}

    request = {
        "bounds": {
            "west": -46.9625,
            "south": -23.1095,
            "east": -46.9595,
            "north": -23.1065,
        },
        "center": {"lng": -46.961, "lat": -23.108},
        "zoom": 17,
        "force_refresh": False,
        "analysis_period": "2026-07-01/2026-07-31",
        "execute": execute,
    }
    first = coordinator.request(**request)
    with session_scope() as session:
        counts_after_execution = (
            session.query(Alert).count(),
            session.query(AlertEvent).count(),
            session.query(Analysis).count(),
        )
    cached = coordinator.request(**request)
    with session_scope() as session:
        counts_after_cache_hit = (
            session.query(Alert).count(),
            session.query(AlertEvent).count(),
            session.query(Analysis).count(),
        )

    assert first["status"] == "analysis_started"
    assert cached["status"] == "cache_hit"
    assert executions == 1
    assert counts_after_execution == counts_after_cache_hit == (1, 1, 2)
    reset_engine()


def test_persistence_engine_restart_api_patch_and_recurrence(tmp_path, monkeypatch):
    database_path = tmp_path / "alerts-e2e.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    reset_engine()
    init_database()
    start = datetime(2026, 8, 1, 10)

    # A: a valid recommendation transition.
    transition_geometry = _geometry(0)
    _persist(transition_geometry, "nao_cortar", start)
    transition_analysis = _persist(
        transition_geometry, "cortar", start + timedelta(days=1)
    )

    # B: the same subject remains CORTAR beyond the seven-day threshold.
    _persist(transition_geometry, "cortar", start + timedelta(days=8))

    # C: an inconclusive observation after a valid recommendation.
    reobservation_geometry = _geometry(0.1)
    _persist(reobservation_geometry, "nao_cortar", start)
    _persist(reobservation_geometry, "inconclusivo", start + timedelta(days=1))

    # D/E: support divergence, automatic resolution and later recurrence.
    support_geometry = _geometry(0.2)
    first_divergence = _persist(support_geometry, "cortar", start, support="diverge")
    _persist(support_geometry, "cortar", start + timedelta(days=1), support="concorda")
    recurring_divergence = _persist(
        support_geometry, "cortar", start + timedelta(days=2), support="diverge"
    )

    # Reprocessing the same scientific analysis is an idempotent update.
    with session_scope() as session:
        events_before = session.query(AlertEvent).count()
    _persist(
        support_geometry,
        "cortar",
        start + timedelta(days=2),
        analysis_id=recurring_divergence,
        support="diverge",
    )
    with session_scope() as session:
        assert session.query(AlertEvent).count() == events_before
        assert session.query(Analysis).filter(Analysis.id == recurring_divergence).count() == 1
        generated_types = {value for value, in session.query(Alert.type).distinct()}
        assert {
            AlertType.RECOMMENDATION_CHANGED.value,
            AlertType.CUT_PENDING.value,
            AlertType.REOBSERVATION_REQUIRED.value,
            AlertType.SUPPORT_DIVERGENCE.value,
        } <= generated_types
        divergences = (
            session.query(Alert)
            .filter(Alert.type == AlertType.SUPPORT_DIVERGENCE.value)
            .order_by(Alert.first_detected_at)
            .all()
        )
        assert len(divergences) == 2
        assert divergences[0].analysis_id == first_divergence
        assert divergences[0].status == "resolved"
        assert divergences[1].analysis_id == recurring_divergence
        assert divergences[1].status == "new"

    # Simulate an API process restart while keeping the same SQLite file.
    reset_engine()
    init_database()
    with TestClient(app, raise_server_exceptions=True) as client:
        page = client.get("/api/alerts", params={"limit": 200})
        assert page.status_code == 200
        body = page.json()
        assert body["total"] >= 5
        assert body["active_count"] >= 4

        transition = next(
            item
            for item in body["items"]
            if item["type"] == AlertType.RECOMMENDATION_CHANGED.value
            and item["analysis_id"] == transition_analysis
        )
        detail = client.get(f"/api/alerts/{transition['id']}")
        assert detail.status_code == 200
        assert detail.json()["timeline"][0]["event_type"] == "created"
        assert detail.json()["map_target"]["geometry"] == transition_geometry

        seen = client.patch(
            f"/api/alerts/{transition['id']}",
            json={"status": "seen", "version": transition["version"]},
        )
        monitoring = client.patch(
            f"/api/alerts/{transition['id']}",
            json={"status": "monitoring", "version": seen.json()["version"]},
        )
        resolved = client.patch(
            f"/api/alerts/{transition['id']}",
            json={"status": "resolved", "version": monitoring.json()["version"]},
        )
        assert [seen.status_code, monitoring.status_code, resolved.status_code] == [200, 200, 200]
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["version"] == transition["version"] + 3

        final_detail = client.get(f"/api/alerts/{transition['id']}").json()
        assert [event["new_status"] for event in final_detail["timeline"][-3:]] == [
            "seen", "monitoring", "resolved"
        ]

    reset_engine()
