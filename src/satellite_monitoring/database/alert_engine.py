"""Deterministic, HTTP-independent operational alert evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .alert_repository import AlertEventRepository, AlertRepository
from .models import (
    Alert,
    AlertEvent,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Analysis,
    MonitoredSection,
)

VALID_RECOMMENDATIONS = {"cortar", "nao_cortar"}


@dataclass(frozen=True)
class AlertEngineConfig:
    cut_pending_days: int = 7
    stale_monitoring_days: int = 30

    def __post_init__(self) -> None:
        if self.cut_pending_days <= 0:
            raise ValueError("cut_pending_days must be positive")
        if self.stale_monitoring_days <= 0:
            raise ValueError("stale_monitoring_days must be positive")


class AlertEngine:
    """Evaluate persisted facts and update alert projections in one Session."""

    def __init__(
        self, session: Session, config: AlertEngineConfig | None = None
    ) -> None:
        self.session = session
        self.config = config or AlertEngineConfig()
        self.alerts = AlertRepository(session)
        self.events = AlertEventRepository(session)

    def evaluate_analysis(self, analysis: Analysis, *, now: datetime) -> list[Alert]:
        if not analysis.subject_key or analysis.status == "failed":
            return []
        changed: list[Alert] = []
        changed.extend(self._evaluate_recommendation_changed(analysis, now))
        changed.extend(self._evaluate_cut_pending(analysis, now))
        changed.extend(self._evaluate_reobservation(analysis, now))
        changed.extend(self._evaluate_support_divergence(analysis, now))
        stale = self._resolve_stale_with_new_observation(analysis, now)
        if stale is not None:
            changed.append(stale)
        return _unique_alerts(changed)

    def evaluate_stale_monitoring(self, *, now: datetime) -> list[Alert]:
        """Explicit temporal evaluation; callers decide when to invoke it."""

        changed: list[Alert] = []
        sections = self.session.execute(
            select(MonitoredSection).where(MonitoredSection.enabled.is_(True))
        ).scalars().all()
        threshold = self.config.stale_monitoring_days
        for section in sections:
            observed_on = section.last_valid_observation_on
            open_alert = self.alerts.get_open_by_type(
                section.subject_key, AlertType.STALE_MONITORING.value
            )
            if observed_on is None:
                continue
            stale_days = (now.date() - observed_on).days
            if stale_days < threshold:
                if open_alert is not None:
                    changed.append(
                        self._resolve(
                            open_alert,
                            now=now,
                            analysis_id=None,
                            reason="valid_observation_is_fresh",
                        )
                    )
                continue
            severity = self._age_severity(stale_days, threshold)
            metadata = {
                "last_valid_observation_on": observed_on.isoformat(),
                "stale_days": stale_days,
                "threshold_days": threshold,
            }
            if open_alert is None:
                changed.append(
                    self._create_from_section(
                        section,
                        AlertType.STALE_MONITORING.value,
                        severity,
                        now=now,
                        metadata=metadata,
                    )
                )
            else:
                updated = self._persist(
                    open_alert,
                    now=now,
                    analysis_id=None,
                    severity=severity,
                    metadata=metadata,
                )
                if updated is not None:
                    changed.append(updated)
        return _unique_alerts(changed)

    def _evaluate_recommendation_changed(
        self, analysis: Analysis, now: datetime
    ) -> list[Alert]:
        current = analysis.decision
        if current not in VALID_RECOMMENDATIONS:
            return []
        previous = self._previous_valid_analysis(analysis)
        alert_type = AlertType.RECOMMENDATION_CHANGED.value
        if previous is None or previous.decision == current:
            existing = self._open_transition_ending_in(analysis.subject_key, current)
            if existing is None:
                return []
            updated = self._persist(existing, now=now, analysis_id=analysis.id)
            return [updated] if updated is not None else []

        direction = f"{previous.decision}:{current}"
        opposite = f"{current}:{previous.decision}"
        changed: list[Alert] = []
        old = self.alerts.get_open_by_key(self._open_key(analysis.subject_key, alert_type, opposite))
        if old is not None:
            changed.append(
                self._resolve(
                    old,
                    now=now,
                    analysis_id=analysis.id,
                    reason="opposite_recommendation_transition",
                )
            )
        if self.events.created_for_analysis(alert_type, analysis.id):
            return changed
        severity = (
            AlertSeverity.HIGH.value
            if direction == "nao_cortar:cortar"
            else AlertSeverity.MEDIUM.value
        )
        changed.append(
            self._create_from_analysis(
                analysis,
                alert_type,
                severity,
                variant=direction,
                now=now,
                previous=previous,
                metadata={"transition": direction},
            )
        )
        return changed

    def _evaluate_cut_pending(self, analysis: Analysis, now: datetime) -> list[Alert]:
        alert_type = AlertType.CUT_PENDING.value
        existing = self.alerts.get_open_by_type(analysis.subject_key, alert_type)
        if analysis.decision != "cortar":
            if existing is None:
                return []
            return [
                self._resolve(
                    existing,
                    now=now,
                    analysis_id=analysis.id,
                    reason="cut_recommendation_streak_ended",
                )
            ]
        streak = self._consecutive_cut_analyses(analysis)
        if not streak:
            return []
        started_at = streak[0].created_at
        pending_days = max(0, (now.date() - started_at.date()).days)
        threshold = self.config.cut_pending_days
        if pending_days < threshold:
            return []
        metadata = {
            "cut_streak_started_at": started_at.isoformat(),
            "cut_pending_days": pending_days,
            "threshold_days": threshold,
            "not_a_mowing_date_estimate": True,
        }
        severity = self._age_severity(pending_days, threshold)
        if existing is not None:
            updated = self._persist(
                existing,
                now=now,
                analysis_id=analysis.id,
                severity=severity,
                metadata=metadata,
            )
            return [updated] if updated is not None else []
        if self.events.created_for_analysis(alert_type, analysis.id):
            return []
        return [
            self._create_from_analysis(
                analysis,
                alert_type,
                severity,
                variant="default",
                now=now,
                previous=streak[-2] if len(streak) > 1 else None,
                metadata=metadata,
            )
        ]

    def _evaluate_reobservation(self, analysis: Analysis, now: datetime) -> list[Alert]:
        alert_type = AlertType.REOBSERVATION_REQUIRED.value
        existing = self.alerts.get_open_by_type(analysis.subject_key, alert_type)
        if analysis.decision in VALID_RECOMMENDATIONS:
            if existing is None:
                return []
            return [
                self._resolve(
                    existing,
                    now=now,
                    analysis_id=analysis.id,
                    reason="valid_recommendation_observed",
                )
            ]
        if analysis.decision != "inconclusivo":
            return []
        previous = self._previous_valid_analysis(analysis)
        if previous is None:
            return []
        metadata = {
            "last_valid_analysis_id": previous.id,
            "last_valid_recommendation": previous.decision,
        }
        if existing is not None:
            updated = self._persist(
                existing,
                now=now,
                analysis_id=analysis.id,
                metadata=metadata,
            )
            return [updated] if updated is not None else []
        if self.events.created_for_analysis(alert_type, analysis.id):
            return []
        return [
            self._create_from_analysis(
                analysis,
                alert_type,
                AlertSeverity.MEDIUM.value,
                variant="default",
                now=now,
                previous=previous,
                metadata=metadata,
            )
        ]

    def _evaluate_support_divergence(
        self, analysis: Analysis, now: datetime
    ) -> list[Alert]:
        alert_type = AlertType.SUPPORT_DIVERGENCE.value
        existing = self.alerts.get_open_by_type(analysis.subject_key, alert_type)
        support = _support_payload(analysis.payload)
        status, agreement = support.get("status"), support.get("agreement")
        if status != "available":
            return []
        if agreement != "diverge":
            if existing is None:
                return []
            return [
                self._resolve(
                    existing,
                    now=now,
                    analysis_id=analysis.id,
                    reason="support_no_longer_diverges",
                )
            ]
        metadata = {
            "support_status": status,
            "agreement": agreement,
            "model_version": support.get("model_version"),
            "calibration_status": support.get("calibration_status"),
            "suggestion": support.get("suggestion"),
            "confidence": support.get("confidence"),
            "satellite_recommendation": analysis.decision,
            "decision_changed": False,
        }
        if existing is not None:
            updated = self._persist(
                existing,
                now=now,
                analysis_id=analysis.id,
                metadata=metadata,
            )
            return [updated] if updated is not None else []
        if self.events.created_for_analysis(alert_type, analysis.id):
            return []
        return [
            self._create_from_analysis(
                analysis,
                alert_type,
                AlertSeverity.LOW.value,
                variant="default",
                now=now,
                metadata=metadata,
            )
        ]

    def _resolve_stale_with_new_observation(
        self, analysis: Analysis, now: datetime
    ) -> Alert | None:
        if analysis.latest_valid_observation_on is None:
            return None
        existing = self.alerts.get_open_by_type(
            analysis.subject_key, AlertType.STALE_MONITORING.value
        )
        if existing is None:
            return None
        recorded = _as_date(existing.metadata_json.get("last_valid_observation_on"))
        if recorded is not None and analysis.latest_valid_observation_on <= recorded:
            return None
        return self._resolve(
            existing,
            now=now,
            analysis_id=analysis.id,
            reason="newer_valid_observation",
        )

    def _previous_valid_analysis(self, analysis: Analysis) -> Analysis | None:
        return self.session.execute(
            select(Analysis)
            .where(
                Analysis.subject_key == analysis.subject_key,
                Analysis.decision.in_(VALID_RECOMMENDATIONS),
                Analysis.status != "failed",
                or_(
                    Analysis.created_at < analysis.created_at,
                    and_(Analysis.created_at == analysis.created_at, Analysis.id < analysis.id),
                ),
            )
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(1)
        ).scalars().first()

    def _consecutive_cut_analyses(self, analysis: Analysis) -> list[Analysis]:
        history = self.session.execute(
            select(Analysis)
            .where(
                Analysis.subject_key == analysis.subject_key,
                Analysis.status != "failed",
                or_(
                    Analysis.created_at < analysis.created_at,
                    and_(Analysis.created_at == analysis.created_at, Analysis.id <= analysis.id),
                ),
            )
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        ).scalars().all()
        streak: list[Analysis] = []
        for item in history:
            if item.decision != "cortar":
                break
            streak.append(item)
        return list(reversed(streak))

    def _open_transition_ending_in(
        self, subject_key: str, recommendation: str
    ) -> Alert | None:
        return self.session.execute(
            select(Alert).where(
                Alert.subject_key == subject_key,
                Alert.type == AlertType.RECOMMENDATION_CHANGED.value,
                Alert.open_key.is_not(None),
                Alert.current_recommendation == recommendation,
            )
        ).scalars().first()

    def _create_from_analysis(
        self,
        analysis: Analysis,
        alert_type: str,
        severity: str,
        *,
        variant: str,
        now: datetime,
        previous: Analysis | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Alert:
        open_key = self._open_key(analysis.subject_key, alert_type, variant)
        alert = Alert(
            id=str(uuid4()),
            type=alert_type,
            severity=severity,
            status=AlertStatus.NEW.value,
            subject_kind=analysis.subject_kind or "geometry",
            subject_key=analysis.subject_key,
            spatial_key=analysis.spatial_key,
            road_id=analysis.road_id,
            road_ref=analysis.road_ref,
            road_name=analysis.road_name,
            axis_id=analysis.axis_id,
            section_id=analysis.section_id,
            section_index=analysis.section_index,
            analysis_id=analysis.id,
            previous_analysis_id=previous.id if previous else None,
            last_analysis_id=analysis.id,
            current_recommendation=analysis.decision,
            previous_recommendation=previous.decision if previous else None,
            first_detected_at=now,
            last_seen_at=now,
            updated_at=now,
            open_key=open_key,
            version=1,
            metadata_json=dict(metadata or {}),
        )
        try:
            with self.session.begin_nested():
                self.alerts.add(alert)
                self._event(alert, "created", now=now, analysis_id=analysis.id)
        except IntegrityError:
            existing = self.alerts.get_open_by_key(open_key)
            if existing is None:
                raise
            updated = self._persist(existing, now=now, analysis_id=analysis.id)
            return updated or existing
        return alert

    def _create_from_section(
        self,
        section: MonitoredSection,
        alert_type: str,
        severity: str,
        *,
        now: datetime,
        metadata: Mapping[str, Any],
    ) -> Alert:
        if section.last_analysis_at is None:
            raise ValueError("monitored section has no source analysis")
        analysis = self.session.execute(
            select(Analysis)
            .where(Analysis.subject_key == section.subject_key)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(1)
        ).scalars().one()
        return self._create_from_analysis(
            analysis,
            alert_type,
            severity,
            variant="default",
            now=now,
            metadata=metadata,
        )

    def _persist(
        self,
        alert: Alert,
        *,
        now: datetime,
        analysis_id: str | None,
        severity: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Alert | None:
        if analysis_id is not None and alert.last_analysis_id == analysis_id:
            return None
        severity_changed = severity is not None and severity != alert.severity
        if (
            analysis_id is None
            and not severity_changed
            and metadata is not None
            and alert.metadata_json == dict(metadata)
            and alert.last_seen_at == now
        ):
            return None
        if severity is not None:
            alert.severity = severity
        alert.last_seen_at = now
        alert.updated_at = now
        alert.version += 1
        if analysis_id is not None:
            alert.last_analysis_id = analysis_id
        if metadata is not None:
            alert.metadata_json = dict(metadata)
        self._event(
            alert,
            "severity_changed" if severity_changed else "condition_persisted",
            now=now,
            analysis_id=analysis_id,
            previous_status=alert.status,
        )
        self.session.flush()
        return alert

    def _resolve(
        self,
        alert: Alert,
        *,
        now: datetime,
        analysis_id: str | None,
        reason: str,
    ) -> Alert:
        previous_status = alert.status
        alert.status = AlertStatus.RESOLVED.value
        alert.resolved_at = now
        alert.updated_at = now
        alert.open_key = None
        alert.version += 1
        if analysis_id is not None:
            alert.last_analysis_id = analysis_id
        self._event(
            alert,
            "auto_resolved",
            now=now,
            analysis_id=analysis_id,
            metadata={"reason": reason},
            previous_status=previous_status,
        )
        self.session.flush()
        return alert

    def _event(
        self,
        alert: Alert,
        event_type: str,
        *,
        now: datetime,
        analysis_id: str | None,
        metadata: Mapping[str, Any] | None = None,
        previous_status: str | None = None,
    ) -> None:
        self.events.add(
            AlertEvent(
                alert_id=alert.id,
                event_type=event_type,
                occurred_at=now,
                analysis_id=analysis_id,
                previous_status=previous_status,
                new_status=alert.status,
                severity=alert.severity,
                metadata_json=dict(metadata or {}),
            )
        )

    @staticmethod
    def _open_key(subject_key: str, alert_type: str, variant: str) -> str:
        return f"{subject_key}|{alert_type}|{variant}"

    @staticmethod
    def _age_severity(age_days: int, threshold: int) -> str:
        return (
            AlertSeverity.HIGH.value
            if age_days >= threshold * 2
            else AlertSeverity.MEDIUM.value
        )


def _support_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    support = payload.get("decision_support")
    return support if isinstance(support, Mapping) else {}


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _unique_alerts(alerts: Sequence[Alert]) -> list[Alert]:
    return list({alert.id: alert for alert in alerts}.values())
