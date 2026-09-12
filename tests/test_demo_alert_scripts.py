"""Safety and idempotency tests for the manual Alertas demo scripts."""

from __future__ import annotations

from datetime import datetime

import pytest
from shapely.geometry import shape

from scripts.clear_demo_alerts import clear_demo_data
from scripts.seed_demo_alerts import seed_demo_alerts
from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    Analysis,
    MonitoredSection,
    init_database,
    reset_engine,
    session_scope,
)

NOW = datetime(2026, 9, 12, 15)


@pytest.fixture
def demo_database(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'demo-alerts.db').as_posix()}"
    )
    reset_engine()
    init_database()
    yield
    reset_engine()


def _real_records() -> tuple[str, str, str]:
    analysis_id = "00000000-0000-0000-0000-000000000001"
    alert_id = "00000000-0000-0000-0000-000000000002"
    subject_key = "roadside:v1:real:section:1"
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [-47.1, -23.1], [-47.09, -23.1],
            [-47.09, -23.09], [-47.1, -23.1],
        ]],
    }
    with session_scope() as session:
        session.add(Analysis(
            id=analysis_id,
            created_at=NOW,
            status="completed",
            decision="cortar",
            experimental=True,
            geometry=geometry,
            subject_kind="road_section",
            subject_key=subject_key,
            spatial_key=subject_key,
            payload={"metadata": {"source": "real"}},
        ))
        session.flush()
        session.add(MonitoredSection(
            subject_key=subject_key,
            subject_kind="road_section",
            enabled=True,
            spatial_key=subject_key,
            geometry=geometry,
            source="automatic",
            cadence_days=7,
            last_analysis_at=NOW,
            next_due_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            metadata_json={"source": "real"},
        ))
        session.add(Alert(
            id=alert_id,
            type="CUT_PENDING",
            severity="medium",
            status="new",
            subject_kind="road_section",
            subject_key=subject_key,
            spatial_key=subject_key,
            analysis_id=analysis_id,
            last_analysis_id=analysis_id,
            current_recommendation="cortar",
            first_detected_at=NOW,
            last_seen_at=NOW,
            updated_at=NOW,
            open_key="real:cut-pending",
            version=1,
            metadata_json={"source": "real"},
        ))
    return analysis_id, alert_id, subject_key


def test_seed_is_idempotent_and_creates_complete_demo_scenarios(demo_database):
    first = seed_demo_alerts(now=NOW)
    second = seed_demo_alerts(now=NOW)
    assert first == second == {
        "active": 5, "new": 3, "monitoring": 1, "resolved": 1, "total": 6,
    }

    with session_scope() as session:
        alerts = session.query(Alert).all()
        analyses = session.query(Analysis).all()
        events = session.query(AlertEvent).all()
        sections = session.query(MonitoredSection).all()
        assert len(alerts) == 6
        assert len(analyses) == 10
        assert len(events) == 15
        assert len(sections) == 6
        assert all(item.metadata_json.get("demo_data") is True for item in alerts)
        assert all(item.metadata_json.get("demo_data") is True for item in events)
        assert all(item.metadata_json.get("demo_data") is True for item in sections)
        assert all(item.payload["metadata"]["demo_data"] is True for item in analyses)
        assert all(shape(item.geometry).is_valid for item in analyses)
        assert {item.road_ref for item in alerts} == {"SP-330", "SP-348"}
        assert {(item.type, item.severity, item.status) for item in alerts} >= {
            ("RECOMMENDATION_CHANGED", "high", "new"),
            ("CUT_PENDING", "high", "monitoring"),
            ("REOBSERVATION_REQUIRED", "medium", "new"),
            ("STALE_MONITORING", "medium", "seen"),
            ("SUPPORT_DIVERGENCE", "low", "new"),
            ("CUT_PENDING", "medium", "resolved"),
        }
        pending = next(item for item in alerts if item.status == "monitoring")
        assert pending.metadata_json["cut_pending_days"] == 9
        assert pending.metadata_json["not_a_mowing_date_estimate"] is True
        resolved = next(item for item in alerts if item.status == "resolved")
        resolved_events = [item for item in events if item.alert_id == resolved.id]
        assert [item.event_type for item in resolved_events] == [
            "created", "status_changed", "condition_persisted",
            "severity_changed", "status_changed", "auto_resolved",
        ]


def test_cleanup_removes_only_demo_records(demo_database):
    real_analysis, real_alert, real_subject = _real_records()
    seed_demo_alerts(now=NOW)
    with session_scope() as session:
        removed = clear_demo_data(session)
    assert removed == {
        "alerts": 6,
        "events": 15,
        "analyses": 10,
        "monitored_sections": 6,
    }
    with session_scope() as session:
        assert session.get(Analysis, real_analysis) is not None
        assert session.get(Alert, real_alert) is not None
        assert session.get(MonitoredSection, real_subject) is not None
        assert session.query(Alert).count() == 1
        assert session.query(Analysis).count() == 1
        assert session.query(MonitoredSection).count() == 1
