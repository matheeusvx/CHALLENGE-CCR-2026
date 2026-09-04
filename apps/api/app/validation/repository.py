"""Small SQLite repository with one connection per operation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1


class DuplicateAnalysisError(Exception):
    """Raised when V1 already contains a sample for an analysis."""


class ValidationSampleRepository:
    def __init__(self, database_path: str | Path, *, timeout: float = 10.0) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.timeout = timeout
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS validation_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS validation_samples (
                    sample_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    vegetation_class TEXT NOT NULL CHECK (
                        vegetation_class IN ('low_grass', 'tall_dense_grass', 'shrub', 'tree', 'mixed')
                    ),
                    maintenance_truth TEXT NOT NULL CHECK (
                        maintenance_truth IN ('cut', 'no_cut', 'uncertain')
                    ),
                    validation_source TEXT NOT NULL CHECK (
                        validation_source IN (
                            'visual_inspection', 'aerial_imagery', 'field_inspection',
                            'maintenance_record', 'other'
                        )
                    ),
                    reference_date TEXT NOT NULL,
                    notes TEXT CHECK (notes IS NULL OR length(notes) <= 1000),
                    selected_area_m2 REAL,
                    s2_decision TEXT,
                    s2_confidence TEXT,
                    s2_ndvi_mean REAL,
                    s2_ndvi_median REAL,
                    s2_current_percentile REAL,
                    s1_status TEXT,
                    s1_quality REAL,
                    s1_coverage REAL,
                    s1_canonical_relative_orbit INTEGER,
                    s1_canonical_observation_count INTEGER,
                    s1_vv_sigma0_linear REAL,
                    s1_vh_sigma0_linear REAL,
                    s1_vv_sigma0_db REAL,
                    s1_vh_sigma0_db REAL,
                    s1_vh_minus_vv_db REAL,
                    s1_vh_vv_sigma0_ratio REAL,
                    snapshot_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_validation_samples_created_at
                    ON validation_samples(created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_validation_samples_ground_truth
                    ON validation_samples(vegetation_class, maintenance_truth, validation_source);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO validation_schema_migrations(version, applied_at)
                VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (SCHEMA_VERSION,),
            )

    def insert(self, record: dict[str, Any]) -> dict[str, Any]:
        columns = (
            "sample_id", "analysis_id", "schema_version", "created_at",
            "vegetation_class", "maintenance_truth", "validation_source",
            "reference_date", "notes", "selected_area_m2", "s2_decision",
            "s2_confidence", "s2_ndvi_mean", "s2_ndvi_median",
            "s2_current_percentile", "s1_status", "s1_quality", "s1_coverage",
            "s1_canonical_relative_orbit", "s1_canonical_observation_count",
            "s1_vv_sigma0_linear", "s1_vh_sigma0_linear", "s1_vv_sigma0_db",
            "s1_vh_sigma0_db", "s1_vh_minus_vv_db",
            "s1_vh_vv_sigma0_ratio", "snapshot_json",
        )
        placeholders = ", ".join("?" for _ in columns)
        values = tuple(record.get(column) for column in columns)
        try:
            with self._connection() as connection:
                connection.execute(
                    f"INSERT INTO validation_samples ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
        except sqlite3.IntegrityError as exc:
            if "validation_samples.analysis_id" in str(exc).lower():
                raise DuplicateAnalysisError(record["analysis_id"]) from exc
            raise
        return self.get(record["sample_id"]) or record

    def get(self, sample_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM validation_samples WHERE sample_id = ?", (sample_id,)
            ).fetchone()
        return self._decode_row(row) if row else None

    def list(
        self,
        *,
        vegetation_class: str | None = None,
        maintenance_truth: str | None = None,
        validation_source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("vegetation_class", vegetation_class),
            ("maintenance_truth", maintenance_truth),
            ("validation_source", validation_source),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                parameters.append(value)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM validation_samples{clause}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM validation_samples{clause}
                ORDER BY created_at DESC, sample_id DESC LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return [self._decode_row(row, include_snapshot=False) for row in rows], total

    def all_rows(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM validation_samples ORDER BY created_at DESC, sample_id DESC"
            ).fetchall()
        return [self._decode_row(row, include_snapshot=False) for row in rows]

    @staticmethod
    def _decode_row(
        row: sqlite3.Row, *, include_snapshot: bool = True
    ) -> dict[str, Any]:
        value = dict(row)
        raw_snapshot = value.pop("snapshot_json", None)
        if include_snapshot and raw_snapshot is not None:
            value["snapshot"] = json.loads(raw_snapshot)
        return value
