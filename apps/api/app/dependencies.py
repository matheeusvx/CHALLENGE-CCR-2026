"""Dependencias substituiveis e registro local de analises."""

from __future__ import annotations

from collections.abc import Callable

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.service import AnalysisResult, run_monitoring_analysis

AnalysisService = Callable[..., AnalysisResult]


def get_analysis_service() -> AnalysisService:
    return run_monitoring_analysis


class AnalysisRegistry:
    def __init__(self) -> None:
        self._results: dict[str, AnalysisResult] = {}

    def add(self, result: AnalysisResult) -> None:
        self._results[result.analysis_id] = result

    def get(self, analysis_id: str) -> AnalysisResult | None:
        return self._results.get(analysis_id)


analysis_registry = AnalysisRegistry()
