"""Configuracao do processo HTTP por variaveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    service_name: str = "motiva-vegetation-api"
    version: str = "0.1.0"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


settings = ApiSettings()
