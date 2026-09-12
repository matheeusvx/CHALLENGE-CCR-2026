"""Remove only records explicitly marked as Alertas demonstration data."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.satellite_monitoring.database import (
    Alert,
    AlertEvent,
    Analysis,
    MonitoredSection,
    init_database,
    session_scope,
)


def _is_demo(metadata: object) -> bool:
    return isinstance(metadata, Mapping) and metadata.get("demo_data") is True


def clear_demo_data(session: Session) -> dict[str, int]:
    """Delete demo rows in FK-safe order, leaving every real row untouched."""

    demo_alert_ids = [
        alert_id
        for alert_id, metadata in session.execute(
            select(Alert.id, Alert.metadata_json)
        )
        if _is_demo(metadata)
    ]
    demo_analysis_ids = [
        analysis_id
        for analysis_id, payload in session.execute(
            select(Analysis.id, Analysis.payload)
        )
        if _is_demo((payload or {}).get("metadata") if isinstance(payload, Mapping) else None)
    ]
    demo_subject_keys = [
        subject_key
        for subject_key, metadata in session.execute(
            select(MonitoredSection.subject_key, MonitoredSection.metadata_json)
        )
        if _is_demo(metadata)
    ]

    event_count = 0
    alert_count = 0
    section_count = 0
    analysis_count = 0
    if demo_alert_ids:
        event_result = session.execute(
            delete(AlertEvent).where(AlertEvent.alert_id.in_(demo_alert_ids))
        )
        event_count = int(event_result.rowcount or 0)
        alert_result = session.execute(delete(Alert).where(Alert.id.in_(demo_alert_ids)))
        alert_count = int(alert_result.rowcount or 0)
    if demo_subject_keys:
        section_result = session.execute(
            delete(MonitoredSection).where(
                MonitoredSection.subject_key.in_(demo_subject_keys)
            )
        )
        section_count = int(section_result.rowcount or 0)
    if demo_analysis_ids:
        analysis_result = session.execute(
            delete(Analysis).where(Analysis.id.in_(demo_analysis_ids))
        )
        analysis_count = int(analysis_result.rowcount or 0)
    session.flush()
    return {
        "alerts": alert_count,
        "events": event_count,
        "analyses": analysis_count,
        "monitored_sections": section_count,
    }


def main() -> int:
    init_database()
    with session_scope() as session:
        removed = clear_demo_data(session)
    print("Demo data removed:")
    for name, count in removed.items():
        print(f"- {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
