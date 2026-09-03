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
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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
    __table_args__ = (Index("ix_analysis_created", "created_at"),)

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
