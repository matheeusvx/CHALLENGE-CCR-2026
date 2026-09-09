"""Dependencias substituiveis e registro local de analises."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.database import get_analysis, session_scope
from src.satellite_monitoring.config import STAC_ENDPOINT
from src.satellite_monitoring.multisource.providers.sentinel1 import Sentinel1Provider
from src.satellite_monitoring.service import AnalysisResult, run_monitoring_analysis

from .config import settings
from .validation.repository import ValidationSampleRepository
from .validation.temporal_benchmark import ValidationTemporalBenchmarkRunner

logger = logging.getLogger(__name__)

AnalysisService = Callable[..., AnalysisResult]


def get_analysis_service() -> AnalysisService:
    return run_monitoring_analysis


def get_analysis_now() -> datetime:
    """Relogio injetavel usado para definir o periodo oficial da analise."""

    return datetime.now(ZoneInfo(settings.analysis_timezone))


@dataclass(frozen=True)
class StoredAnalysis:
    """Analise recuperada do banco, suficiente para servir artefatos."""

    analysis_id: str
    artifacts: dict[str, str]
    run_directory: Path | None


class AnalysisRegistry:
    """Cache em memoria com retorno ao banco.

    Mantem o comportamento anterior para a execucao corrente e, quando o
    identificador nao esta em memoria (por exemplo, apos reiniciar a API),
    recupera os caminhos gravados no historico.
    """

    def __init__(self) -> None:
        self._results: dict[str, AnalysisResult] = {}

    def add(self, result: AnalysisResult) -> None:
        self._results[result.analysis_id] = result

    def get(self, analysis_id: str) -> AnalysisResult | StoredAnalysis | None:
        cached = self._results.get(analysis_id)
        if cached is not None:
            return cached
        return self._load_from_history(analysis_id)

    @staticmethod
    def _load_from_history(analysis_id: str) -> StoredAnalysis | None:
        try:
            with session_scope() as session:
                record = get_analysis(session, analysis_id)
                if record is None or not record.artifacts:
                    return None
                return StoredAnalysis(
                    analysis_id=record.id,
                    artifacts=dict(record.artifacts),
                    run_directory=(
                        Path(record.run_directory) if record.run_directory else None
                    ),
                )
        except Exception:  # pragma: no cover - persistencia nunca quebra a rota
            logger.exception("Falha ao consultar o historico de analises.")
            return None

    def clear(self) -> None:
        self._results.clear()


analysis_registry = AnalysisRegistry()


def get_analysis_registry() -> AnalysisRegistry:
    return analysis_registry


def get_validation_repository() -> ValidationSampleRepository:
    # The repository owns no persistent Connection. Each operation opens and closes
    # its own SQLite connection, which is safe for FastAPI's worker threads.
    return ValidationSampleRepository(settings.validation_db_path)


def get_validation_multisensor_benchmark_v2_path() -> Path:
    """Injectable location of the frozen, offline-generated V2 artifact."""

    return settings.validation_multisensor_benchmark_v2_path


def get_validation_holdout_benchmark_path() -> Path:
    """Injectable location of the frozen, offline-generated holdout artifact."""

    return settings.validation_holdout_benchmark_path


def get_validation_temporal_benchmark_runner() -> ValidationTemporalBenchmarkRunner:
    return ValidationTemporalBenchmarkRunner(
        Sentinel1Provider(
            endpoint=STAC_ENDPOINT,
            collection=settings.sentinel1_collection,
            max_scenes=settings.sentinel1_max_scenes,
        ),
        output_root=settings.output_root,
    )
