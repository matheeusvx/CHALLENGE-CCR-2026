"""Transaction-friendly persistence repositories for ALERT-01."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from .identity import AnalysisIdentity
from .models import Alert, AlertEvent, AlertStatus, Analysis, MonitoredSection


class AlertVersionConflict(RuntimeError):
    """The projection changed since the client read it."""


class InvalidAlertTransition(ValueError):
    """The requested operational status transition is not allowed."""


_ALLOWED_STATUS_TRANSITIONS = {
    AlertStatus.NEW.value: {
        AlertStatus.SEEN.value,
        AlertStatus.MONITORING.value,
        AlertStatus.RESOLVED.value,
    },
    AlertStatus.SEEN.value: {
        AlertStatus.MONITORING.value,
        AlertStatus.RESOLVED.value,
    },
    AlertStatus.MONITORING.value: {AlertStatus.RESOLVED.value},
    AlertStatus.RESOLVED.value: set(),
}


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, alert: Alert) -> Alert:
        self.session.add(alert)
        self.session.flush()
        return alert

    def get(self, alert_id: str) -> Alert | None:
        return self.session.get(Alert, alert_id)

    def get_analysis(self, analysis_id: str | None) -> Analysis | None:
        return self.session.get(Analysis, analysis_id) if analysis_id else None

    def list_filtered(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        alert_type: str | None = None,
        road: str | None = None,
        section_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, int, Sequence[Alert]]:
        filters = []
        if status is not None:
            filters.append(Alert.status == status)
        if severity is not None:
            filters.append(Alert.severity == severity)
        if alert_type is not None:
            filters.append(Alert.type == alert_type)
        if road is not None:
            normalized = road.strip().lower()
            filters.append(
                or_(
                    func.lower(Alert.road_id) == normalized,
                    func.lower(Alert.road_ref) == normalized,
                    func.lower(Alert.road_name).contains(normalized),
                )
            )
        if section_id is not None:
            filters.append(Alert.section_id == section_id)

        total = self.session.scalar(select(func.count(Alert.id)).where(*filters)) or 0
        active_count = self.session.scalar(
            select(func.count(Alert.id)).where(
                *filters, Alert.status != AlertStatus.RESOLVED.value
            )
        ) or 0
        active_order = case((Alert.status == AlertStatus.RESOLVED.value, 1), else_=0)
        severity_order = case(
            (Alert.severity == "critical", 0),
            (Alert.severity == "high", 1),
            (Alert.severity == "medium", 2),
            else_=3,
        )
        items = self.session.execute(
            select(Alert)
            .where(*filters)
            .order_by(active_order, severity_order, Alert.last_seen_at.desc(), Alert.id)
            .limit(limit)
            .offset(offset)
        ).scalars().all()
        return int(total), int(active_count), items

    def update_status(
        self,
        alert_id: str,
        *,
        expected_version: int,
        new_status: str,
        now: datetime,
    ) -> Alert | None:
        alert = self.get(alert_id)
        if alert is None:
            return None
        if alert.version != expected_version:
            raise AlertVersionConflict(alert_id)
        if new_status not in _ALLOWED_STATUS_TRANSITIONS.get(alert.status, set()):
            raise InvalidAlertTransition(f"{alert.status}:{new_status}")

        previous_status = alert.status
        values: dict[str, Any] = {
            "status": new_status,
            "updated_at": now,
            "version": Alert.version + 1,
        }
        if alert.acknowledged_at is None:
            values["acknowledged_at"] = now
        if new_status == AlertStatus.RESOLVED.value:
            values["resolved_at"] = now
            values["open_key"] = None

        result = self.session.execute(
            update(Alert)
            .where(Alert.id == alert_id, Alert.version == expected_version)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise AlertVersionConflict(alert_id)
        self.session.expire(alert)
        self.session.refresh(alert)
        AlertEventRepository(self.session).add(
            AlertEvent(
                alert_id=alert.id,
                event_type="status_changed",
                occurred_at=now,
                analysis_id=alert.last_analysis_id,
                previous_status=previous_status,
                new_status=new_status,
                severity=alert.severity,
                metadata_json={"source": "operator_api"},
            )
        )
        return alert

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
