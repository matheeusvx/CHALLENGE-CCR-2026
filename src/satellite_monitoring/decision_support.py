"""Apoio a decisao a partir do historico operacional.

Combina a ultima condicao observada em campo pela CCR Motiva com o modelo de
rebrota para produzir uma evidencia de apoio a leitura do satelite.

Contrato inegociavel deste modulo: **a decisao do satelite nunca e alterada**.
O retorno e puramente aditivo e serve para o operador confirmar ou questionar
uma recomendacao, sobretudo quando a confianca sai baixa ou `inconclusivo`.

O apoio se degrada de forma explicita em vez de inventar certeza:

* ``available``            -> ha levantamento recente e o modelo opina;
* ``stale_field_data``     -> ha historico, mas antigo demais para extrapolar;
                              devolve apenas contexto descritivo;
* ``insufficient_history`` -> nao ha observacao de campo para o quilometro;
* ``unavailable``          -> modelo ausente ou erro ao avaliar.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .database.analytics import KilometreContext, kilometre_context, worst_case_position
from .models.regrowth_support import (
    CALIBRATION_STATUS,
    MAX_FIELD_DATA_AGE_DAYS,
    MODEL_VERSION,
    PREDICTION_HORIZON_DAYS,
    agreement_with,
    classify_support,
    model_feature_vector,
    score_non_compliance,
    support_confidence,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "regrowth_support_v0.json"
)


@lru_cache(maxsize=4)
def load_support_model(path: str | Path = DEFAULT_MODEL_PATH) -> dict[str, Any] | None:
    """Carrega o artefato treinado. Ausencia do arquivo nao e erro."""

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.exception("Artefato do modelo de apoio esta ilegivel.")
        return None


@dataclass
class DecisionSupport:
    status: str
    experimental: bool = True
    model_version: str | None = None
    calibration_status: str | None = None
    suggestion: str | None = None
    score: float | None = None
    confidence: str | None = None
    agreement: str | None = None
    reference_km: int | None = None
    last_survey_on: str | None = None
    days_since_survey: int | None = None
    prediction_horizon_days: int = PREDICTION_HORIZON_DAYS
    factors: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _context_payload(context: KilometreContext) -> dict[str, Any]:
    share = context.non_compliant_share
    metodo = next(iter(context.mowing_methods), None)
    return {
        "observation_count": context.observation_count,
        "applicable_count": context.applicable_count,
        "non_compliant_count": context.non_compliant_count,
        "non_compliant_share": None if share is None else round(share, 4),
        "class_counts": context.class_counts,
        "dominant_mowing_method": metodo,
        "positions": context.positions,
    }


def _describe_position(position: dict[str, Any]) -> str:
    limite = int(position["height_limit_cm"])
    return (
        f"{position['description']} no km "
        f"{position['chainage_start_m'] / 1000:.1f}: classe "
        f"{position['height_class']} para um limite de {limite} cm"
    )


def build_decision_support(
    session: Session,
    *,
    km: int | None,
    reference_date: date,
    decision: str | None = None,
    model_path: str | Path = DEFAULT_MODEL_PATH,
) -> DecisionSupport:
    """Monta o apoio para uma analise localizada no quilometro informado."""

    if km is None:
        return DecisionSupport(
            status="insufficient_history",
            factors=["A area analisada nao pode ser associada a um marco quilometrico."],
        )

    context = kilometre_context(session, km)
    if context.last_survey_on is None:
        return DecisionSupport(
            status="insufficient_history",
            reference_km=km,
            factors=[f"Nao ha levantamento de campo registrado para o km {km}."],
        )

    idade = (reference_date - context.last_survey_on).days
    contexto = _context_payload(context)
    base = DecisionSupport(
        status="available",
        model_version=MODEL_VERSION,
        calibration_status=CALIBRATION_STATUS,
        reference_km=km,
        last_survey_on=context.last_survey_on.isoformat(),
        days_since_survey=idade,
        context=contexto,
    )

    share = context.non_compliant_share
    if share is not None:
        base.factors.append(
            f"No ultimo levantamento, {context.non_compliant_count} de "
            f"{context.applicable_count} pontos do km {km} estavam acima do limite."
        )
    if contexto["dominant_mowing_method"]:
        base.factors.append(
            f"Metodo de rocada predominante no trecho: {contexto['dominant_mowing_method']}."
        )

    if idade > MAX_FIELD_DATA_AGE_DAYS:
        base.status = "stale_field_data"
        base.factors.insert(
            0,
            f"O levantamento de campo mais recente tem {idade} dias, acima do "
            f"horizonte de {MAX_FIELD_DATA_AGE_DAYS} dias sustentado pelo modelo. "
            "O apoio fica descritivo e nao sugere decisao.",
        )
        return base

    model = load_support_model(model_path)
    if model is None:
        base.status = "unavailable"
        base.factors.insert(0, "O modelo de apoio ainda nao foi treinado.")
        return base

    position = worst_case_position(context)
    if position is None:
        base.status = "insufficient_history"
        base.factors.insert(0, f"Nenhum ponto aplicavel observado no km {km}.")
        return base

    try:
        features = model_feature_vector(
            {
                "current_height_class": float(position["height_class"]),
                "height_limit_cm": float(position["height_limit_cm"]),
            }
        )
        score = score_non_compliance(features, model)
    except (KeyError, TypeError, ValueError):
        logger.exception("Falha ao avaliar o modelo de apoio.")
        base.status = "unavailable"
        base.factors.insert(0, "Nao foi possivel avaliar o modelo de apoio.")
        return base

    base.score = round(score, 4)
    base.suggestion = classify_support(score)
    base.confidence = support_confidence(score)
    base.agreement = agreement_with(decision, base.suggestion)
    base.factors.insert(0, f"Ponto mais critico: {_describe_position(position)}.")
    base.factors.append(
        f"Projecao valida para cerca de {PREDICTION_HORIZON_DAYS} dias apos o "
        "levantamento; o modelo e experimental e nao substitui a leitura do satelite."
    )
    return base
