"""ALERT-02 deterministic operational alert engine tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from src.satellite_monitoring.database import (
    Alert,
    AlertEngine,
    AlertEvent,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Analysis,
    geometry_identity,
    init_database,
    reset_engine,
    save_analysis,
    session_scope,
)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [-47.0, -23.0], [-46.99, -23.0], [-46.99, -22.99], [-47.0, -23.0]
    ]],
}


@pytest.fixture
def alert_database(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'alert-engine.db').as_posix()}"
    )
    reset_engine()
    init_database()
    yield
    reset_engine()


def _payload(
    decision: str,
    *,
    analysis_id: str | None = None,
    observed_on: str = "2026-01-01",
    support_status: str | None = None,
    agreement: str | None = None,
) -> dict:
    payload = {
        "analysis_id": analysis_id or str(uuid4()),
        "status": "completed",
        "recommendation": {
            "decision": decision,
            "confidence": "medium",
            "experimental": True,
            "metrics": {"observation_count": 1},
        },
        "analysis_period": {},
        "aoi": {"centroid": {"longitude": -46.995, "latitude": -22.995}},
        "summary": {},
        "timeseries": [{"datetime": f"{observed_on}T10:00:00Z", "ndvi_mean": 0.5}],
    }
    if support_status is not None:
        payload["decision_support"] = {
            "status": support_status,
            "agreement": agreement,
            "model_version": "regrowth-support-v0",
            "suggestion": "nao_cortar",
            "confidence": "low",
            # Deliberately present: the engine must not interpret it as elapsed days.
            "score": 9999,
        }
    return payload


def _save(decision: str, at: datetime, **kwargs) -> str:
    payload = _payload(decision, **kwargs)
    with session_scope() as session:
        save_analysis(
            session,
            payload,
            geometry=GEOMETRY,
            created_at=at,
            alert_now=at,
        )
    return payload["analysis_id"]


def _alerts(alert_type: AlertType) -> list[Alert]:
    with session_scope() as session:
        return list(
            session.query(Alert)
            .filter(Alert.type == alert_type.value)
            .order_by(Alert.first_detected_at)
            .all()
        )


def _event_types(alert_id: str) -> list[str]:
    with session_scope() as session:
        return [
            event.event_type
            for event in session.query(AlertEvent)
            .filter(AlertEvent.alert_id == alert_id)
            .order_by(AlertEvent.id)
        ]


def test_recommendation_changes_have_directional_severity_and_resolve_opposite(
    alert_database,
):
    start = datetime(2026, 1, 1, 10)
    first = _save("nao_cortar", start)
    second = _save("cortar", start + timedelta(days=1))

    upward = _alerts(AlertType.RECOMMENDATION_CHANGED)[0]
    assert upward.severity == AlertSeverity.HIGH.value
    assert upward.previous_analysis_id == first
    assert upward.analysis_id == second
    assert upward.metadata_json["transition"] == "nao_cortar:cortar"

    third = _save("nao_cortar", start + timedelta(days=2))
    transitions = _alerts(AlertType.RECOMMENDATION_CHANGED)
    assert len(transitions) == 2
    assert transitions[0].status == AlertStatus.RESOLVED.value
    assert transitions[0].open_key is None
    assert transitions[0].last_analysis_id == third
    assert transitions[1].severity == AlertSeverity.MEDIUM.value
    assert transitions[1].metadata_json["transition"] == "cortar:nao_cortar"
    assert _event_types(transitions[0].id) == ["created", "auto_resolved"]


def test_inconclusive_transitions_are_ignored_by_recommendation_changed(
    alert_database,
):
    start = datetime(2026, 1, 1, 10)
    _save("nao_cortar", start)
    _save("inconclusivo", start + timedelta(days=1))
    _save("cortar", start + timedelta(days=2))

    transitions = _alerts(AlertType.RECOMMENDATION_CHANGED)
    assert len(transitions) == 1
    assert transitions[0].metadata_json["transition"] == "nao_cortar:cortar"


def test_cut_pending_at_seven_and_fourteen_days_then_resolves(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("cortar", start)
    day_7 = _save("cortar", start + timedelta(days=7))
    alert = _alerts(AlertType.CUT_PENDING)[0]
    assert alert.severity == AlertSeverity.MEDIUM.value
    assert alert.last_analysis_id == day_7
    assert alert.metadata_json["cut_pending_days"] == 7
    assert alert.metadata_json["not_a_mowing_date_estimate"] is True

    day_14 = _save("cortar", start + timedelta(days=14))
    alert = _alerts(AlertType.CUT_PENDING)[0]
    assert alert.severity == AlertSeverity.HIGH.value
    assert alert.last_analysis_id == day_14
    assert _event_types(alert.id) == ["created", "severity_changed"]

    ending = _save("inconclusivo", start + timedelta(days=15))
    alert = _alerts(AlertType.CUT_PENDING)[0]
    assert alert.status == AlertStatus.RESOLVED.value
    assert alert.last_analysis_id == ending
    assert _event_types(alert.id)[-1] == "auto_resolved"


def test_cut_pending_uses_consecutive_recommendations_not_support_score(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("cortar", start, support_status="available", agreement="diverge")
    _save("nao_cortar", start + timedelta(days=6))
    _save("cortar", start + timedelta(days=14))
    assert _alerts(AlertType.CUT_PENDING) == []


def test_reobservation_requires_a_previous_valid_decision_and_preserves_it(
    alert_database,
):
    start = datetime(2026, 1, 1, 10)
    previous = _save("cortar", start)
    current = _save("inconclusivo", start + timedelta(days=1))
    alert = _alerts(AlertType.REOBSERVATION_REQUIRED)[0]
    assert alert.severity == AlertSeverity.MEDIUM.value
    assert alert.previous_analysis_id == previous
    assert alert.last_analysis_id == current
    assert alert.metadata_json == {
        "last_valid_analysis_id": previous,
        "last_valid_recommendation": "cortar",
    }

    resolved_by = _save("nao_cortar", start + timedelta(days=2))
    alert = _alerts(AlertType.REOBSERVATION_REQUIRED)[0]
    assert alert.status == AlertStatus.RESOLVED.value
    assert alert.last_analysis_id == resolved_by


def test_inconclusive_without_valid_history_creates_no_reobservation(alert_database):
    _save("inconclusivo", datetime(2026, 1, 1, 10))
    assert _alerts(AlertType.REOBSERVATION_REQUIRED) == []


def test_stale_monitoring_at_thirty_and_sixty_days_and_new_observation_resolution(
    alert_database,
):
    created = datetime(2026, 1, 2, 10)
    _save("nao_cortar", created, observed_on="2026-01-01")
    with session_scope() as session:
        AlertEngine(session).evaluate_stale_monitoring(now=datetime(2026, 1, 31, 10))
    alert = _alerts(AlertType.STALE_MONITORING)[0]
    assert alert.severity == AlertSeverity.MEDIUM.value
    assert alert.metadata_json["stale_days"] == 30

    with session_scope() as session:
        AlertEngine(session).evaluate_stale_monitoring(now=datetime(2026, 3, 2, 10))
    alert = _alerts(AlertType.STALE_MONITORING)[0]
    assert alert.severity == AlertSeverity.HIGH.value
    assert _event_types(alert.id) == ["created", "severity_changed"]

    fresh = _save(
        "nao_cortar", datetime(2026, 3, 3, 10), observed_on="2026-03-03"
    )
    alert = _alerts(AlertType.STALE_MONITORING)[0]
    assert alert.status == AlertStatus.RESOLVED.value
    assert alert.last_analysis_id == fresh


def test_stale_temporal_recheck_same_instant_has_no_redundant_event(alert_database):
    _save("nao_cortar", datetime(2026, 1, 2, 10), observed_on="2026-01-01")
    now = datetime(2026, 1, 31, 10)
    with session_scope() as session:
        engine = AlertEngine(session)
        engine.evaluate_stale_monitoring(now=now)
        engine.evaluate_stale_monitoring(now=now)
    alert = _alerts(AlertType.STALE_MONITORING)[0]
    assert _event_types(alert.id) == ["created"]


@pytest.mark.parametrize(
    ("status", "agreement", "expected"),
    [
        ("available", "diverge", 1),
        ("available", "concorda", 0),
        ("unavailable", "diverge", 0),
    ],
)
def test_support_divergence_gate(alert_database, status, agreement, expected):
    _save(
        "cortar",
        datetime(2026, 1, 1, 10),
        support_status=status,
        agreement=agreement,
    )
    alerts = _alerts(AlertType.SUPPORT_DIVERGENCE)
    assert len(alerts) == expected
    if alerts:
        assert alerts[0].severity == AlertSeverity.LOW.value
        assert alerts[0].metadata_json["decision_changed"] is False
        assert "score" not in alerts[0].metadata_json


def test_unavailable_support_does_not_resolve_existing_divergence(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("cortar", start, support_status="available", agreement="diverge")
    _save(
        "cortar",
        start + timedelta(days=1),
        support_status="unavailable",
        agreement=None,
    )
    alert = _alerts(AlertType.SUPPORT_DIVERGENCE)[0]
    assert alert.status == AlertStatus.NEW.value
    assert _event_types(alert.id) == ["created"]


def test_same_analysis_processed_twice_is_idempotent(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("nao_cortar", start)
    analysis_id = str(uuid4())
    _save("cortar", start + timedelta(days=1), analysis_id=analysis_id)
    alert = _alerts(AlertType.RECOMMENDATION_CHANGED)[0]
    before = _event_types(alert.id)

    _save("cortar", start + timedelta(days=1), analysis_id=analysis_id)
    after = _alerts(AlertType.RECOMMENDATION_CHANGED)
    assert len(after) == 1
    assert _event_types(after[0].id) == before == ["created"]


def test_resolved_alert_can_recur_as_a_new_alert(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("cortar", start, support_status="available", agreement="diverge")
    _save("cortar", start + timedelta(days=1), support_status="available", agreement="concorda")
    _save("cortar", start + timedelta(days=2), support_status="available", agreement="diverge")

    alerts = _alerts(AlertType.SUPPORT_DIVERGENCE)
    assert len(alerts) == 2
    assert alerts[0].status == AlertStatus.RESOLVED.value
    assert alerts[0].open_key is None
    assert alerts[1].status == AlertStatus.NEW.value
    assert alerts[1].open_key is not None
    assert alerts[0].id != alerts[1].id


def test_persistent_condition_updates_once_per_new_analysis(alert_database):
    start = datetime(2026, 1, 1, 10)
    _save("cortar", start, support_status="available", agreement="diverge")
    latest = _save(
        "cortar",
        start + timedelta(days=1),
        support_status="available",
        agreement="diverge",
    )
    alert = _alerts(AlertType.SUPPORT_DIVERGENCE)[0]
    assert alert.last_analysis_id == latest
    assert _event_types(alert.id) == ["created", "condition_persisted"]


def test_concurrent_creation_keeps_one_open_alert_and_one_created_event(alert_database):
    """The unique open_key remains the final authority across DB sessions."""

    subject = geometry_identity(GEOMETRY).subject_key
    start = datetime(2026, 1, 1, 10)
    previous_id, current_id = str(uuid4()), str(uuid4())
    with session_scope() as session:
        session.add_all([
            Analysis(
                id=previous_id,
                created_at=start,
                status="completed",
                decision="nao_cortar",
                experimental=True,
                subject_kind="geometry",
                subject_key=subject,
                payload=_payload("nao_cortar", analysis_id=previous_id),
            ),
            Analysis(
                id=current_id,
                created_at=start + timedelta(days=1),
                status="completed",
                decision="cortar",
                experimental=True,
                subject_kind="geometry",
                subject_key=subject,
                payload=_payload("cortar", analysis_id=current_id),
            ),
        ])

    barrier = Barrier(2)

    def evaluate() -> None:
        with session_scope() as session:
            analysis = session.get(Analysis, current_id)
            assert analysis is not None
            barrier.wait(timeout=5)
            AlertEngine(session).evaluate_analysis(
                analysis, now=start + timedelta(days=1)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(evaluate) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    alerts = _alerts(AlertType.RECOMMENDATION_CHANGED)
    assert len(alerts) == 1
    assert alerts[0].open_key is not None
    assert _event_types(alerts[0].id) == ["created"]
