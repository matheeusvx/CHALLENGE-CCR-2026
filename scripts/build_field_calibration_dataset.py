"""Gera dataset offline de ground truth de campo enriquecido por Sentinel-2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.satellite_monitoring.experiments.field_calibration import (
    build_field_calibration_rows,
    load_field_data,
    write_field_calibration_outputs,
)


def run(arguments: argparse.Namespace) -> dict[str, object]:
    observations = load_field_data(
        arguments.field_data,
        geometry_dir=arguments.geometry_dir,
    )
    rows = build_field_calibration_rows(observations)
    report = write_field_calibration_outputs(rows, arguments.output_dir)
    return {
        "field_data": str(arguments.field_data),
        "output_dir": str(arguments.output_dir),
        "sample_count": len(rows),
        "sample_statuses": {
            str(row["sample_id"]): {
                "field_measurement_status": row["field_measurement_status"],
                "spectral_extraction_status": row["spectral_extraction_status"],
                "temporal_status": row["temporal_status"],
                "training_eligible": row["training_eligible"],
                "external_validation": row["external_validation"],
            }
            for row in rows
        },
        "quality": report,
        "production_changes": False,
        "models_trained": False,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build reproducible field calibration data without model fitting."
    )
    parser.add_argument("--field-data", type=Path, required=True)
    parser.add_argument("--geometry-dir", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/field_calibration"),
    )
    return parser.parse_args()


def main() -> int:
    summary = run(_arguments())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
