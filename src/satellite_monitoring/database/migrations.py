"""Small versioned migrations for the embedded operational database.

The project intentionally has no external migration service. These migrations
run transactionally at API startup and, unlike ``create_all()``, upgrade an
existing SQLite file in place.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, inspect, text

from .models import Alert, AlertEvent, MonitoredSection

ALERT_FOUNDATION_VERSION = "0001_alert_foundation"
MONITORING_CLAIMS_VERSION = "0002_monitoring_claims"

ANALYSIS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("subject_kind", "VARCHAR(24)"),
    ("subject_key", "VARCHAR(512)"),
    ("spatial_key", "VARCHAR(512)"),
    ("road_id", "VARCHAR(128)"),
    ("road_ref", "VARCHAR(64)"),
    ("road_name", "VARCHAR(200)"),
    ("axis_id", "VARCHAR(256)"),
    ("section_id", "VARCHAR(64)"),
    ("section_index", "INTEGER"),
    ("latest_valid_observation_on", "DATE"),
)

ANALYSIS_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_analysis_subject_created", "subject_key, created_at"),
    ("ix_analysis_spatial_key", "spatial_key"),
    ("ix_analysis_road_section", "road_ref, section_id"),
)


def _migration_applied(connection: Connection, version: str) -> bool:
    row = connection.execute(
        text("SELECT 1 FROM schema_migration WHERE version = :version"),
        {"version": version},
    ).first()
    return row is not None


def _upgrade_alert_foundation(connection: Connection) -> None:
    inspector = inspect(connection)
    if "analysis" not in inspector.get_table_names():
        raise RuntimeError("analysis table must exist before ALERT-01 migration")
    existing = {column["name"] for column in inspector.get_columns("analysis")}
    for name, sql_type in ANALYSIS_COLUMNS:
        if name not in existing:
            connection.exec_driver_sql(
                f'ALTER TABLE analysis ADD COLUMN "{name}" {sql_type}'
            )
    for name, columns in ANALYSIS_INDEXES:
        connection.exec_driver_sql(
            f'CREATE INDEX IF NOT EXISTS "{name}" ON analysis ({columns})'
        )
    Alert.__table__.create(connection, checkfirst=True)
    AlertEvent.__table__.create(connection, checkfirst=True)
    MonitoredSection.__table__.create(connection, checkfirst=True)


def _upgrade_monitoring_claims(connection: Connection) -> None:
    inspector = inspect(connection)
    if "monitored_section" not in inspector.get_table_names():
        MonitoredSection.__table__.create(connection, checkfirst=True)
        return
    existing = {
        column["name"] for column in inspector.get_columns("monitored_section")
    }
    for name, sql_type in (
        ("claimed_at", "DATETIME"),
        ("claim_token", "VARCHAR(64)"),
        ("claim_expires_at", "DATETIME"),
    ):
        if name not in existing:
            connection.exec_driver_sql(
                f'ALTER TABLE monitored_section ADD COLUMN "{name}" {sql_type}'
            )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_monitored_section_due "
        "ON monitored_section (enabled, next_due_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_monitored_section_claim "
        "ON monitored_section (claim_expires_at)"
    )


def run_migrations(engine: Engine) -> None:
    """Apply every pending embedded migration exactly once."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS schema_migration (
                version VARCHAR(80) PRIMARY KEY,
                applied_at DATETIME NOT NULL
            )"""
        )
        if not _migration_applied(connection, ALERT_FOUNDATION_VERSION):
            _upgrade_alert_foundation(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_migration(version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": ALERT_FOUNDATION_VERSION,
                    "applied_at": datetime.now(UTC).replace(tzinfo=None),
                },
            )
        if not _migration_applied(connection, MONITORING_CLAIMS_VERSION):
            _upgrade_monitoring_claims(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_migration(version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": MONITORING_CLAIMS_VERSION,
                    "applied_at": datetime.now(UTC).replace(tzinfo=None),
                },
            )
