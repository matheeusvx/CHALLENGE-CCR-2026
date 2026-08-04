"""Configuracao e validacao dos parametros do monitoramento."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION_ID = "sentinel-2-l2a"
DEFAULT_MAX_SCENES = 12
DEFAULT_SCENE_ORDER = "newest"
DEFAULT_MIN_VALID_PIXEL_PERCENTAGE = 70.0
DEFAULT_MIN_OBSERVATIONS = 4
DEFAULT_MEDIUM_QUALITY_THRESHOLD = 70.0
DEFAULT_HIGH_QUALITY_THRESHOLD = 85.0


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
    max_scenes: int = DEFAULT_MAX_SCENES
    scene_order: str = DEFAULT_SCENE_ORDER
    min_valid_pixel_percentage: float = DEFAULT_MIN_VALID_PIXEL_PERCENTAGE
    include_low_quality_scenes: bool = False
    min_observations: int = DEFAULT_MIN_OBSERVATIONS
    medium_quality_threshold: float = DEFAULT_MEDIUM_QUALITY_THRESHOLD
    high_quality_threshold: float = DEFAULT_HIGH_QUALITY_THRESHOLD
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
        if self.scene_order not in {"newest", "oldest"}:
            raise ValueError("A ordem das cenas deve ser 'newest' ou 'oldest'.")
        if not 0 <= self.min_valid_pixel_percentage <= 100:
            raise ValueError("O percentual minimo de pixels validos deve estar entre 0 e 100.")
        if self.min_observations <= 0:
            raise ValueError("A quantidade minima de observacoes deve ser maior que zero.")
        if not 0 <= self.medium_quality_threshold <= self.high_quality_threshold <= 100:
            raise ValueError(
                "Os limites de qualidade devem respeitar 0 <= medium <= high <= 100."
            )

    @property
    def datetime_range(self) -> str:
        return f"{self.start_date.isoformat()}/{self.end_date.isoformat()}"

    @property
    def quality_thresholds(self) -> dict[str, Any]:
        """Explicita os limiares provisorios usados pela avaliacao de qualidade."""
        return {
            "high_min_valid_pixel_percentage": self.high_quality_threshold,
            "medium_min_valid_pixel_percentage": self.medium_quality_threshold,
            "accepted_min_valid_pixel_percentage": self.min_valid_pixel_percentage,
            "provisional": True,
        }

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["start_date"] = self.start_date.isoformat()
        values["end_date"] = self.end_date.isoformat()
        values["output_root"] = str(self.output_root)
        return values
