"""Configuracao e validacao dos parametros do monitoramento."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION_ID = "sentinel-2-l2a"


def parse_iso_date(value: str) -> date:
    """Converte uma data ISO (AAAA-MM-DD) com mensagem de erro objetiva."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Data invalida: {value}. Use o formato AAAA-MM-DD.") from exc


@dataclass(frozen=True)
class MonitoringConfig:
    """Parametros de uma execucao do pipeline Sentinel-2."""

    latitude: float
    longitude: float
    radius_meters: float
    start_date: date
    end_date: date
    max_cloud_cover: float = 20.0
    max_scenes: int = 10
    output_root: Path = Path("outputs/satellite_monitoring")
    endpoint: str = STAC_ENDPOINT
    collection: str = COLLECTION_ID

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("Latitude deve estar entre -90 e 90 graus.")
        if not -180 <= self.longitude <= 180:
            raise ValueError("Longitude deve estar entre -180 e 180 graus.")
        if self.radius_meters <= 0:
            raise ValueError("O raio deve ser maior que zero.")
        if self.start_date > self.end_date:
            raise ValueError("A data inicial nao pode ser posterior a data final.")
        if not 0 <= self.max_cloud_cover <= 100:
            raise ValueError("A cobertura maxima de nuvens deve estar entre 0 e 100.")
        if self.max_scenes <= 0:
            raise ValueError("A quantidade maxima de cenas deve ser maior que zero.")

    @property
    def datetime_range(self) -> str:
        return f"{self.start_date.isoformat()}/{self.end_date.isoformat()}"

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["start_date"] = self.start_date.isoformat()
        values["end_date"] = self.end_date.isoformat()
        values["output_root"] = str(self.output_root)
        return values
