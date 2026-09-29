"""Avalia o gate seletivo v0 com predicoes OOF group-aware, sem salvar modelo."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.satellite_monitoring.features.vegetation_mask import HeightMaskConfig
from src.satellite_monitoring.models.grass_threshold import (
    HIGH_DECISION_THRESHOLD,
    LOW_DECISION_THRESHOLD,
    MODEL_VERSION,
)
from src.satellite_monitoring.models.validation import (
    experimental_validation_metrics,
    grouped_validation_predictions,
    risk_coverage_table,
    selective_validation_metrics,
    training_arrays,
)


def _load_training_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    for row in rows:
        row["target"] = int(row["target"])
    return rows


def evaluate(samples_path: Path, model_path: Path) -> dict[str, Any]:
    rows = _load_training_rows(samples_path)
    x, y, groups = training_arrays(rows)
    scores, fold_count, limitations = grouped_validation_predictions(x, y, groups)
    if scores is None:
        raise ValueError("OOF group-aware scores could not be regenerated.")
    model_bytes = model_path.read_bytes()
    artifact = json.loads(model_bytes.decode("utf-8"))
    current = selective_validation_metrics(
        y,
        scores,
        lower=LOW_DECISION_THRESHOLD,
        upper=HIGH_DECISION_THRESHOLD,
    )
    recomputed_baseline = experimental_validation_metrics(y, scores)
    return {
        "model_version": MODEL_VERSION,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_strategy": (
            "deterministic_regeneration_of_original_StratifiedGroupKFold_OOF_scores;"
            " no final model artifact written"
        ),
        "fold_count": fold_count,
        "current_thresholds": {
            "lower": LOW_DECISION_THRESHOLD,
            "upper": HIGH_DECISION_THRESHOLD,
        },
        "selective_validation_metrics": current,
        "risk_coverage_table": risk_coverage_table(y, scores),
        "height_mask_configuration": HeightMaskConfig().to_dict(),
        "baseline_comparison": {
            "model_artifact_sha256": hashlib.sha256(model_bytes).hexdigest(),
            "artifact_validation_metrics_at_0_5": artifact.get(
                "experimental_validation_metrics"
            ),
            "recomputed_validation_metrics_at_0_5": recomputed_baseline,
            "recommendation_affected": False,
            "coefficients_modified": False,
        },
        "limitations": [
            "Scores are not calibrated probabilities.",
            "Metrics use the existing 186-sample baseline and its group-aware OOF strategy.",
            "The new height mask was not retroactively applied to the historical v0 samples.",
            *limitations,
        ],
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate height-estimator-v0 gate.")
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument(
        "--model", type=Path, default=Path("models/height_estimator_v0.json")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/height_estimator_v0_selective_evaluation.json"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = evaluate(arguments.samples, arguments.model)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
