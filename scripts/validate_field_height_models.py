"""Executa holdout externo V0/C2/T3 sem alterar artefatos de produção."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_height_ablation import C2_FEATURES, _read_baseline_samples
from scripts.run_height_temporal_experiment import (
    EXPERIMENT_FEATURES,
    _collect_feature_histories,
)
from src.satellite_monitoring.experiments.field_model_validation import (
    assert_external_holdouts_excluded,
    evaluate_external_holdouts,
    load_enriched_holdouts,
    write_external_validation_outputs,
)
from src.satellite_monitoring.models.validation import build_training_pipeline


def _fit_historical_only(
    rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str]
) -> Any:
    assert_external_holdouts_excluded(rows)
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x = np.asarray(
        [[float(row[name]) for name in feature_names] for row in ordered], dtype=float
    )
    y = np.asarray([int(row["target"]) for row in ordered], dtype=int)
    if not np.all(np.isfinite(x)) or set(y.tolist()) != {0, 1}:
        raise ValueError("Historical challenger cohort is invalid.")
    model = build_training_pipeline()
    model.fit(x, y)
    return model


def _expected_coefficients(path: Path, experiment: str) -> tuple[list[float], float]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if str(row.get("experiment")) == experiment
        ]
    if not rows:
        raise ValueError(f"No frozen coefficient diagnostics for {experiment}: {path}")
    return (
        [float(row["standardized_coefficient"]) for row in rows],
        float(rows[0]["intercept"]),
    )


def _verify_reproduction(model: Any, path: Path, experiment: str) -> bool:
    expected_coefficients, expected_intercept = _expected_coefficients(path, experiment)
    classifier = model.named_steps["classifier"]
    actual_coefficients = np.asarray(classifier.coef_[0], dtype=float)
    return bool(
        np.allclose(actual_coefficients, expected_coefficients, rtol=0.0, atol=1e-12)
        and np.isclose(
            float(classifier.intercept_[0]),
            expected_intercept,
            rtol=0.0,
            atol=1e-12,
        )
    )


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    holdouts = load_enriched_holdouts(arguments.field_data)
    baseline = _read_baseline_samples(arguments.baseline_samples)
    assert_external_holdouts_excluded(baseline)
    frozen_geometry_ids = {
        str(row["sample_id"]): tuple(
            value
            for value in str(row.get("geometry_group_id") or "").split(";")
            if value
        )
        for row in baseline
    }
    if any(not values for values in frozen_geometry_ids.values()):
        raise ValueError("Frozen historical geometry groups are incomplete.")
    variants, _, _, diagnostics = _collect_feature_histories(
        baseline,
        arguments.dataset or Path("unused_frozen_geometry_dataset.csv"),
        arguments.management_kmz,
        arguments.km_markers_kmz,
        frozen_candidate_geometry_ids=frozen_geometry_ids,
    )
    try:
        c2_rows = variants["T0"]
        t3_rows = variants["T3"]
        assert_external_holdouts_excluded(c2_rows)
        assert_external_holdouts_excluded(t3_rows)
        c2_model = _fit_historical_only(c2_rows, C2_FEATURES)
        t3_model = _fit_historical_only(t3_rows, EXPERIMENT_FEATURES["T3"])
        c2_match = _verify_reproduction(
            c2_model, arguments.ablation_coefficients, "C2"
        )
        t3_match = _verify_reproduction(
            t3_model, arguments.temporal_coefficients, "T3"
        )
        if not c2_match or not t3_match:
            raise ValueError("Reproduced challenger coefficients differ from prior experiment.")
        results = evaluate_external_holdouts(
            holdouts, c2_model=c2_model, t3_model=t3_model
        )
        reproduction = {
            "historical_only": True,
            "external_holdouts_used_in_fit": False,
            "training_metadata_source": (
                "frozen_training_samples_geometry_group_id_and_field_date"
            ),
            "c2_training_sample_count": len(c2_rows),
            "c2_positive_sample_count": sum(int(row["target"]) for row in c2_rows),
            "t3_training_sample_count": len(t3_rows),
            "t3_positive_sample_count": sum(int(row["target"]) for row in t3_rows),
            "c2_coefficients_match_prior_experiment": c2_match,
            "t3_coefficients_match_prior_experiment": t3_match,
            "same_preprocessing": "StandardScaler_then_balanced_LogisticRegression",
            "same_random_seed": 20260818,
            "prior_fold_assignments_preserved_in_experiment_outputs": True,
            "challenger_artifacts_written": False,
            "collection_diagnostics": diagnostics,
        }
        summary = write_external_validation_outputs(
            results, arguments.output_dir, reproduction=reproduction
        )
        return summary
    finally:
        # Explicitly release potentially large raster-derived cohorts after output.
        variants.clear()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate frozen/reproduced height estimators on external field holdouts."
    )
    parser.add_argument(
        "--field-data",
        type=Path,
        default=Path("outputs/field_calibration/field_ground_truth_enriched.csv"),
    )
    parser.add_argument(
        "--baseline-samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--management-kmz", type=Path, required=True)
    parser.add_argument("--km-markers-kmz", type=Path, required=True)
    parser.add_argument(
        "--ablation-coefficients",
        type=Path,
        default=Path("outputs/height_ablation_sentinel2/model_coefficients.csv"),
    )
    parser.add_argument(
        "--temporal-coefficients",
        type=Path,
        default=Path(
            "outputs/height_temporal_experiment/temporal_model_coefficients.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/field_external_model_validation"),
    )
    return parser.parse_args()


def main() -> int:
    summary = run(_arguments())
    concise = {
        "verdict": summary["verdict"],
        "recommended_next_action": summary["recommended_next_action"],
        "results": summary["results"],
        "reproduction": {
            key: value
            for key, value in summary["reproduction"].items()
            if key != "collection_diagnostics"
        },
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
