"""Treina o modelo de apoio por historico de rebrota.

Usa as transicoes observadas entre levantamentos de campo consecutivos e grava
o artefato em ``models/regrowth_support_v0.json``.

A validacao e group-aware por quilometro (`StratifiedGroupKFold`), a mesma
estrategia ja adotada pelo estimador de altura: dois segmentos do mesmo km sao
espacialmente correlacionados e nao podem ficar em lados opostos da particao.

Uso::

    python -m scripts.train_regrowth_support
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.satellite_monitoring.database import session_scope
from src.satellite_monitoring.database.analytics import (
    TransitionSample,
    build_transition_dataset,
)
from src.satellite_monitoring.models.regrowth_support import (
    CALIBRATION_STATUS,
    MODEL_FEATURES,
    MODEL_VERSION,
    PREDICTION_HORIZON_DAYS,
)

TRAINING_RANDOM_SEED = 20260818
MAX_VALIDATION_FOLDS = 5
DEFAULT_OUTPUT = Path("models") / "regrowth_support_v0.json"

LIMITATIONS = [
    "Treinado sobre uma janela curta de levantamentos de campo; a extrapolacao "
    "para prazos maiores que o horizonte do modelo nao tem base empirica.",
    "O alvo e a classe de altura declarada no unifilar da concessionaria, nao "
    "uma medicao fisica em campo.",
    "Poucos exemplos positivos: a precisao e instavel e o modelo tende a "
    "sinalizar mais nao conformidades do que ocorrem.",
    "Serve apenas como apoio. A recomendacao operacional continua sendo "
    "produzida pelo motor de satelite.",
]


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=TRAINING_RANDOM_SEED,
                ),
            ),
        ]
    )


def training_arrays(
    samples: Iterable[TransitionSample],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[TransitionSample]]:
    ordered = [
        sample
        for sample in sorted(samples, key=lambda item: (item.segment_id, item.observed_on))
        if sample.non_compliant_next is not None
        and sample.current_height_class.isdigit()
    ]
    if not ordered:
        raise ValueError("Nenhuma transicao valida encontrada para treinamento.")

    features = np.asarray(
        [
            [float(sample.current_height_class), float(sample.height_limit_cm)]
            for sample in ordered
        ],
        dtype=float,
    )
    target = np.asarray(
        [int(bool(sample.non_compliant_next)) for sample in ordered], dtype=int
    )
    groups = np.asarray([sample.km for sample in ordered], dtype=int)
    return features, target, groups, ordered


def cross_validated_scores(
    features: np.ndarray, target: np.ndarray, groups: np.ndarray
) -> tuple[np.ndarray, int]:
    positives = int(target.sum())
    negatives = int(len(target) - positives)
    folds = min(MAX_VALIDATION_FOLDS, positives, negatives, len(set(groups.tolist())))
    if folds < 2:
        raise ValueError("Amostras insuficientes para validacao cruzada.")

    splitter = StratifiedGroupKFold(
        n_splits=folds, shuffle=True, random_state=TRAINING_RANDOM_SEED
    )
    scores = np.zeros(len(target), dtype=float)
    for train_index, test_index in splitter.split(features, target, groups):
        pipeline = build_pipeline()
        pipeline.fit(features[train_index], target[train_index])
        scores[test_index] = pipeline.predict_proba(features[test_index])[:, 1]
    return scores, folds


def evaluate(target: np.ndarray, scores: np.ndarray) -> dict[str, float | list[list[int]]]:
    predictions = (scores >= 0.5).astype(int)
    matrix = confusion_matrix(target, predictions, labels=[0, 1]).tolist()
    return {
        "roc_auc": round(float(roc_auc_score(target, scores)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(target, predictions)), 4),
        "precision": round(float(precision_score(target, predictions, zero_division=0)), 4),
        "recall": round(float(recall_score(target, predictions, zero_division=0)), 4),
        "f1": round(float(f1_score(target, predictions, zero_division=0)), 4),
        "confusion_matrix": matrix,
    }


def dataset_fingerprint(samples: Iterable[TransitionSample]) -> str:
    payload = json.dumps(
        [
            [
                sample.segment_id,
                sample.observed_on.isoformat(),
                sample.current_height_class,
                sample.next_height_class,
                sample.height_limit_cm,
            ]
            for sample in samples
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def train(output_path: Path) -> dict[str, object]:
    with session_scope() as session:
        samples = build_transition_dataset(session)

    features, target, groups, ordered = training_arrays(samples)
    scores, folds = cross_validated_scores(features, target, groups)
    metrics = evaluate(target, scores)

    pipeline = build_pipeline()
    pipeline.fit(features, target)
    scaler: StandardScaler = pipeline.named_steps["scaler"]
    classifier: LogisticRegression = pipeline.named_steps["classifier"]

    horizons = sorted({sample.elapsed_days for sample in ordered})
    artifact = {
        "model_version": MODEL_VERSION,
        "features": list(MODEL_FEATURES),
        "intercept": float(classifier.intercept_[0]),
        "coefficients": [float(value) for value in classifier.coef_[0]],
        "scaler_mean": [float(value) for value in scaler.mean_],
        "scaler_scale": [float(value) for value in scaler.scale_],
        "calibration_status": CALIBRATION_STATUS,
        "training_sample_count": int(len(target)),
        "positive_sample_count": int(target.sum()),
        "negative_sample_count": int(len(target) - target.sum()),
        "group_count": int(len(set(groups.tolist()))),
        "fold_count": folds,
        "validation_strategy": "StratifiedGroupKFold_by_km",
        "validation_metrics": metrics,
        "prediction_horizon_days": PREDICTION_HORIZON_DAYS,
        "observed_horizons_days": horizons,
        "survey_dates": sorted(
            {sample.observed_on.isoformat() for sample in ordered}
            | {sample.next_observed_on.isoformat() for sample in ordered}
        ),
        "training_dataset_sha256": dataset_fingerprint(ordered),
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "limitations": LIMITATIONS,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return artifact


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    artifact = train(arguments.output)
    metrics = artifact["validation_metrics"]
    assert isinstance(metrics, dict)

    print(f"Modelo treinado: {artifact['model_version']}")
    print(f"  amostras: {artifact['training_sample_count']} "
          f"(positivos: {artifact['positive_sample_count']})")
    print(f"  grupos (km): {artifact['group_count']} | folds: {artifact['fold_count']}")
    print(f"  ROC-AUC: {metrics['roc_auc']}")
    print(f"  balanced accuracy: {metrics['balanced_accuracy']}")
    print(f"  precisao: {metrics['precision']} | recall: {metrics['recall']}")
    print(f"  horizonte observado (dias): {artifact['observed_horizons_days']}")
    print(f"Artefato: {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
