"""Scoped manual jobs; shared executor and scientific route implementation."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import Executor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .exceptions import ApiError

logger = logging.getLogger(__name__)


class ManualAnalysisCoordinator:
    def __init__(self, path: Path, executor: Executor, *, max_pending: int = 2,
                 timeout_seconds: int = 1800) -> None:
        self.path = path
        self.executor = executor
        self.max_pending = max_pending
        self.timeout_seconds = timeout_seconds
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS manual_analysis_jobs (
                analysis_id TEXT PRIMARY KEY, operator_scope TEXT NOT NULL,
                status TEXT NOT NULL, deadline TEXT NOT NULL,
                result_json TEXT, error_code TEXT
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _expire(db: sqlite3.Connection) -> None:
        db.execute("""UPDATE manual_analysis_jobs SET status='failed', error_code='JOB_TIMEOUT'
                      WHERE status='in_progress' AND deadline < ?""",
                   (datetime.now(UTC).isoformat(),))

    def start(self, scope: str, execute: Callable[[str], dict]) -> dict:
        analysis_id = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            count = db.execute("SELECT COUNT(*) FROM manual_analysis_jobs WHERE status='in_progress'").fetchone()[0]
            if count >= self.max_pending:
                raise ApiError("ANALYSIS_BUSY", "Aguarde as análises em processamento.", status_code=429)
            db.execute("INSERT INTO manual_analysis_jobs VALUES (?, ?, 'in_progress', ?, NULL, NULL)",
                       (analysis_id, scope, (datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)).isoformat()))
        try:
            self.executor.submit(self._run, analysis_id, execute)
        except Exception:
            logger.exception("Falha ao agendar análise manual %s", analysis_id)
            self._finish(analysis_id, "failed", error_code="EXECUTOR_UNAVAILABLE")
            raise ApiError("EXECUTOR_UNAVAILABLE", "Não foi possível iniciar a análise.", status_code=503)
        return {"analysis_id": analysis_id, "status": "in_progress"}

    def _finish(self, analysis_id: str, status: str, *, result: dict | None = None,
                error_code: str | None = None) -> None:
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False) if result is not None else None
        with self._connect() as db:
            db.execute("""UPDATE manual_analysis_jobs SET status=?, result_json=?, error_code=?
                          WHERE analysis_id=? AND status='in_progress'""",
                       (status, serialized, error_code, analysis_id))

    def _run(self, analysis_id: str, execute: Callable[[str], dict]) -> None:
        try:
            result = execute(analysis_id)
            if result["status"] == "failed":
                raise ApiError("PROCESSING_ERROR", "O pipeline falhou.")
            self._finish(analysis_id, "completed", result=result)
        except Exception as exc:
            logger.exception("Falha na análise manual %s", analysis_id)
            self._finish(analysis_id, "failed", error_code=exc.code if isinstance(exc, ApiError) else "PROCESSING_ERROR")

    def get(self, analysis_id: str, scope: str) -> dict:
        with self._connect() as db:
            self._expire(db)
            row = db.execute("SELECT * FROM manual_analysis_jobs WHERE analysis_id=? AND operator_scope=?",
                             (analysis_id, scope)).fetchone()
        if row is None:
            raise ApiError("ANALYSIS_NOT_FOUND", "Análise não encontrada.", status_code=404)
        return {"analysis_id": row["analysis_id"], "status": row["status"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error_code": row["error_code"]}
