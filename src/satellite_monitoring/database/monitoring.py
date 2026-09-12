"""Scheduler-ready, HTTP-independent monitoring orchestration."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal
from uuid import uuid4

from .alert_engine import AlertEngine
from .alert_repository import MonitoredSectionRepository
from .models import MonitoredSection
from .session import session_scope

logger = logging.getLogger(__name__)

AnalysisOutcome = Literal["completed", "cache_hit", "failed"]
SectionAnalyzer = Callable[[MonitoredSection], AnalysisOutcome]


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class MonitoringConfig:
    enabled: bool = False
    batch_size: int = 10
    default_cadence_days: int = 7
    claim_ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("MONITORING_BATCH_SIZE must be positive")
        if self.default_cadence_days <= 0:
            raise ValueError("MONITORING_DEFAULT_CADENCE_DAYS must be positive")
        if self.claim_ttl_seconds <= 0:
            raise ValueError("MONITORING_CLAIM_TTL_SECONDS must be positive")

    @classmethod
    def from_env(cls) -> "MonitoringConfig":
        return cls(
            enabled=_read_bool("MONITORING_ENABLED", False),
            batch_size=int(os.getenv("MONITORING_BATCH_SIZE", "10")),
            default_cadence_days=int(
                os.getenv("MONITORING_DEFAULT_CADENCE_DAYS", "7")
            ),
            claim_ttl_seconds=int(
                os.getenv("MONITORING_CLAIM_TTL_SECONDS", "3600")
            ),
        )


@dataclass(frozen=True)
class DueSection:
    subject_key: str
    subject_kind: str
    next_due_at: datetime
    overdue_seconds: int
    road_ref: str | None
    section_id: str | None


@dataclass
class MonitoringRunSummary:
    due: int = 0
    claimed: int = 0
    processed: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class MonitoringService:
    """Claim due road sections and invoke an injected existing-pipeline adapter."""

    def __init__(
        self,
        analyzer: SectionAnalyzer | None,
        *,
        config: MonitoringConfig | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.config = config or MonitoringConfig.from_env()

    def due_sections(
        self, *, now: datetime, limit: int | None = None, road: str | None = None
    ) -> list[DueSection]:
        current = _database_time(now)
        selected_limit = self.config.batch_size if limit is None else limit
        with session_scope() as session:
            records = MonitoredSectionRepository(session).list_due(
                now=current, limit=selected_limit, road=road
            )
            return [
                DueSection(
                    subject_key=item.subject_key,
                    subject_kind=item.subject_kind,
                    next_due_at=item.next_due_at,
                    overdue_seconds=max(
                        0, int((current - item.next_due_at).total_seconds())
                    ),
                    road_ref=item.road_ref,
                    section_id=item.section_id,
                )
                for item in records
                if item.next_due_at is not None
            ]

    def evaluate_temporal_alerts(self, *, now: datetime) -> int:
        with session_scope() as session:
            changed = AlertEngine(session).evaluate_stale_monitoring(
                now=_database_time(now)
            )
            count = len(changed)
        logger.info(
            "temporal_alerts_evaluated",
            extra={"changed": count, "evaluated_at": now.isoformat()},
        )
        return count

    def run_once(
        self,
        *,
        now: datetime,
        dry_run: bool = False,
        road: str | None = None,
        limit: int | None = None,
    ) -> MonitoringRunSummary:
        current = _database_time(now)
        due = self.due_sections(now=current, limit=limit, road=road)
        summary = MonitoringRunSummary(due=len(due))
        logger.info(
            "monitoring_run_started",
            extra={"dry_run": dry_run, "due": summary.due},
        )
        if dry_run:
            summary.skipped = len(due)
            for item in due:
                logger.info(
                    "section_skipped",
                    extra={
                        "subject_key": item.subject_key,
                        "reason": "dry_run",
                    },
                )
            self._log_completed(summary)
            return summary

        self.evaluate_temporal_alerts(now=current)
        if not self.config.enabled:
            summary.skipped = len(due)
            for item in due:
                logger.info(
                    "section_skipped",
                    extra={
                        "subject_key": item.subject_key,
                        "reason": "monitoring_disabled",
                    },
                )
            self._log_completed(summary)
            return summary
        for item in due:
            if item.subject_kind != "road_section":
                summary.skipped += 1
                logger.info(
                    "section_skipped",
                    extra={
                        "subject_key": item.subject_key,
                        "reason": "subject_kind_not_eligible",
                    },
                )
                continue
            token = str(uuid4())
            with session_scope() as session:
                claimed = MonitoredSectionRepository(session).claim(
                    item.subject_key,
                    token=token,
                    now=current,
                    expires_at=current
                    + timedelta(seconds=self.config.claim_ttl_seconds),
                )
                if claimed is not None:
                    session.expunge(claimed)
            if claimed is None:
                summary.skipped += 1
                logger.info(
                    "section_skipped",
                    extra={"subject_key": item.subject_key, "reason": "claim_lost"},
                )
                continue
            summary.claimed += 1
            summary.processed += 1
            logger.info(
                "section_claimed",
                extra={"subject_key": item.subject_key, "claim_token": token},
            )
            try:
                if self.analyzer is None:
                    raise RuntimeError("monitoring analyzer is not configured")
                logger.info(
                    "analysis_started", extra={"subject_key": item.subject_key}
                )
                outcome = self.analyzer(claimed)
                if outcome == "completed":
                    summary.completed += 1
                    logger.info(
                        "analysis_completed", extra={"subject_key": item.subject_key}
                    )
                elif outcome == "cache_hit":
                    summary.skipped += 1
                    logger.info(
                        "section_skipped",
                        extra={"subject_key": item.subject_key, "reason": "cache_hit"},
                    )
                else:
                    summary.failed += 1
                    logger.info(
                        "analysis_failed",
                        extra={"subject_key": item.subject_key, "reason": "failed"},
                    )
            except Exception as exc:  # one section must not abort the batch
                summary.failed += 1
                logger.exception(
                    "analysis_failed",
                    extra={
                        "subject_key": item.subject_key,
                        "error_type": type(exc).__name__,
                    },
                )
            finally:
                with session_scope() as session:
                    MonitoredSectionRepository(session).release_claim(
                        item.subject_key, token=token
                    )
        self._log_completed(summary)
        return summary

    @staticmethod
    def _log_completed(summary: MonitoringRunSummary) -> None:
        logger.info("monitoring_run_completed", extra=summary.to_dict())


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
