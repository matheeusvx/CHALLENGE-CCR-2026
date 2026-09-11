"""Transaction-friendly persistence repositories for ALERT-01."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .identity import AnalysisIdentity
from .models import Alert, AlertEvent, MonitoredSection


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, alert: Alert) -> Alert:
        self.session.add(alert)
        self.session.flush()
        return alert

    def get(self, alert_id: str) -> Alert | None:
        return self.session.get(Alert, alert_id)

    def get_open_by_key(self, open_key: str) -> Alert | None:
        return self.session.execute(
            select(Alert).where(Alert.open_key == open_key)
        ).scalars().first()

    def list_by_subject_key(self, subject_key: str) -> Sequence[Alert]:
        return self.session.execute(
            select(Alert)
            .where(Alert.subject_key == subject_key)
            .order_by(Alert.first_detected_at.desc())
        ).scalars().all()

    def list_open_by_subject_key(self, subject_key: str) -> Sequence[Alert]:
        return self.session.execute(
            select(Alert).where(
                Alert.subject_key == subject_key,
                Alert.open_key.is_not(None),
            )
        ).scalars().all()

    def get_open_by_type(self, subject_key: str, alert_type: str) -> Alert | None:
        return self.session.execute(
            select(Alert).where(
                Alert.subject_key == subject_key,
                Alert.type == alert_type,
                Alert.open_key.is_not(None),
            )
        ).scalars().first()


class AlertEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: AlertEvent) -> AlertEvent:
        self.session.add(event)
        self.session.flush()
        return event

    def list_for_alert(self, alert_id: str) -> Sequence[AlertEvent]:
        return self.session.execute(
            select(AlertEvent)
            .where(AlertEvent.alert_id == alert_id)
            .order_by(AlertEvent.occurred_at, AlertEvent.id)
        ).scalars().all()

    def created_for_analysis(self, alert_type: str, analysis_id: str) -> bool:
        return self.session.execute(
            select(AlertEvent.id)
            .join(Alert, Alert.id == AlertEvent.alert_id)
            .where(
                Alert.type == alert_type,
                AlertEvent.event_type == "created",
                AlertEvent.analysis_id == analysis_id,
            )
            .limit(1)
        ).first() is not None


class MonitoredSectionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_subject_key(self, subject_key: str) -> MonitoredSection | None:
        return self.session.get(MonitoredSection, subject_key)

    def upsert(
        self,
        identity: AnalysisIdentity,
        *,
        geometry: Mapping[str, Any] | None,
        source: str,
        analysis_at: datetime,
        latest_valid_observation_on: date | None,
        cadence_days: int = 30,
        metadata: Mapping[str, Any] | None = None,
    ) -> MonitoredSection:
        if cadence_days <= 0:
            raise ValueError("cadence_days must be positive")
        record = self.session.get(MonitoredSection, identity.subject_key)
        if record is None:
            record = MonitoredSection(
                subject_key=identity.subject_key,
                subject_kind=identity.subject_kind,
                created_at=analysis_at,
            )
            self.session.add(record)
        is_latest_analysis = (
            record.last_analysis_at is None or analysis_at >= record.last_analysis_at
        )
        record.subject_kind = identity.subject_kind
        record.enabled = True if record.enabled is None else record.enabled
        if is_latest_analysis:
            record.spatial_key = identity.spatial_key
            record.road_id = identity.road_id
            record.road_ref = identity.road_ref
            record.road_name = identity.road_name
            record.axis_id = identity.axis_id
            record.section_id = identity.section_id
            record.section_index = identity.section_index
            record.geometry = dict(geometry) if geometry else None
            record.source = source
        record.cadence_days = record.cadence_days or cadence_days
        if is_latest_analysis:
            record.last_analysis_at = analysis_at
        if latest_valid_observation_on is not None and (
            record.last_valid_observation_on is None
            or latest_valid_observation_on > record.last_valid_observation_on
        ):
            record.last_valid_observation_on = latest_valid_observation_on
        assert record.last_analysis_at is not None
        record.next_due_at = record.last_analysis_at + timedelta(
            days=record.cadence_days
        )
        record.updated_at = max(record.updated_at or analysis_at, analysis_at)
        if metadata is not None:
            record.metadata_json = dict(metadata)
        elif record.metadata_json is None:
            record.metadata_json = {}
        self.session.flush()
        return record
