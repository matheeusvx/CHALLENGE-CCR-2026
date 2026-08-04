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

    latitude: float | None = None
    longitude: float | None = None
    radius_meters: float | None = None
    start_date: date | None = None
    end_date: date | None = None
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
    geometry_file: Path | None = None

    def __post_init__(self) -> None:
        circular_values = (self.latitude, self.longitude, self.radius_meters)
        has_any_circular_value = any(value is not None for value in circular_values)
        has_all_circular_values = all(value is not None for value in circular_values)
        has_geometry_file = self.geometry_file is not None

        if has_geometry_file and has_any_circular_value:
            raise ValueError(
                "Informe geometry-file ou latitude/longitude/raio, nunca os dois modos juntos."
            )
        if not has_geometry_file and not has_any_circular_value:
            raise ValueError(
                "Informe geometry-file ou os tres parametros latitude, longitude e radius-meters."
            )
        if not has_geometry_file and not has_all_circular_values:
            raise ValueError(
                "O modo circular exige latitude, longitude e radius-meters em conjunto."
            )

        if has_all_circular_values:
            assert self.latitude is not None
            assert self.longitude is not None
            assert self.radius_meters is not None
            if not -90 <= self.latitude <= 90:
                raise ValueError("Latitude deve estar entre -90 e 90 graus.")
            if not -180 <= self.longitude <= 180:
                raise ValueError("Longitude deve estar entre -180 e 180 graus.")
            if self.radius_meters <= 0:
                raise ValueError("O raio deve ser maior que zero.")

        if self.start_date is None or self.end_date is None:
            raise ValueError("As datas inicial e final sao obrigatorias.")
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
        assert self.start_date is not None
        assert self.end_date is not None
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
        values["start_date"] = self.start_date.isoformat() if self.start_date else None
        values["end_date"] = self.end_date.isoformat() if self.end_date else None
        values["geometry_file"] = str(self.geometry_file) if self.geometry_file else None
        values["output_root"] = str(self.output_root)
        return values
