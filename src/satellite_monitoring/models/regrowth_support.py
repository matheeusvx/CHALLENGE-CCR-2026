"""Modelo de apoio por historico de rebrota.

Estima a probabilidade de a vegetacao de um trecho estar acima do limite
contratual do local, a partir da ultima condicao observada em campo pela
CCR Motiva.

Este modelo **nao decide**. A recomendacao operacional continua sendo produzida
exclusivamente pelo motor de satelite (`cut_recommendation.recommend_cut`); o
score daqui entra na resposta apenas como evidencia de apoio, util sobretudo
quando a decisao do satelite sai com confianca baixa ou `inconclusivo`.

Limitacoes conhecidas, deliberadamente explicitas:

* treinado sobre uma unica janela de 7 dias (dois levantamentos de campo),
  entao o horizonte de validade e curto e a extrapolacao para semanas ou meses
  nao tem base empirica;
* poucos exemplos positivos, o que torna a precisao instavel;
* o alvo e a classe declarada no unifilar, nao uma medicao fisica de altura.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

MODEL_VERSION = "regrowth-support-v0"
MODEL_FEATURES = ("current_height_class", "height_limit_cm")

# Horizonte empirico do modelo: o intervalo entre os dois levantamentos.
PREDICTION_HORIZON_DAYS = 7

# Acima desta idade, a ultima observacao de campo deixa de sustentar a
# extrapolacao e o apoio passa a ser apenas descritivo.
MAX_FIELD_DATA_AGE_DAYS = 30

# Faixa de abstencao: entre os dois limiares o modelo nao sugere nada.
LOW_DECISION_THRESHOLD = 0.35
HIGH_DECISION_THRESHOLD = 0.65
MEDIUM_CONFIDENCE_LOW = 0.20
MEDIUM_CONFIDENCE_HIGH = 0.80

CALIBRATION_STATUS = "uncalibrated"


def model_feature_vector(features: Mapping[str, Any]) -> list[float]:
    values = [float(features[name]) for name in MODEL_FEATURES]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Regrowth features must be finite.")
    return values


def score_non_compliance(features: Sequence[float], model: Mapping[str, Any]) -> float:
    """Score logistico de descumprimento do limite; nao e probabilidade calibrada."""

    normalized = [
        (value - mean) / scale
        for value, mean, scale in zip(
            features, model["scaler_mean"], model["scaler_scale"]
        )
    ]
    logit = float(model["intercept"]) + sum(
        coefficient * value
        for coefficient, value in zip(model["coefficients"], normalized)
    )
    if logit >= 0:
        score = 1.0 / (1.0 + math.exp(-logit))
    else:
        exponential = math.exp(logit)
        score = exponential / (1.0 + exponential)
    return min(1.0, max(0.0, score))


def classify_support(
    score: float,
    *,
    lower: float = LOW_DECISION_THRESHOLD,
    upper: float = HIGH_DECISION_THRESHOLD,
) -> str:
    """Traduz o score em sugestao de apoio, com abstencao no meio da faixa."""

    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("Support score must be finite and between zero and one.")
    if not 0.0 <= lower < upper <= 1.0:
        raise ValueError("Selective thresholds must satisfy 0 <= lower < upper <= 1.")
    if score <= lower:
        return "nao_cortar"
    if score >= upper:
        return "cortar"
    return "inconclusivo"


def support_confidence(score: float) -> str:
    return (
        "medium"
        if score <= MEDIUM_CONFIDENCE_LOW or score >= MEDIUM_CONFIDENCE_HIGH
        else "low"
    )


def agreement_with(decision: str | None, suggestion: str | None) -> str | None:
    """Compara a sugestao de apoio com a decisao do satelite."""

    if not decision or not suggestion:
        return None
    if suggestion == "inconclusivo" or decision == "inconclusivo":
        return "indeterminado"
    return "concorda" if suggestion == decision else "diverge"
