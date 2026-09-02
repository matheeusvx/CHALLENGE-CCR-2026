"""Configuracao e validacao dos parametros do monitoramento."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION_ID = "sentinel-2-l2a"
DEFAULT_MAX_SCENES = 12
DEFAULT_MAX_CANDIDATE_SCENES = 40
DEFAULT_SCENE_ORDER = "newest"
DEFAULT_MIN_VALID_PIXEL_PERCENTAGE = 70.0
DEFAULT_MIN_VALID_PIXEL_COUNT = 30
DEFAULT_MIN_AOI_COVERAGE_PERCENTAGE = 95.0
DEFAULT_MIN_OBSERVATIONS = 4
DEFAULT_MEDIUM_QUALITY_THRESHOLD = 70.0
DEFAULT_HIGH_QUALITY_THRESHOLD = 85.0
DEFAULT_DAILY_AGGREGATION = "best"
DEFAULT_DECISION_MIN_OBSERVATIONS = 4
DEFAULT_HIGH_VEGETATION_PERCENTILE = 75.0
DEFAULT_SIGNIFICANT_DROP_ABSOLUTE = 0.06
DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE = 15.0
DEFAULT_TREND_WINDOW = 3
DEFAULT_MAX_GAP_DAYS = 20
DEFAULT_RECENT_INTERVENTION_DAYS = 20
DEFAULT_TEMPORAL_OUTLIER_MIN_DEVIATION = 0.06
DEFAULT_TEMPORAL_OUTLIER_MAD_MULTIPLIER = 3.0
DEFAULT_TEMPORAL_RETURN_RATIO = 0.5


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
    max_candidate_scenes: int = DEFAULT_MAX_CANDIDATE_SCENES
    scene_order: str = DEFAULT_SCENE_ORDER
    min_valid_pixel_percentage: float = DEFAULT_MIN_VALID_PIXEL_PERCENTAGE
    min_valid_pixel_count: int = DEFAULT_MIN_VALID_PIXEL_COUNT
    min_aoi_coverage_percentage: float = DEFAULT_MIN_AOI_COVERAGE_PERCENTAGE
    include_low_quality_scenes: bool = False
    min_observations: int = DEFAULT_MIN_OBSERVATIONS
    medium_quality_threshold: float = DEFAULT_MEDIUM_QUALITY_THRESHOLD
    high_quality_threshold: float = DEFAULT_HIGH_QUALITY_THRESHOLD
    output_root: Path = Path("outputs/satellite_monitoring")
    endpoint: str = STAC_ENDPOINT
    collection: str = COLLECTION_ID
    geometry_file: Path | None = None
    geometry: dict[str, Any] | None = None
    daily_aggregation: str = DEFAULT_DAILY_AGGREGATION
    decision_min_observations: int = DEFAULT_DECISION_MIN_OBSERVATIONS
    high_vegetation_percentile: float = DEFAULT_HIGH_VEGETATION_PERCENTILE
    significant_drop_absolute: float = DEFAULT_SIGNIFICANT_DROP_ABSOLUTE
    significant_drop_relative_percentage: float = (
        DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE
    )
    trend_window: int = DEFAULT_TREND_WINDOW
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS
    recent_intervention_days: int = DEFAULT_RECENT_INTERVENTION_DAYS
    temporal_outlier_min_deviation: float = DEFAULT_TEMPORAL_OUTLIER_MIN_DEVIATION
    temporal_outlier_mad_multiplier: float = DEFAULT_TEMPORAL_OUTLIER_MAD_MULTIPLIER
    temporal_return_ratio: float = DEFAULT_TEMPORAL_RETURN_RATIO
    height_estimation_enabled: bool = False
    spatial_segmentation_enabled: bool = False
    spatial_regularization_enabled: bool = False

    def __post_init__(self) -> None:
        circular_values = (self.latitude, self.longitude, self.radius_meters)
        has_any_circular_value = any(value is not None for value in circular_values)
        has_all_circular_values = all(value is not None for value in circular_values)
        has_geometry_file = self.geometry_file is not None
        has_inline_geometry = self.geometry is not None
        geometry_mode_count = int(has_geometry_file) + int(has_inline_geometry)

        if geometry_mode_count > 1 or (geometry_mode_count and has_any_circular_value):
            raise ValueError(
                "Informe geometry, geometry-file ou latitude/longitude/raio, "
                "nunca os dois modos juntos."
            )
        if geometry_mode_count == 0 and not has_any_circular_value:
            raise ValueError(
                "Informe geometry-file, geometry ou os tres parametros "
                "latitude, longitude e radius-meters."
            )
        if geometry_mode_count == 0 and not has_all_circular_values:
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
        if self.max_candidate_scenes <= 0:
            raise ValueError("A quantidade maxima de cenas candidatas deve ser maior que zero.")
        if self.scene_order not in {"newest", "oldest"}:
            raise ValueError("A ordem das cenas deve ser 'newest' ou 'oldest'.")
        if not 0 <= self.min_valid_pixel_percentage <= 100:
            raise ValueError("O percentual minimo de pixels validos deve estar entre 0 e 100.")
        if self.min_valid_pixel_count <= 0:
            raise ValueError("A quantidade minima de pixels validos deve ser maior que zero.")
        if not 0 <= self.min_aoi_coverage_percentage <= 100:
            raise ValueError("A cobertura minima da AOI deve estar entre 0 e 100.")
        if self.min_observations <= 0:
            raise ValueError("A quantidade minima de observacoes deve ser maior que zero.")
        if not 0 <= self.medium_quality_threshold <= self.high_quality_threshold <= 100:
            raise ValueError(
                "Os limites de qualidade devem respeitar 0 <= medium <= high <= 100."
            )
        if self.daily_aggregation not in {"best", "median", "none"}:
            raise ValueError("A agregacao diaria deve ser 'best', 'median' ou 'none'.")
        if self.decision_min_observations <= 0:
            raise ValueError("O minimo de observacoes para decisao deve ser maior que zero.")
        if not 0 <= self.high_vegetation_percentile <= 100:
            raise ValueError("O percentil de vegetacao alta deve estar entre 0 e 100.")
        if not 0 < self.significant_drop_absolute <= 2:
            raise ValueError("A queda absoluta significativa deve estar entre 0 e 2.")
        if self.significant_drop_relative_percentage <= 0:
            raise ValueError("A queda relativa significativa deve ser maior que zero.")
        if self.trend_window < 2:
            raise ValueError("A janela de tendencia deve conter pelo menos duas observacoes.")
        if self.max_gap_days <= 0:
            raise ValueError("O intervalo maximo entre observacoes deve ser maior que zero.")
        if self.recent_intervention_days <= 0:
            raise ValueError("A janela de intervencao recente deve ser maior que zero.")
        if self.temporal_outlier_min_deviation <= 0:
            raise ValueError("O desvio minimo para outlier temporal deve ser maior que zero.")
        if self.temporal_outlier_mad_multiplier <= 0:
            raise ValueError("O multiplicador MAD deve ser maior que zero.")
        if not 0 <= self.temporal_return_ratio <= 1:
            raise ValueError("A razao de retorno temporal deve estar entre 0 e 1.")

    @property
    def datetime_range(self) -> str:
        assert self.start_date is not None
        assert self.end_date is not None
        return f"{self.start_date.isoformat()}/{self.end_date.isoformat()}"

    @property
    def effective_max_candidate_scenes(self) -> int:
        """Preserva max-scenes configuravel sem reduzir o pool solicitado pela CLI."""
        return max(self.max_candidate_scenes, self.max_scenes)

    @property
    def quality_thresholds(self) -> dict[str, Any]:
        """Explicita os limiares provisorios usados pela avaliacao de qualidade."""
        return {
            "high_min_valid_pixel_percentage": self.high_quality_threshold,
            "medium_min_valid_pixel_percentage": self.medium_quality_threshold,
            "accepted_min_valid_pixel_percentage": self.min_valid_pixel_percentage,
            "accepted_min_valid_pixel_count": self.min_valid_pixel_count,
            "accepted_min_aoi_coverage_percentage": self.min_aoi_coverage_percentage,
            "provisional": True,
        }

    @property
    def recommendation_thresholds(self) -> dict[str, Any]:
        """Explicita os parametros experimentais usados pela recomendacao."""
        return {
            "decision_min_observations": self.decision_min_observations,
            "high_vegetation_percentile": self.high_vegetation_percentile,
            "significant_drop_absolute": self.significant_drop_absolute,
            "significant_drop_relative_percentage": (
                self.significant_drop_relative_percentage
            ),
            "trend_window": self.trend_window,
            "max_gap_days": self.max_gap_days,
            "recent_intervention_days": self.recent_intervention_days,
            "experimental": True,
            "validated_by_motiva": False,
        }

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        if not self.spatial_segmentation_enabled:
            values.pop("spatial_segmentation_enabled", None)
        if not self.spatial_regularization_enabled:
            values.pop("spatial_regularization_enabled", None)
        values["start_date"] = self.start_date.isoformat() if self.start_date else None
        values["end_date"] = self.end_date.isoformat() if self.end_date else None
        values["geometry_file"] = str(self.geometry_file) if self.geometry_file else None
        values["output_root"] = str(self.output_root)
        return values
