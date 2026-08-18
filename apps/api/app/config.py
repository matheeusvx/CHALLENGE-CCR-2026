"""Configuracao do processo HTTP por variaveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .operational_profile import DEFAULT_ANALYSIS_TIMEZONE


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
    height_estimation_enabled: bool = field(
        default_factory=lambda: _read_bool("HEIGHT_ESTIMATION_ENABLED")
    )
    multisource_fusion_mode: str = field(
        default_factory=lambda: os.getenv("MULTISOURCE_FUSION_MODE", "disabled")
        .strip()
        .lower()
    )
    service_name: str = "motiva-vegetation-api"
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.multisource_fusion_mode not in {"disabled", "shadow"}:
            raise ValueError(
                "MULTISOURCE_FUSION_MODE must be 'disabled' or 'shadow'."
            )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


settings = ApiSettings()
