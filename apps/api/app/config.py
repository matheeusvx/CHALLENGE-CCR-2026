"""Configuracao do processo HTTP por variaveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .operational_profile import DEFAULT_ANALYSIS_TIMEZONE


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _validation_db_path() -> Path:
    raw_value = os.getenv("VALIDATION_DB_PATH")
    if not raw_value:
        return PROJECT_ROOT / "data" / "validation" / "validation.sqlite3"
    configured = Path(raw_value).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _validation_multisensor_benchmark_v2_path() -> Path:
    raw_value = os.getenv("VALIDATION_MULTISENSOR_BENCHMARK_V2_PATH")
    configured = Path(raw_value) if raw_value else Path(
        "outputs/validation/validation_multisensor_benchmark_v2.json"
    )
    configured = configured.expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _validation_holdout_benchmark_path() -> Path:
    raw_value = os.getenv("VALIDATION_HOLDOUT_BENCHMARK_PATH")
    configured = Path(raw_value) if raw_value else Path(
        "outputs/validation-holdout/validation_holdout_benchmark_v1.json"
    )
    configured = configured.expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _automatic_analysis_db_path() -> Path:
    raw_value = os.getenv("AUTO_ANALYSIS_DB_PATH")
    configured = Path(raw_value) if raw_value else Path(
        "data/automatic_analysis/automatic_analysis.sqlite3"
    )
    configured = configured.expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _automatic_analysis_roads_dataset_path() -> Path:
    raw_value = os.getenv("AUTO_ANALYSIS_ROADS_DATASET_PATH")
    configured = Path(raw_value) if raw_value else Path(
        "data/roads/processed/motiva-sp-roads-state.geojson"
    )
    configured = configured.expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _read_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


@dataclass(frozen=True)
class ApiSettings:
    host: str = os.getenv("API_HOST", "0.0.0.0")
    port: int = int(os.getenv("API_PORT", "8000"))
    output_root: Path = Path(
        os.getenv("API_OUTPUT_ROOT", "outputs/satellite_monitoring")
    )
    validation_db_path: Path = field(default_factory=_validation_db_path)
    validation_multisensor_benchmark_v2_path: Path = field(
        default_factory=_validation_multisensor_benchmark_v2_path
    )
    validation_holdout_benchmark_path: Path = field(
        default_factory=_validation_holdout_benchmark_path
    )
    automatic_analysis_db_path: Path = field(default_factory=_automatic_analysis_db_path)
    automatic_analysis_roads_dataset_path: Path = field(
        default_factory=_automatic_analysis_roads_dataset_path
    )
    cors_origins_raw: str = os.getenv(
        "API_CORS_ORIGINS", "http://localhost:3000"
    )
    analysis_timezone: str = os.getenv(
        "ANALYSIS_TIMEZONE", DEFAULT_ANALYSIS_TIMEZONE
    )
    multisource_enabled: bool = field(
        default_factory=lambda: _read_bool("MULTISOURCE_ENABLED")
    )
    gedi_enabled: bool = field(default_factory=lambda: _read_bool("GEDI_ENABLED"))
    icesat2_enabled: bool = field(
        default_factory=lambda: _read_bool("ICESAT2_ENABLED")
    )
    sentinel1_enabled: bool = field(
        default_factory=lambda: _read_bool("SENTINEL1_ENABLED")
    )
    sentinel1_collection: str = field(
        default_factory=lambda: os.getenv(
            "SENTINEL1_COLLECTION", "sentinel-1-grd"
        ).strip()
    )
    sentinel1_max_scenes: int = field(
        default_factory=lambda: int(os.getenv("SENTINEL1_MAX_SCENES", "8"))
    )
    height_estimation_enabled: bool = field(
        default_factory=lambda: _read_bool("HEIGHT_ESTIMATION_ENABLED")
    )
    spatial_segmentation_enabled: bool = field(
        default_factory=lambda: _read_bool("SPATIAL_SEGMENTATION_ENABLED")
    )
    spatial_section_length_m: int = field(
        default_factory=lambda: int(os.getenv("SPATIAL_SECTION_LENGTH_M", "50"))
    )
    spatial_regularization_enabled: bool = field(
        default_factory=lambda: _read_bool("SPATIAL_REGULARIZATION_ENABLED")
    )
    multisource_fusion_mode: str = field(
        default_factory=lambda: os.getenv("MULTISOURCE_FUSION_MODE", "disabled")
        .strip()
        .lower()
    )
    auto_analysis_enabled: bool = field(
        default_factory=lambda: _read_bool("AUTO_ANALYSIS_ENABLED")
    )
    auto_analysis_min_zoom: float = field(
        default_factory=lambda: float(os.getenv("AUTO_ANALYSIS_MIN_ZOOM", "14"))
    )
    auto_analysis_max_zoom: float = field(
        default_factory=lambda: float(os.getenv("AUTO_ANALYSIS_MAX_ZOOM", "22"))
    )
    auto_analysis_canonical_tile_zoom: int = field(
        default_factory=lambda: int(os.getenv("AUTO_ANALYSIS_CANONICAL_TILE_ZOOM", "17"))
    )
    auto_analysis_max_area_km2: float = field(
        default_factory=lambda: float(os.getenv("AUTO_ANALYSIS_MAX_AREA_KM2", "1000.0"))
    )
    auto_analysis_min_dimension_meters: float = field(
        default_factory=lambda: float(os.getenv("AUTO_ANALYSIS_MIN_DIMENSION_METERS", "20"))
    )
    auto_analysis_max_dimension_meters: float = field(
        default_factory=lambda: float(os.getenv("AUTO_ANALYSIS_MAX_DIMENSION_METERS", "50000"))
    )
    auto_analysis_cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("AUTO_ANALYSIS_CACHE_TTL_SECONDS", "21600"))
    )
    auto_analysis_in_progress_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("AUTO_ANALYSIS_IN_PROGRESS_TTL_SECONDS", "1800"))
    )
    auto_analysis_failure_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("AUTO_ANALYSIS_FAILURE_TTL_SECONDS", "300"))
    )
    auto_analysis_force_refresh_cooldown_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("AUTO_ANALYSIS_FORCE_REFRESH_COOLDOWN_SECONDS", "300")
        )
    )
    auto_analysis_max_concurrent: int = field(
        default_factory=lambda: int(os.getenv("AUTO_ANALYSIS_MAX_CONCURRENT", "2"))
    )
    auto_analysis_spatial_strategy: str = field(
        default_factory=lambda: os.getenv(
            "AUTO_ANALYSIS_SPATIAL_STRATEGY", "roadside_v1"
        ).strip().lower()
    )
    auto_analysis_roadside_min_zoom: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROADSIDE_MIN_ZOOM", "13")
        )
    )
    auto_analysis_road_snap_max_distance_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROAD_SNAP_MAX_DISTANCE_M", "50")
        )
    )
    auto_analysis_road_ambiguity_tolerance_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROAD_AMBIGUITY_TOLERANCE_M", "10")
        )
    )
    auto_analysis_road_segment_length_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROAD_SEGMENT_LENGTH_M", "300")
        )
    )
    auto_analysis_roadway_exclusion_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROADWAY_EXCLUSION_M", "25")
        )
    )
    auto_analysis_lateral_width_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_LATERAL_WIDTH_M", "40")
        )
    )
    auto_analysis_roadside_min_area_m2: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROADSIDE_MIN_AREA_M2", "5000")
        )
    )
    auto_analysis_roadside_min_width_m: float = field(
        default_factory=lambda: float(
            os.getenv("AUTO_ANALYSIS_ROADSIDE_MIN_WIDTH_M", "20")
        )
    )
    service_name: str = "motiva-vegetation-api"
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.multisource_fusion_mode not in {
            "disabled", "shadow", "experimental", "operational"
        }:
            raise ValueError(
                "MULTISOURCE_FUSION_MODE must be 'disabled', 'shadow', "
                "'experimental', or 'operational'."
            )
        if self.spatial_section_length_m <= 0:
            raise ValueError("SPATIAL_SECTION_LENGTH_M must be positive.")
        if not self.sentinel1_collection:
            raise ValueError("SENTINEL1_COLLECTION cannot be empty.")
        if self.sentinel1_max_scenes <= 0:
            raise ValueError("SENTINEL1_MAX_SCENES must be positive.")
        if not 0 <= self.auto_analysis_min_zoom <= self.auto_analysis_max_zoom <= 24:
            raise ValueError("Automatic analysis zoom limits must satisfy 0 <= min <= max <= 24.")
        if not 0 <= self.auto_analysis_canonical_tile_zoom <= 24:
            raise ValueError("AUTO_ANALYSIS_CANONICAL_TILE_ZOOM must be between 0 and 24.")
        if self.auto_analysis_spatial_strategy not in {"tile_v1", "roadside_v1"}:
            raise ValueError(
                "AUTO_ANALYSIS_SPATIAL_STRATEGY must be 'tile_v1' or 'roadside_v1'."
            )
        if not 0 <= self.auto_analysis_roadside_min_zoom <= self.auto_analysis_max_zoom:
            raise ValueError(
                "AUTO_ANALYSIS_ROADSIDE_MIN_ZOOM must be between 0 and AUTO_ANALYSIS_MAX_ZOOM."
            )
        if self.auto_analysis_road_snap_max_distance_m <= 0:
            raise ValueError("AUTO_ANALYSIS_ROAD_SNAP_MAX_DISTANCE_M must be positive.")
        if self.auto_analysis_road_ambiguity_tolerance_m < 0:
            raise ValueError(
                "AUTO_ANALYSIS_ROAD_AMBIGUITY_TOLERANCE_M cannot be negative."
            )
        if self.auto_analysis_road_segment_length_m <= 0:
            raise ValueError("AUTO_ANALYSIS_ROAD_SEGMENT_LENGTH_M must be positive.")
        for name, value in (
            ("AUTO_ANALYSIS_ROADWAY_EXCLUSION_M", self.auto_analysis_roadway_exclusion_m),
            ("AUTO_ANALYSIS_LATERAL_WIDTH_M", self.auto_analysis_lateral_width_m),
            ("AUTO_ANALYSIS_ROADSIDE_MIN_AREA_M2", self.auto_analysis_roadside_min_area_m2),
            ("AUTO_ANALYSIS_ROADSIDE_MIN_WIDTH_M", self.auto_analysis_roadside_min_width_m),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive.")
        if self.auto_analysis_max_area_km2 <= 0:
            raise ValueError("AUTO_ANALYSIS_MAX_AREA_KM2 must be positive.")
        if not 0 < self.auto_analysis_min_dimension_meters < self.auto_analysis_max_dimension_meters:
            raise ValueError("Automatic analysis dimensions must satisfy 0 < min < max.")
        for name, value in (
            ("AUTO_ANALYSIS_CACHE_TTL_SECONDS", self.auto_analysis_cache_ttl_seconds),
            ("AUTO_ANALYSIS_IN_PROGRESS_TTL_SECONDS", self.auto_analysis_in_progress_ttl_seconds),
            ("AUTO_ANALYSIS_FAILURE_TTL_SECONDS", self.auto_analysis_failure_ttl_seconds),
            (
                "AUTO_ANALYSIS_FORCE_REFRESH_COOLDOWN_SECONDS",
                self.auto_analysis_force_refresh_cooldown_seconds,
            ),
            ("AUTO_ANALYSIS_MAX_CONCURRENT", self.auto_analysis_max_concurrent),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive.")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


settings = ApiSettings()
