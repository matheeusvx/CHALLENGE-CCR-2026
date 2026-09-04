"""Consultas analiticas sobre o historico de campo.

Duas responsabilidades:

* montar o conjunto de treino do modelo de apoio, a partir das transicoes
  observadas entre levantamentos consecutivos da CCR Motiva;
* montar o contexto de inferencia de um quilometro, usado para explicar a
  situacao do trecho ao operador.

Nada aqui produz recomendacao. A decisao operacional continua vindo do motor
de satelite.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    CrossSectionPosition,
    FieldConditionObservation,
    KmMarker,
    MowingPolygon,
    Segment,
    exceeds_height_limit,
)


@dataclass(frozen=True)
class TransitionSample:
    """Uma celula do unifilar observada em dois levantamentos consecutivos."""

    segment_id: int
    km: int
    position_code: str
    height_limit_cm: float
    current_height_class: str
    next_height_class: str
    observed_on: date
    next_observed_on: date

    @property
    def elapsed_days(self) -> int:
        return (self.next_observed_on - self.observed_on).days

    @property
    def non_compliant_next(self) -> bool | None:
        return exceeds_height_limit(self.next_height_class, self.height_limit_cm)


def survey_dates(session: Session) -> list[date]:
    """Datas distintas de levantamento de campo, em ordem cronologica."""

    statement = (
        select(FieldConditionObservation.observed_on)
        .distinct()
        .order_by(FieldConditionObservation.observed_on)
    )
    return list(session.execute(statement).scalars().all())


def build_transition_dataset(session: Session) -> list[TransitionSample]:
    """Pares (t, t+1) de cada segmento entre levantamentos consecutivos.

    Celulas marcadas como ``X`` (nao se aplica ao local) sao descartadas nas
    duas pontas, porque nao representam vegetacao observavel.
    """

    statement = (
        select(
            Segment.id,
            Segment.chainage_start_m,
            CrossSectionPosition.code,
            CrossSectionPosition.height_limit_cm,
            FieldConditionObservation.observed_on,
            FieldConditionObservation.height_class,
        )
        .join(CrossSectionPosition, Segment.position_id == CrossSectionPosition.id)
        .join(FieldConditionObservation, FieldConditionObservation.segment_id == Segment.id)
        .order_by(Segment.id, FieldConditionObservation.observed_on)
    )

    por_segmento: dict[int, list[tuple[date, str]]] = defaultdict(list)
    metadados: dict[int, tuple[int, str, float]] = {}
    for segment_id, chainage, code, limit, observed_on, height_class in session.execute(statement):
        por_segmento[segment_id].append((observed_on, height_class))
        metadados[segment_id] = (int(chainage // 1000), code, float(limit))

    amostras: list[TransitionSample] = []
    for segment_id, observacoes in por_segmento.items():
        km, code, limit = metadados[segment_id]
        for (data_atual, classe_atual), (data_prox, classe_prox) in zip(
            observacoes, observacoes[1:]
        ):
            if classe_atual == "X" or classe_prox == "X":
                continue
            amostras.append(
                TransitionSample(
                    segment_id=segment_id,
                    km=km,
                    position_code=code,
                    height_limit_cm=limit,
                    current_height_class=classe_atual,
                    next_height_class=classe_prox,
                    observed_on=data_atual,
                    next_observed_on=data_prox,
                )
            )
    return sorted(amostras, key=lambda item: (item.segment_id, item.observed_on))


@dataclass
class KilometreContext:
    """Situacao de campo conhecida para um quilometro da rodovia."""

    km: int
    last_survey_on: date | None = None
    observation_count: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    non_compliant_count: int = 0
    applicable_count: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)
    mowing_methods: dict[str, float] = field(default_factory=dict)

    @property
    def non_compliant_share(self) -> float | None:
        if not self.applicable_count:
            return None
        return self.non_compliant_count / self.applicable_count


def kilometre_context(session: Session, km: int) -> KilometreContext:
    """Ultima condicao observada em campo para o quilometro informado."""

    context = KilometreContext(km=km)

    latest = session.execute(
        select(func.max(FieldConditionObservation.observed_on))
        .join(Segment, FieldConditionObservation.segment_id == Segment.id)
        .where(Segment.chainage_start_m >= km * 1000)
        .where(Segment.chainage_start_m < (km + 1) * 1000)
    ).scalar()
    if latest is None:
        return context
    context.last_survey_on = latest

    rows = session.execute(
        select(
            CrossSectionPosition.code,
            CrossSectionPosition.description,
            CrossSectionPosition.height_limit_cm,
            Segment.chainage_start_m,
            FieldConditionObservation.height_class,
        )
        .join(Segment, FieldConditionObservation.segment_id == Segment.id)
        .join(CrossSectionPosition, Segment.position_id == CrossSectionPosition.id)
        .where(Segment.chainage_start_m >= km * 1000)
        .where(Segment.chainage_start_m < (km + 1) * 1000)
        .where(FieldConditionObservation.observed_on == latest)
        .order_by(CrossSectionPosition.code, Segment.chainage_start_m)
    ).all()

    contagem: dict[str, int] = defaultdict(int)
    for code, description, limit, chainage, height_class in rows:
        context.observation_count += 1
        contagem[height_class] += 1
        if height_class == "X":
            continue
        context.applicable_count += 1
        if exceeds_height_limit(height_class, float(limit)):
            context.non_compliant_count += 1
        context.positions.append(
            {
                "position_code": code,
                "description": description,
                "height_limit_cm": float(limit),
                "chainage_start_m": int(chainage),
                "height_class": height_class,
            }
        )
    context.class_counts = dict(contagem)

    metodos = session.execute(
        select(MowingPolygon.method, func.sum(MowingPolygon.area_m2))
        .where(MowingPolygon.km == km)
        .group_by(MowingPolygon.method)
        .order_by(func.sum(MowingPolygon.area_m2).desc())
    ).all()
    context.mowing_methods = {
        str(method): float(area or 0.0) for method, area in metodos
    }
    return context


def worst_case_position(context: KilometreContext) -> dict[str, Any] | None:
    """Posicao mais critica do quilometro: maior classe sob o menor limite."""

    if not context.positions:
        return None
    return max(
        context.positions,
        key=lambda item: (
            int(item["height_class"]) if item["height_class"].isdigit() else 0,
            -item["height_limit_cm"],
        ),
    )


def available_kilometres(session: Session) -> Sequence[int]:
    statement = select(KmMarker.km).distinct().order_by(KmMarker.km)
    return list(session.execute(statement).scalars().all())
