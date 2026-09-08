"""Dependencias substituiveis e registro local de analises."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from src.satellite_monitoring.config import STAC_ENDPOINT
from src.satellite_monitoring.multisource.providers.sentinel1 import Sentinel1Provider
from src.satellite_monitoring.road_geometry import LocalGeoJsonRoadGeometryProvider
from src.satellite_monitoring.service import AnalysisResult, run_monitoring_analysis

from .config import settings
from .automatic_analysis import (
    AutomaticAnalysisCoordinator,
    AutomaticAnalysisPolicy,
    AutomaticAnalysisRepository,
)
from .validation.repository import ValidationSampleRepository
from .validation.temporal_benchmark import ValidationTemporalBenchmarkRunner

AnalysisService = Callable[..., AnalysisResult]


def get_analysis_service() -> AnalysisService:
    return run_monitoring_analysis


def get_analysis_now() -> datetime:
    """Relogio injetavel usado para definir o periodo oficial da analise."""

    return datetime.now(ZoneInfo(settings.analysis_timezone))


class AnalysisRegistry:
    def __init__(self) -> None:
        self._results: dict[str, AnalysisResult] = {}
        self._lock = RLock()

    def add(self, result: AnalysisResult) -> None:
        with self._lock:
            self._results[result.analysis_id] = result

    def get(self, analysis_id: str) -> AnalysisResult | None:
        with self._lock:
            return self._results.get(analysis_id)

    def clear(self) -> None:
        with self._lock:
            self._results.clear()


analysis_registry = AnalysisRegistry()
_automatic_analysis_coordinator: AutomaticAnalysisCoordinator | None = None
_automatic_analysis_lock = RLock()


def get_analysis_registry() -> AnalysisRegistry:
    return analysis_registry


def get_automatic_analysis_coordinator() -> AutomaticAnalysisCoordinator:
    global _automatic_analysis_coordinator
    with _automatic_analysis_lock:
        if _automatic_analysis_coordinator is None:
            _automatic_analysis_coordinator = AutomaticAnalysisCoordinator(
                AutomaticAnalysisRepository(settings.automatic_analysis_db_path),
                AutomaticAnalysisPolicy(
                    enabled=settings.auto_analysis_enabled,
                    min_zoom=settings.auto_analysis_min_zoom,
                    max_zoom=settings.auto_analysis_max_zoom,
                    canonical_tile_zoom=settings.auto_analysis_canonical_tile_zoom,
                    max_area_km2=settings.auto_analysis_max_area_km2,
                    min_dimension_meters=settings.auto_analysis_min_dimension_meters,
                    max_dimension_meters=settings.auto_analysis_max_dimension_meters,
                    cache_ttl_seconds=settings.auto_analysis_cache_ttl_seconds,
                    in_progress_ttl_seconds=settings.auto_analysis_in_progress_ttl_seconds,
                    failure_ttl_seconds=settings.auto_analysis_failure_ttl_seconds,
                    force_refresh_cooldown_seconds=(
                        settings.auto_analysis_force_refresh_cooldown_seconds
                    ),
                    max_concurrent=settings.auto_analysis_max_concurrent,
                    spatial_strategy=settings.auto_analysis_spatial_strategy,
                    roadside_min_zoom=settings.auto_analysis_roadside_min_zoom,
                    road_snap_max_distance_m=(
                        settings.auto_analysis_road_snap_max_distance_m
                    ),
                    road_ambiguity_tolerance_m=(
                        settings.auto_analysis_road_ambiguity_tolerance_m
                    ),
                    road_segment_length_m=(
                        settings.auto_analysis_road_segment_length_m
                    ),
                    roadway_exclusion_m=settings.auto_analysis_roadway_exclusion_m,
                    lateral_width_m=settings.auto_analysis_lateral_width_m,
                    roadside_min_area_m2=settings.auto_analysis_roadside_min_area_m2,
                    roadside_min_width_m=settings.auto_analysis_roadside_min_width_m,
                ),
                road_geometry_provider=LocalGeoJsonRoadGeometryProvider(
                    settings.automatic_analysis_roads_dataset_path
                ),
            )
        return _automatic_analysis_coordinator


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
