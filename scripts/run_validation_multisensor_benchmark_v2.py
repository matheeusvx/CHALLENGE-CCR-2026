"""Build the frozen, experimental Multisensor Validation Benchmark V2 artifact."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.validation.multisensor_benchmark_v2 import (
    build_validation_multisensor_benchmark_v2,
    write_multisensor_benchmark_v2_artifact,
)
from apps.api.app.validation.temporal_benchmark import ValidationTemporalBenchmarkRunner
from src.satellite_monitoring.config import STAC_ENDPOINT
from src.satellite_monitoring.multisource.providers.sentinel1 import Sentinel1Provider


ARTIFACT_NAME = "validation_multisensor_benchmark_v2.json"
FROZEN_TEMPORAL_NAME = "validation_temporal_benchmark_input.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_validation_details_read_only(path: Path) -> list[dict[str, Any]]:
    """Load immutable snapshots through SQLite mode=ro without schema side effects."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM validation_samples ORDER BY created_at, sample_id"
        ).fetchall()
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        raw_snapshot = row.pop("snapshot_json", None)
        if not isinstance(raw_snapshot, str):
            raise ValueError(f"validation snapshot missing for {row.get('sample_id')}")
        row["snapshot"] = json.loads(raw_snapshot)
        result.append(row)
    return result


def _holdout(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"independent": False, "preregistered": False}
    document = _read_json(path)
    if not isinstance(document, dict):
        raise ValueError("holdout manifest must be a JSON object")
    return {
        "independent": document.get("independent") is True,
        "preregistered": document.get("preregistered") is True,
        "manifest_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-db", required=True, type=Path)
    parser.add_argument("--soak-runs", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    temporal = parser.add_mutually_exclusive_group(required=True)
    temporal.add_argument(
        "--temporal-benchmark-json",
        type=Path,
        help="Frozen temporal V1 input; makes generation fully offline",
    )
    temporal.add_argument(
        "--refresh-temporal",
        action="store_true",
        help="Run the current temporal benchmark once and freeze its result",
    )
    parser.add_argument("--analysis-output-root", type=Path, default=Path("outputs/satellite_monitoring"))
    parser.add_argument("--endpoint", default=STAC_ENDPOINT)
    parser.add_argument("--collection", default="sentinel-1-grd")
    parser.add_argument("--max-scenes", type=int, default=8)
    parser.add_argument("--expected-soak-repetitions", type=int, default=3)
    parser.add_argument(
        "--holdout-manifest",
        type=Path,
        help="Optional preregistration evidence; omitted for the current development dataset",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    validation_rows = load_validation_details_read_only(arguments.validation_db)
    soak_runs = _read_json(arguments.soak_runs)
    if not isinstance(soak_runs, list):
        raise ValueError("soak runs input must be a JSON array")
    arguments.output_root.mkdir(parents=True, exist_ok=True)

    if arguments.refresh_temporal:
        provider = Sentinel1Provider(
            endpoint=arguments.endpoint,
            collection=arguments.collection,
            max_scenes=arguments.max_scenes,
        )
        temporal = ValidationTemporalBenchmarkRunner(
            provider,
            output_root=arguments.analysis_output_root,
        ).run(validation_rows)
        frozen_temporal_path = arguments.output_root / FROZEN_TEMPORAL_NAME
        _write_json_atomic(frozen_temporal_path, temporal)
    else:
        temporal = _read_json(arguments.temporal_benchmark_json)
        frozen_temporal_path = arguments.temporal_benchmark_json.resolve()
    if not isinstance(temporal, dict):
        raise ValueError("temporal benchmark input must be a JSON object")

    artifact = build_validation_multisensor_benchmark_v2(
        validation_rows,
        temporal,
        soak_runs,
        expected_soak_repetitions=arguments.expected_soak_repetitions,
        holdout=_holdout(arguments.holdout_manifest),
    )
    artifact_path = arguments.output_root / ARTIFACT_NAME
    write_multisensor_benchmark_v2_artifact(artifact_path, artifact)
    return {
        "artifact": str(artifact_path.resolve()),
        "frozen_temporal_input": str(frozen_temporal_path),
        "sample_count": artifact["dataset"]["sample_count"],
        "scientific_sample_count": artifact["dataset"]["scientific_sample_count"],
        "soak_run_count": artifact["dataset"]["soak_run_count"],
        "overall_gate": artifact["recommendation_gate"]["overall_status"],
    }


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        result = run(arguments)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
