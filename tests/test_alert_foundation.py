"""ALERT-01 persistent foundation and subject identity tests."""

from __future__ import annotations

from datetime import date, datetime
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.satellite_monitoring.database import (
    Alert,
    AlertRepository,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Highway,
    KmMarker,
    MonitoredSection,
    MonitoredSectionRepository,
    count_analyses,
    count_history_analyses,
    geometry_identity,
    get_analysis,
    get_engine,
    init_database,
    hide_analysis_from_history,
    list_history_analyses,
    reset_engine,
    save_analysis,
    session_scope,
)
from src.satellite_monitoring.database.migrations import (
    ALERT_FOUNDATION_VERSION,
    HISTORY_VISIBILITY_VERSION,
)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[-47.0, -23.0], [-46.99, -23.0], [-46.99, -22.99], [-47.0, -23.0]]
    ],
}


def _payload(analysis_id: str | None = None) -> dict:
    return {
        "analysis_id": analysis_id or str(uuid4()),
        "status": "completed",
        "recommendation": {
            "decision": "cortar",
            "confidence": "medium",
            "experimental": True,
            "summary": "persisted",
            "metrics": {"observation_count": 2},
        },
        "analysis_period": {
            "start_date": "2026-07-01",
            "end_date": "2026-08-01",
            "timezone": "America/Sao_Paulo",
            "strategy": "explicit",
        },
        "aoi": {"centroid": {"longitude": -46.995, "latitude": -22.995}},
        "summary": {},
        "timeseries": [
            {"datetime": "2026-07-10T00:00:00Z", "ndvi_mean": 0.3},
            {"datetime": "2026-07-20T00:00:00Z", "ndvi_mean": 0.4},
        ],
    }


@pytest.fixture
def alert_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'alerts.db').as_posix()}")
    reset_engine()
    init_database()
    init_database()
    yield
    reset_engine()


def _create_legacy_database(path) -> str:
    analysis_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE analysis (
            id VARCHAR(36) PRIMARY KEY,
            created_at DATETIME NOT NULL,
            status VARCHAR(32) NOT NULL,
            decision VARCHAR(16), confidence VARCHAR(16), summary TEXT,
            experimental BOOLEAN NOT NULL,
            period_start DATE, period_end DATE, period_timezone VARCHAR(64),
            period_strategy VARCHAR(32), selected_area_m2 FLOAT,
            effective_area_m2 FLOAT, effective_area_pct FLOAT,
            analysis_quality_status VARCHAR(16), analysis_quality_score FLOAT,
            observation_count INTEGER, current_ndvi_mean FLOAT,
            current_percentile FLOAT, recent_trend FLOAT,
            recent_trend_status VARCHAR(16), geometry JSON,
            centroid_longitude FLOAT, centroid_latitude FLOAT,
            nearest_km INTEGER, run_directory VARCHAR(512), artifacts JSON,
            payload JSON
        );
        INSERT INTO analysis (
            id, created_at, status, decision, confidence, experimental, payload
        ) VALUES (
            'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            '2026-08-01 10:00:00', 'completed', 'cortar', 'medium', 1,
            '{"analysis_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}'
        );
        """
    )
    connection.commit()
    connection.close()
    return analysis_id


def test_versioned_migration_upgrades_existing_sqlite_and_preserves_analysis(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy.db"
    analysis_id = _create_legacy_database(path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    reset_engine()
    init_database()
    init_database()

    inspector = inspect(get_engine())
    columns = {item["name"] for item in inspector.get_columns("analysis")}
    assert {
        "subject_kind", "subject_key", "spatial_key", "road_id", "road_ref",
        "road_name", "axis_id", "section_id", "section_index",
        "latest_valid_observation_on",
        "hidden_from_history_at",
    } <= columns
    monitored_columns = {
        item["name"]
        for item in inspector.get_columns("monitored_section")
    }
    assert {"claimed_at", "claim_token", "claim_expires_at"} <= monitored_columns
    assert {"alert", "alert_event", "monitored_section", "schema_migration"} <= set(
        inspector.get_table_names()
    )
    with session_scope() as session:
        old = get_analysis(session, analysis_id)
        assert old is not None
        assert old.decision == "cortar"
        assert old.subject_key is None
        versions = set(session.execute(
            text("SELECT version FROM schema_migration")
        ).scalars())
        assert ALERT_FOUNDATION_VERSION in versions
        assert HISTORY_VISIBILITY_VERSION in versions
    reset_engine()


def test_new_database_contains_alert_foundation(alert_database):
    inspector = inspect(get_engine())
    assert {"analysis", "alert", "alert_event", "monitored_section"} <= set(
        inspector.get_table_names()
    )
    assert "ix_analysis_subject_created" in {
        item["name"] for item in inspector.get_indexes("analysis")
    }
    assert "ix_analysis_history_visible" in {
        item["name"] for item in inspector.get_indexes("analysis")
    }


def test_hidden_analysis_stays_available_to_scientific_alert_history(alert_database):
    subject_key = geometry_identity(GEOMETRY).subject_key
    first = _payload()
    first["recommendation"]["decision"] = "nao_cortar"
    with session_scope() as session:
        saved = save_analysis(
            session,
            first,
            geometry=GEOMETRY,
            created_at=datetime(2026, 8, 1, 12),
        )
        assert hide_analysis_from_history(
            session, saved.id, hidden_at=datetime(2026, 8, 2, 12)
        )

    with session_scope() as session:
        assert count_history_analyses(session) == 0
        assert list_history_analyses(session) == []
        hidden = get_analysis(session, first["analysis_id"])
        assert hidden is not None
        assert len(hidden.observations) == 2

    second = _payload()
    with session_scope() as session:
        save_analysis(
            session,
            second,
            geometry=GEOMETRY,
            created_at=datetime(2026, 8, 3, 12),
            alert_now=datetime(2026, 8, 3, 12),
        )

    with session_scope() as session:
        transition = session.query(Alert).filter_by(
            subject_key=subject_key,
            type=AlertType.RECOMMENDATION_CHANGED.value,
        ).one()
        assert transition.previous_analysis_id == first["analysis_id"]


def test_save_analysis_updates_same_row_without_breaking_foreign_keys(alert_database):
    analysis_id = str(uuid4())
    with session_scope() as session:
        save_analysis(session, _payload(analysis_id), geometry=GEOMETRY)
        rowid_before = session.execute(
            text("SELECT rowid FROM analysis WHERE id=:id"), {"id": analysis_id}
        ).scalar_one()
        AlertRepository(session).add(
            _alert(analysis_id, geometry_identity(GEOMETRY).subject_key, "active-key")
        )

    updated = _payload(analysis_id)
    updated["recommendation"]["confidence"] = "high"
    updated["timeseries"][1]["ndvi_mean"] = 0.5
    with session_scope() as session:
        save_analysis(session, updated, geometry=GEOMETRY)

    with session_scope() as session:
        stored = get_analysis(session, analysis_id)
        assert stored is not None
        assert stored.confidence == "high"
        assert stored.observations[1].ndvi_mean == pytest.approx(0.5)
        assert count_analyses(session) == 1
        assert session.query(Alert).count() == 1
        assert session.execute(
            text("SELECT rowid FROM analysis WHERE id=:id"), {"id": analysis_id}
        ).scalar_one() == rowid_before


def test_geometry_identity_is_canonical_and_ignores_properties():
    shifted_reversed_feature = {
        "type": "Feature",
        "properties": {"ignored": "anything"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-46.99, -22.99], [-46.99, -23.0], [-47.0, -23.0],
                [-46.99, -22.99],
            ]],
        },
    }
    assert geometry_identity(GEOMETRY).subject_key == geometry_identity(
        shifted_reversed_feature
    ).subject_key


def test_different_manual_geometry_has_different_identity():
    changed = {
        "type": "Polygon",
        "coordinates": [
            [[-47.0, -23.0], [-46.98, -23.0], [-46.98, -22.99], [-47.0, -23.0]]
        ],
    }
    assert geometry_identity(GEOMETRY).subject_key != geometry_identity(changed).subject_key


def test_nearest_km_does_not_participate_in_manual_identity(alert_database):
    with session_scope() as session:
        highway = Highway(code="SP-001", name="Test")
        session.add(highway)
        session.flush()
        session.add_all([
            KmMarker(highway_id=highway.id, km=1, longitude=-46.995, latitude=-22.995),
            KmMarker(highway_id=highway.id, km=99, longitude=-45.0, latitude=-21.0),
        ])
    first, second = str(uuid4()), str(uuid4())
    with session_scope() as session:
        save_analysis(session, _payload(first), geometry=GEOMETRY)
    with session_scope() as session:
        markers = session.query(KmMarker).all()
        markers[0].longitude, markers[0].latitude = -44.0, -20.0
        markers[1].longitude, markers[1].latitude = -46.995, -22.995
    with session_scope() as session:
        save_analysis(session, _payload(second), geometry=GEOMETRY)
    with session_scope() as session:
        one, two = get_analysis(session, first), get_analysis(session, second)
        assert one is not None and two is not None
        assert one.nearest_km == 1 and two.nearest_km == 99
        assert one.subject_key == two.subject_key


def test_monitored_section_upserts_and_tracks_latest_valid_observation(alert_database):
    first, second = str(uuid4()), str(uuid4())
    with session_scope() as session:
        save_analysis(
            session, _payload(first), geometry=GEOMETRY,
            created_at=datetime(2026, 8, 1, 10),
        )
        key = geometry_identity(GEOMETRY).subject_key
        initial = MonitoredSectionRepository(session).get_by_subject_key(key)
        assert initial is not None
        assert initial.last_valid_observation_on == date(2026, 7, 20)
    newer = _payload(second)
    newer["timeseries"].append({"datetime": "2026-08-02T00:00:00Z", "ndvi_mean": 0.6})
    with session_scope() as session:
        save_analysis(
            session, newer, geometry=GEOMETRY,
            created_at=datetime(2026, 8, 3, 10),
        )
    with session_scope() as session:
        records = session.query(MonitoredSection).all()
        assert len(records) == 1
        assert records[0].last_analysis_at == datetime(2026, 8, 3, 10)
        assert records[0].last_valid_observation_on == date(2026, 8, 2)
        # ALERT-01 provides persistence only; no alert rule runs on analysis save.
        assert session.query(Alert).count() == 0


def test_open_key_is_unique_but_null_allows_resolved_recurrence(alert_database):
    analysis_id = str(uuid4())
    subject_key = geometry_identity(GEOMETRY).subject_key
    with session_scope() as session:
        save_analysis(session, _payload(analysis_id), geometry=GEOMETRY)
        AlertRepository(session).add(_alert(analysis_id, subject_key, "same-open-key"))
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            AlertRepository(session).add(_alert(analysis_id, subject_key, "same-open-key"))
    with session_scope() as session:
        existing = session.query(Alert).one()
        existing.status = AlertStatus.RESOLVED.value
        existing.open_key = None
        existing.resolved_at = datetime(2026, 8, 2)
    with session_scope() as session:
        AlertRepository(session).add(_alert(analysis_id, subject_key, "same-open-key"))
    with session_scope() as session:
        assert session.query(Alert).count() == 2


def _alert(analysis_id: str, subject_key: str, open_key: str | None) -> Alert:
    detected = datetime(2026, 8, 1, 12)
    return Alert(
        id=str(uuid4()),
        type=AlertType.CUT_PENDING.value,
        severity=AlertSeverity.MEDIUM.value,
        status=AlertStatus.NEW.value,
        subject_kind="geometry",
        subject_key=subject_key,
        analysis_id=analysis_id,
        first_detected_at=detected,
        last_seen_at=detected,
        updated_at=detected,
        open_key=open_key,
        metadata_json={},
    )
