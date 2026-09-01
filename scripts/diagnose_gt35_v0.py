"""Gera diagnóstico explicativo do V0/height mask para GT35 sem treino."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.satellite_monitoring.experiments.field_calibration import load_field_data
from src.satellite_monitoring.experiments.field_v0_diagnostic import (
    build_gt35_v0_diagnostic,
    write_gt35_v0_diagnostic,
)
from src.satellite_monitoring.experiments.sentinel2_ablation import (
    read_height_mask_pixel_diagnostics,
)
from src.satellite_monitoring.datasets.training_scenes import query_training_scenes
from src.satellite_monitoring.geometry import extract_polygon_geometry


def run(arguments: argparse.Namespace) -> dict[str, object]:
    with arguments.enriched.open("r", encoding="utf-8-sig", newline="") as handle:
        enriched = list(csv.DictReader(handle))
    observations = {item.sample_id: item for item in load_field_data(arguments.campaign)}
    observation = observations["GT35-02"]
    polygon, _ = extract_polygon_geometry(dict(observation.geometry or {}))
    scene_date = date.fromisoformat(
        next(row["scene_date"] for row in enriched if row["sample_id"] == "GT35-02")
    )
    expected_item_id = next(
        row["sentinel_item_id"]
        for row in enriched
        if row["sample_id"] == "GT35-02"
    )
    records, error = query_training_scenes(polygon, scene_date, scene_date)
    if error:
        raise RuntimeError(error)
    record = next(
        (row for row in records if str(row.get("item_id")) == expected_item_id),
        None,
    )
    if record is None or record.get("_scene_item") is None:
        raise RuntimeError("Persisted causal Sentinel item is unavailable.")
    pixels = read_height_mask_pixel_diagnostics(
        record["_scene_item"], dict(observation.geometry or {})
    )
    payload = build_gt35_v0_diagnostic(
        enriched,
        pixels,
        model_path=arguments.model,
        training_path=arguments.training_samples,
    )
    write_gt35_v0_diagnostic(payload, arguments.output)
    return payload


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign",
        type=Path,
        default=Path("data/field_calibration/campaign_gt35_external_validation.json"),
    )
    parser.add_argument(
        "--enriched",
        type=Path,
        default=Path(
            "outputs/field_calibration/gt35_enrichment/field_ground_truth_enriched.csv"
        ),
    )
    parser.add_argument(
        "--model", type=Path, default=Path("models/height_estimator_v0.json")
    )
    parser.add_argument(
        "--training-samples",
        type=Path,
        default=Path("outputs/height_estimator_v0/training_samples.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/field_calibration/gt35_v0_diagnostic.json"),
    )
    return parser.parse_args()


def main() -> int:
    payload = run(_arguments())
    print(json.dumps({"conclusion": payload["conclusion"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
