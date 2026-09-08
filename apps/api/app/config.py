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
    service_name: str = "motiva-vegetation-api"
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.multisource_fusion_mode not in {"disabled", "shadow", "operational"}:
            raise ValueError(
                "MULTISOURCE_FUSION_MODE must be 'disabled', 'shadow', or 'operational'."
            )
        if self.spatial_section_length_m <= 0:
            raise ValueError("SPATIAL_SECTION_LENGTH_M must be positive.")
        if not self.sentinel1_collection:
            raise ValueError("SENTINEL1_COLLECTION cannot be empty.")
        if self.sentinel1_max_scenes <= 0:
            raise ValueError("SENTINEL1_MAX_SCENES must be positive.")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


settings = ApiSettings()
