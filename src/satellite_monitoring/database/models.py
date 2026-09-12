"""Modelo relacional do historico operacional e dos dados de referencia da Motiva.

Duas familias de tabelas convivem aqui:

* Referencia (semeada com os dados fornecidos pela CCR Motiva): rodovia, marcos
  quilometricos, posicoes transversais, segmentos, condicao observada em campo e
  poligonos de classificacao de rocada.
* Operacional (gravada a cada execucao): analises de satelite e a serie temporal
  NDVI correspondente.

A decisao continua sendo produzida exclusivamente pelo motor de satelite. Esta
camada apenas registra o resultado; nao existe logica de decisao neste modulo.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Classes de altura do formulario unifilar da CCR (RA-RET-ROC-LIMP).
# 1 = h < 10 cm | 2 = 10 cm <= h <= 30 cm | 3 = h > 30 cm | X = nao se aplica.
HEIGHT_CLASS_RANGES: dict[str, tuple[float | None, float | None]] = {
    "1": (0.0, 10.0),
    "2": (10.0, 30.0),
    "3": (30.0, None),
    "X": (None, None),
}

# Limites contratuais de altura da vegetacao.
# 30 cm: ARTESP Anexo 06, item b.1.1 (folha 22) e ANTT PER p. 31, item 6 -
#        regra geral da faixa de dominio, largura minima de 4 m.
# 10 cm: mesmos documentos - "areas nobres" (acessos, trevos, pracas de pedagio,
#        postos de pesagem) e entornos de instalacoes operacionais e monumentos,
#        largura minima de 10 m.
DEFAULT_HEIGHT_LIMIT_CM = 30.0
STRICT_HEIGHT_LIMIT_CM = 10.0

# Mantido por compatibilidade com o alvo de classificacao ja usado no projeto.
HEIGHT_THRESHOLD_CM = DEFAULT_HEIGHT_LIMIT_CM


def exceeds_height_limit(height_class: str, height_limit_cm: float) -> bool | None:
    """Indica descumprimento do limite contratual para a classe observada.

    Retorna ``None`` quando a classe nao se aplica ao local (``X``). A avaliacao
    e conservadora: usa o piso da faixa de altura da classe, de modo que a classe
    2 (10 a 30 cm) ja descumpre um limite de 10 cm, mas nao um limite de 30 cm.
    """

    minimum, _maximum = HEIGHT_CLASS_RANGES.get(height_class, (None, None))
    if minimum is None:
        return None
    return minimum >= height_limit_cm


class Base(DeclarativeBase):
    pass


class Highway(Base):
    """Rodovia concessionada."""

    __tablename__ = "highway"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(160))
    length_km: Mapped[float | None] = mapped_column(Float)

    km_markers: Mapped[list["KmMarker"]] = relationship(back_populates="highway")
    segments: Mapped[list["Segment"]] = relationship(back_populates="highway")


class KmMarker(Base):
    """Marco quilometrico georreferenciado em EPSG:4326."""

    __tablename__ = "km_marker"
    __table_args__ = (UniqueConstraint("highway_id", "km", name="uq_km_marker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    highway_id: Mapped[int] = mapped_column(ForeignKey("highway.id"), index=True)
    km: Mapped[int] = mapped_column(Integer, index=True)
    longitude: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)

    highway: Mapped[Highway] = relationship(back_populates="km_markers")


class CrossSectionPosition(Base):
    """Posicao transversal da secao, itens 1.1 a 1.12 do unifilar."""

    __tablename__ = "cross_section_position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(120))
    side: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str | None] = mapped_column(String(32))
    # Limite contratual aplicavel a esta posicao. Depende do local, nao da
    # severidade: dispositivos e trevos respondem a 10 cm, o restante a 30 cm.
    height_limit_cm: Mapped[float] = mapped_column(
        Float, default=DEFAULT_HEIGHT_LIMIT_CM
    )
    regulatory_basis: Mapped[str | None] = mapped_column(String(160))

    segments: Mapped[list["Segment"]] = relationship(back_populates="position")


class Segment(Base):
    """Celula do unifilar: intervalo de quilometragem por posicao transversal."""

    __tablename__ = "segment"
    __table_args__ = (
        UniqueConstraint(
            "highway_id", "position_id", "chainage_start_m", name="uq_segment"
        ),
        Index("ix_segment_chainage", "highway_id", "chainage_start_m"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    highway_id: Mapped[int] = mapped_column(ForeignKey("highway.id"), index=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("cross_section_position.id"), index=True
    )
    chainage_start_m: Mapped[int] = mapped_column(Integer)
    chainage_end_m: Mapped[int] = mapped_column(Integer)

    highway: Mapped[Highway] = relationship(back_populates="segments")
    position: Mapped[CrossSectionPosition] = relationship(back_populates="segments")
    observations: Mapped[list["FieldConditionObservation"]] = relationship(
        back_populates="segment"
    )

    @property
    def km(self) -> float:
        return self.chainage_start_m / 1000.0


class FieldConditionObservation(Base):
    """Condicao de altura observada em campo para um segmento numa data."""

    __tablename__ = "field_condition_observation"
    __table_args__ = (
        UniqueConstraint("segment_id", "observed_on", name="uq_field_observation"),
        Index("ix_field_observation_date", "observed_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segment.id"), index=True)
    observed_on: Mapped[date] = mapped_column(Date)
    height_class: Mapped[str] = mapped_column(String(2), index=True)
    height_min_cm: Mapped[float | None] = mapped_column(Float)
    height_max_cm: Mapped[float | None] = mapped_column(Float)
    applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(48), default="ccr_unifilar")
    source_file: Mapped[str | None] = mapped_column(String(255))

    segment: Mapped[Segment] = relationship(back_populates="observations")

    @property
    def above_threshold(self) -> bool | None:
        """True quando a classe observada e a classe alta (h > 30 cm)."""

        if not self.applicable:
            return None
        return self.height_class == "3"

    @property
    def non_compliant(self) -> bool | None:
        """True quando a altura observada descumpre o limite do local.

        Usa o limite contratual da posicao transversal, e nao um valor unico:
        em dispositivos e trevos o limite e 10 cm, no restante da faixa e 30 cm.
        """

        if not self.applicable:
            return None
        limit = DEFAULT_HEIGHT_LIMIT_CM
        if self.segment is not None and self.segment.position is not None:
            limit = self.segment.position.height_limit_cm
        return exceeds_height_limit(self.height_class, limit)


class MowingPolygon(Base):
    """Poligono de classificacao de rocada com o metodo/equipamento exigido."""

    __tablename__ = "mowing_polygon"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    highway_id: Mapped[int] = mapped_column(ForeignKey("highway.id"), index=True)
    km: Mapped[int | None] = mapped_column(Integer, index=True)
    method: Mapped[str] = mapped_column(String(120), index=True)
    area_m2: Mapped[float | None] = mapped_column(Float)
    centroid_longitude: Mapped[float | None] = mapped_column(Float)
    centroid_latitude: Mapped[float | None] = mapped_column(Float)
    geometry: Mapped[dict[str, Any]] = mapped_column(JSON)


class Analysis(Base):
    """Execucao de analise de satelite e a decisao registrada."""

    __tablename__ = "analysis"
    __table_args__ = (
        Index("ix_analysis_created", "created_at"),
        Index("ix_analysis_subject_created", "subject_key", "created_at"),
        Index("ix_analysis_spatial_key", "spatial_key"),
        Index("ix_analysis_road_section", "road_ref", "section_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(32))

    decision: Mapped[str | None] = mapped_column(String(16), index=True)
    confidence: Mapped[str | None] = mapped_column(String(16), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    experimental: Mapped[bool] = mapped_column(Boolean, default=True)

    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    period_timezone: Mapped[str | None] = mapped_column(String(64))
    period_strategy: Mapped[str | None] = mapped_column(String(32))

    selected_area_m2: Mapped[float | None] = mapped_column(Float)
    effective_area_m2: Mapped[float | None] = mapped_column(Float)
    effective_area_pct: Mapped[float | None] = mapped_column(Float)

    analysis_quality_status: Mapped[str | None] = mapped_column(String(16))
    analysis_quality_score: Mapped[float | None] = mapped_column(Float)
    observation_count: Mapped[int | None] = mapped_column(Integer)

    current_ndvi_mean: Mapped[float | None] = mapped_column(Float)
    current_percentile: Mapped[float | None] = mapped_column(Float)
    recent_trend: Mapped[float | None] = mapped_column(Float)
    recent_trend_status: Mapped[str | None] = mapped_column(String(16))

    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    centroid_longitude: Mapped[float | None] = mapped_column(Float)
    centroid_latitude: Mapped[float | None] = mapped_column(Float)
    nearest_km: Mapped[int | None] = mapped_column(Integer, index=True)

    # Stable operational identity. Road-aware runs use the full roadside_v1
    # spatial key; manual runs use a canonical geometry fingerprint.
    subject_kind: Mapped[str | None] = mapped_column(String(24))
    subject_key: Mapped[str | None] = mapped_column(String(512))
    spatial_key: Mapped[str | None] = mapped_column(String(512))
    road_id: Mapped[str | None] = mapped_column(String(128))
    road_ref: Mapped[str | None] = mapped_column(String(64))
    road_name: Mapped[str | None] = mapped_column(String(200))
    axis_id: Mapped[str | None] = mapped_column(String(256))
    section_id: Mapped[str | None] = mapped_column(String(64))
    section_index: Mapped[int | None] = mapped_column(Integer)
    latest_valid_observation_on: Mapped[date | None] = mapped_column(Date)

    run_directory: Mapped[str | None] = mapped_column(String(512))
    # Caminhos de arquivo dos artefatos gerados, para que continuem acessiveis
    # depois de um reinicio da API.
    artifacts: Mapped[dict[str, str] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    observations: Mapped[list["AnalysisObservation"]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        order_by="AnalysisObservation.observed_on",
    )


class AnalysisObservation(Base):
    """Ponto da serie temporal NDVI usada pela analise."""

    __tablename__ = "analysis_observation"
    __table_args__ = (
        UniqueConstraint("analysis_id", "observed_on", name="uq_analysis_observation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analysis.id", ondelete="CASCADE"), index=True
    )
    observed_on: Mapped[date] = mapped_column(Date)
    ndvi_mean: Mapped[float | None] = mapped_column(Float)
    ndvi_median: Mapped[float | None] = mapped_column(Float)
    scene_quality_score: Mapped[float | None] = mapped_column(Float)
    valid_pixel_percentage: Mapped[float | None] = mapped_column(Float)

    analysis: Mapped[Analysis] = relationship(back_populates="observations")


class AlertType(StrEnum):
    RECOMMENDATION_CHANGED = "RECOMMENDATION_CHANGED"
    CUT_PENDING = "CUT_PENDING"
    REOBSERVATION_REQUIRED = "REOBSERVATION_REQUIRED"
    STALE_MONITORING = "STALE_MONITORING"
    SUPPORT_DIVERGENCE = "SUPPORT_DIVERGENCE"


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class Alert(Base):
    """Current alert projection. ALERT-01 only provides persistence."""

    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint(
            "type IN ('RECOMMENDATION_CHANGED','CUT_PENDING',"
            "'REOBSERVATION_REQUIRED','STALE_MONITORING','SUPPORT_DIVERGENCE')",
            name="ck_alert_type",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="ck_alert_severity",
        ),
        CheckConstraint(
            "status IN ('new','seen','monitoring','resolved')",
            name="ck_alert_status",
        ),
        Index("ix_alert_status_seen", "status", "last_seen_at"),
        Index("ix_alert_type_status", "type", "status"),
        Index("ix_alert_severity_status", "severity", "status"),
        Index("ix_alert_road_status", "road_ref", "section_id", "status"),
        Index("ix_alert_subject_history", "subject_key", "first_detected_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    subject_kind: Mapped[str] = mapped_column(String(24))
    subject_key: Mapped[str] = mapped_column(String(512))
    spatial_key: Mapped[str | None] = mapped_column(String(512))
    road_id: Mapped[str | None] = mapped_column(String(128))
    road_ref: Mapped[str | None] = mapped_column(String(64))
    road_name: Mapped[str | None] = mapped_column(String(200))
    axis_id: Mapped[str | None] = mapped_column(String(256))
    section_id: Mapped[str | None] = mapped_column(String(64))
    section_index: Mapped[int | None] = mapped_column(Integer)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis.id"))
    previous_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis.id"))
    last_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis.id"))
    current_recommendation: Mapped[str | None] = mapped_column(String(16))
    previous_recommendation: Mapped[str | None] = mapped_column(String(16))
    first_detected_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    open_key: Mapped[str | None] = mapped_column(String(640), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    events: Mapped[list["AlertEvent"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class AlertEvent(Base):
    """Append-only audit event for a future alert lifecycle."""

    __tablename__ = "alert_event"
    __table_args__ = (Index("ix_alert_event_alert_time", "alert_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alert.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis.id"))
    previous_status: Mapped[str | None] = mapped_column(String(24))
    new_status: Mapped[str | None] = mapped_column(String(24))
    severity: Mapped[str | None] = mapped_column(String(16))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    alert: Mapped[Alert] = relationship(back_populates="events")


class MonitoredSection(Base):
    """Persisted scheduler-ready subject state; no scheduling logic lives here."""

    __tablename__ = "monitored_section"

    subject_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(24))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    spatial_key: Mapped[str | None] = mapped_column(String(512))
    road_id: Mapped[str | None] = mapped_column(String(128))
    road_ref: Mapped[str | None] = mapped_column(String(64))
    road_name: Mapped[str | None] = mapped_column(String(200))
    axis_id: Mapped[str | None] = mapped_column(String(256))
    section_id: Mapped[str | None] = mapped_column(String(64))
    section_index: Mapped[int | None] = mapped_column(Integer)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(24))
    cadence_days: Mapped[int] = mapped_column(Integer, default=30)
    last_analysis_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_valid_observation_on: Mapped[date | None] = mapped_column(Date)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

class NdviFieldSample(Base):
    """Amostra de treino alinhando NDVI de satelite e condicao observada em campo.

    Cada linha e um par (quilometro, data de levantamento): a AOI e a uniao dos
    poligonos de rocada daquele km, e o rotulo vem das observacoes de campo da
    mesma data.

    A resolucao e quilometrica, e nao por posicao transversal, porque os
    poligonos entregues pela concessionaria carregam o metodo de rocada e o km,
    mas nao a posicao na secao. Sem o eixo da rodovia nao ha como derivar essa
    associacao com seguranca.
    """

    __tablename__ = "ndvi_field_sample"
    __table_args__ = (
        UniqueConstraint("km", "observed_on", name="uq_ndvi_field_sample"),
        Index("ix_ndvi_field_sample_date", "observed_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    km: Mapped[int] = mapped_column(Integer, index=True)
    observed_on: Mapped[date] = mapped_column(Date)

    # Alinhamento temporal com a cena Sentinel-2 escolhida.
    scene_item_id: Mapped[str | None] = mapped_column(String(160))
    scene_datetime: Mapped[str | None] = mapped_column(String(40))
    alignment_days: Mapped[int | None] = mapped_column(Integer)

    aoi_polygon_count: Mapped[int | None] = mapped_column(Integer)
    aoi_area_m2: Mapped[float | None] = mapped_column(Float)

    ndvi_mean: Mapped[float | None] = mapped_column(Float)
    ndvi_median: Mapped[float | None] = mapped_column(Float)
    ndvi_std: Mapped[float | None] = mapped_column(Float)
    ndvi_min: Mapped[float | None] = mapped_column(Float)
    ndvi_max: Mapped[float | None] = mapped_column(Float)

    valid_pixel_percentage: Mapped[float | None] = mapped_column(Float)
    valid_pixel_count: Mapped[int | None] = mapped_column(Integer)
    aoi_coverage_percentage: Mapped[float | None] = mapped_column(Float)
    scene_quality_score: Mapped[float | None] = mapped_column(Float)
    quality_status: Mapped[str | None] = mapped_column(String(16))
    accepted_for_timeseries: Mapped[bool] = mapped_column(Boolean, default=False)

    # Rotulos derivados do unifilar, agregados no quilometro.
    worst_height_class: Mapped[str | None] = mapped_column(String(2))
    applicable_count: Mapped[int | None] = mapped_column(Integer)
    non_compliant_count: Mapped[int | None] = mapped_column(Integer)
    non_compliant_share: Mapped[float | None] = mapped_column(Float)
    non_compliant_any: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(DateTime)

