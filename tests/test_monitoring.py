"""ALERT-05 scheduler-ready monitoring tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    AlertType,
    Analysis,
    MonitoredSection,
    MonitoredSectionRepository,
    MonitoringConfig,
    MonitoringService,
    init_database,
    reset_engine,
    save_analysis,
    session_scope,
)
from src.satellite_monitoring.database.identity import road_section_identity

NOW = datetime(2026, 9, 12, 12)


@pytest.fixture
def monitoring_database(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'monitoring.db').as_posix()}"
    )
    reset_engine()
    init_database()
    yield
    reset_engine()


def _section(
    key: str,
    *,
    due: datetime,
    enabled: bool = True,
    kind: str = "road_section",
    road_ref: str = "SP-348",
) -> MonitoredSection:
    return MonitoredSection(
        subject_key=key,
        subject_kind=kind,
        enabled=enabled,
        spatial_key=key if kind == "road_section" else None,
        road_id="road-1" if kind == "road_section" else None,
        road_ref=road_ref if kind == "road_section" else None,
        road_name="Bandeirantes" if kind == "road_section" else None,
        axis_id="axis-1" if kind == "road_section" else None,
        section_id=key if kind == "road_section" else None,
        section_index=1 if kind == "road_section" else None,
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [-47.0, -23.0], [-46.99, -23.0],
                [-46.99, -22.99], [-47.0, -23.0],
            ]],
        },
        source="automatic" if kind == "road_section" else "manual",
        cadence_days=7,
        last_analysis_at=due - timedelta(days=7),
        last_valid_observation_on=NOW.date(),
        next_due_at=due,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=7),
        metadata_json={},
    )


def _add(*sections: MonitoredSection) -> None:
    with session_scope() as session:
        for section in sections:
            session.add(Analysis(
                id=str(uuid4()),
                created_at=section.last_analysis_at,
                status="completed",
                decision="nao_cortar",
                confidence="medium",
                experimental=True,
                geometry=section.geometry,
                subject_kind=section.subject_kind,
                subject_key=section.subject_key,
                spatial_key=section.spatial_key,
                road_id=section.road_id,
                road_ref=section.road_ref,
                road_name=section.road_name,
                axis_id=section.axis_id,
                section_id=section.section_id,
                section_index=section.section_index,
                latest_valid_observation_on=section.last_valid_observation_on,
                payload={},
            ))
            session.add(section)


def _service(analyzer=None, *, enabled=True, batch_size=10, ttl=60):
    return MonitoringService(
        analyzer,
        config=MonitoringConfig(
            enabled=enabled,
            batch_size=batch_size,
            default_cadence_days=7,
            claim_ttl_seconds=ttl,
        ),
    )


def test_due_query_boundaries_order_limit_disabled_and_road(monitoring_database):
    _add(
        _section("future", due=NOW + timedelta(seconds=1)),
        _section("exact", due=NOW),
        _section("old", due=NOW - timedelta(days=3)),
        _section("disabled", due=NOW - timedelta(days=4), enabled=False),
        _section("other-road", due=NOW - timedelta(days=5), road_ref="SP-330"),
    )
    service = _service()
    assert [item.subject_key for item in service.due_sections(now=NOW)] == [
        "other-road", "old", "exact"
    ]
    assert [item.subject_key for item in service.due_sections(now=NOW, limit=2)] == [
        "other-road", "old"
    ]
    assert [
        item.subject_key for item in service.due_sections(now=NOW, road="SP-348")
    ] == ["old", "exact"]


def test_claim_is_exclusive_and_expired_claim_is_recoverable(monitoring_database):
    _add(_section("due", due=NOW - timedelta(days=1)))
    with session_scope() as session:
        repository = MonitoredSectionRepository(session)
        assert repository.claim(
            "due", token="one", now=NOW, expires_at=NOW + timedelta(minutes=1)
        ) is not None
    with session_scope() as session:
        repository = MonitoredSectionRepository(session)
        assert repository.claim(
            "due", token="two", now=NOW, expires_at=NOW + timedelta(minutes=1)
        ) is None
        record = repository.get_by_subject_key("due")
        assert record is not None
        record.claim_expires_at = NOW - timedelta(seconds=1)
    with session_scope() as session:
        claimed = MonitoredSectionRepository(session).claim(
            "due", token="three", now=NOW, expires_at=NOW + timedelta(minutes=1)
        )
        assert claimed is not None and claimed.claim_token == "three"


def test_two_runners_do_not_process_the_same_section(monitoring_database):
    _add(_section("due", due=NOW))
    entered, release = Event(), Event()
    calls = 0
    lock = Lock()

    def analyzer(_section):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        release.wait(timeout=5)
        return "cache_hit"

    service = _service(analyzer)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.run_once, now=NOW)
        assert entered.wait(timeout=5)
        second = executor.submit(service.run_once, now=NOW)
        second_summary = second.result(timeout=5)
        release.set()
        first_summary = first.result(timeout=5)
    assert calls == 1
    assert first_summary.claimed + second_summary.claimed == 1


def test_cache_hit_and_failure_do_not_advance_due_and_claim_is_released(
    monitoring_database,
):
    _add(
        _section("cache", due=NOW),
        _section("failure", due=NOW),
    )

    def analyzer(section):
        if section.subject_key == "failure":
            raise RuntimeError("controlled failure")
        return "cache_hit"

    summary = _service(analyzer).run_once(now=NOW)
    assert summary.processed == 2
    assert summary.failed == 1
    assert summary.skipped == 1
    with session_scope() as session:
        records = session.query(MonitoredSection).all()
        assert all(item.next_due_at == NOW for item in records)
        assert all(item.claim_token is None for item in records)
        assert all(item.last_valid_observation_on == NOW.date() for item in records)


def test_real_success_via_save_analysis_advances_due(monitoring_database):
    geometry = _section("unused", due=NOW).geometry
    identity = road_section_identity("roadside:v1:road-1:section-1", {
        "id": "road-1", "ref": "SP-348", "name": "Bandeirantes",
        "axis_id": "axis-1", "section_id": "section-1", "section_index": 1,
    })
    due = NOW - timedelta(days=1)
    _add(_section(identity.subject_key, due=due))

    def analyzer(section):
        payload = {
            "analysis_id": "new-analysis",
            "status": "completed",
            "recommendation": {"decision": "nao_cortar", "metrics": {}},
            "timeseries": [{"datetime": "2026-09-12", "ndvi_mean": 0.5}],
            "analysis_trigger": "automatic_viewport",
        }
        with session_scope() as session:
            save_analysis(
                session,
                payload,
                geometry=section.geometry,
                created_at=NOW,
                identity=identity,
            )
        return "completed"

    summary = _service(analyzer).run_once(now=NOW)
    assert summary.completed == 1
    with session_scope() as session:
        record = session.get(MonitoredSection, identity.subject_key)
        assert record is not None
        assert record.last_analysis_at == NOW
        assert record.next_due_at == NOW + timedelta(days=7)


def test_manual_geometry_is_explicitly_skipped(monitoring_database):
    _add(_section("geometry:v1:test", due=NOW, kind="geometry"))
    calls = []
    summary = _service(lambda item: calls.append(item) or "completed").run_once(now=NOW)
    assert summary.skipped == 1
    assert summary.claimed == 0
    assert calls == []


def test_temporal_stale_is_idempotent(monitoring_database):
    section = _section("stale", due=NOW + timedelta(days=1))
    section.last_valid_observation_on = NOW.date() - timedelta(days=30)
    _add(section)
    service = _service()
    assert service.evaluate_temporal_alerts(now=NOW) == 1
    assert service.evaluate_temporal_alerts(now=NOW) == 0
    with session_scope() as session:
        assert session.query(Alert).filter(
            Alert.type == AlertType.STALE_MONITORING.value
        ).count() == 1
        assert session.query(AlertEvent).count() == 1


def test_dry_run_does_not_modify_claims_or_alerts(monitoring_database):
    section = _section("due", due=NOW - timedelta(days=30))
    section.last_valid_observation_on = NOW.date() - timedelta(days=60)
    _add(section)
    summary = _service(lambda _item: "completed").run_once(now=NOW, dry_run=True)
    assert summary.due == summary.skipped == 1
    with session_scope() as session:
        record = session.get(MonitoredSection, "due")
        assert record is not None and record.claim_token is None
        assert session.query(Alert).count() == 0


def test_one_failure_does_not_interrupt_remaining_batch(monitoring_database):
    _add(_section("a", due=NOW), _section("b", due=NOW))
    seen = []

    def analyzer(section):
        seen.append(section.subject_key)
        if section.subject_key == "a":
            raise RuntimeError("first fails")
        return "completed"

    summary = _service(analyzer).run_once(now=NOW)
    assert seen == ["a", "b"]
    assert summary.failed == 1
    assert summary.completed == 1


def test_monitoring_claim_columns_exist_after_migration(monitoring_database):
    from src.satellite_monitoring.database import get_engine

    columns = {
        item["name"] for item in inspect(get_engine()).get_columns("monitored_section")
    }
    assert {"claimed_at", "claim_token", "claim_expires_at"} <= columns


def test_configuration_reads_conservative_environment_defaults(monkeypatch):
    for name in (
        "MONITORING_ENABLED",
        "MONITORING_BATCH_SIZE",
        "MONITORING_DEFAULT_CADENCE_DAYS",
        "MONITORING_CLAIM_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert MonitoringConfig.from_env() == MonitoringConfig(
        enabled=False,
        batch_size=10,
        default_cadence_days=7,
        claim_ttl_seconds=3600,
    )


def test_existing_section_keeps_its_persisted_cadence(monitoring_database):
    geometry = _section("unused", due=NOW).geometry
    identity = road_section_identity("roadside:v1:road-1:custom", {
        "id": "road-1", "ref": "SP-348", "section_id": "custom",
    })
    existing = _section(identity.subject_key, due=NOW)
    existing.cadence_days = 14
    _add(existing)
    with session_scope() as session:
        MonitoredSectionRepository(session).upsert(
            identity,
            geometry=geometry,
            source="automatic",
            analysis_at=NOW + timedelta(days=1),
            latest_valid_observation_on=NOW.date(),
            cadence_days=7,
        )
    with session_scope() as session:
        record = session.get(MonitoredSection, identity.subject_key)
        assert record is not None
        assert record.cadence_days == 14
        assert record.next_due_at == NOW + timedelta(days=15)
