"""Initialize, freeze, and evaluate the independent validation holdout."""

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

from apps.api.app.validation.holdout_benchmark import (
    build_validation_holdout_benchmark_v1,
    create_preregistration,
    freeze_preregistration,
    mark_preregistration_collecting,
    mark_preregistration_evaluated,
    validate_preregistration,
    write_json_atomic,
)
from apps.api.app.validation.identity import geometry_fingerprint


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_validation_details_read_only(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(validation_samples)")
        }
        rows = connection.execute(
            "SELECT * FROM validation_samples ORDER BY created_at, sample_id"
        ).fetchall()
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        snapshot = json.loads(row.pop("snapshot_json"))
        row["snapshot"] = snapshot
        row.setdefault("cohort", "development")
        if "aoi_fingerprint" not in columns or not row.get("aoi_fingerprint"):
            row["aoi_fingerprint"] = geometry_fingerprint(
                (snapshot.get("aoi") or {}).get("geometry_geojson")
            )
        result.append(row)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="Create a new draft preregistration")
    initialize.add_argument("--preregistration", required=True, type=Path)
    initialize.add_argument("--methodology-note", action="append", default=[])

    collecting = commands.add_parser("collecting", help="Move draft to collecting")
    collecting.add_argument("--preregistration", required=True, type=Path)

    freeze = commands.add_parser("freeze", help="Freeze rule, gates, and holdout membership")
    freeze.add_argument("--validation-db", required=True, type=Path)
    freeze.add_argument("--preregistration", required=True, type=Path)

    evaluate = commands.add_parser("evaluate", help="Evaluate frozen holdout offline")
    evaluate.add_argument("--validation-db", required=True, type=Path)
    evaluate.add_argument("--preregistration", required=True, type=Path)
    evaluate.add_argument("--temporal-benchmark-json", required=True, type=Path)
    evaluate.add_argument("--soak-runs", type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    preregistration_path = arguments.preregistration
    if arguments.command == "init":
        document = create_preregistration(methodology_notes=arguments.methodology_note)
        write_json_atomic(preregistration_path, document)
        return {"status": document["status"], "preregistration": str(preregistration_path.resolve())}

    document = validate_preregistration(_read_json(preregistration_path))
    if arguments.command == "collecting":
        updated = mark_preregistration_collecting(document)
        write_json_atomic(preregistration_path, updated, overwrite=True)
        return {"status": updated["status"], "preregistration": str(preregistration_path.resolve())}

    validation_rows = load_validation_details_read_only(arguments.validation_db)
    if arguments.command == "freeze":
        frozen = freeze_preregistration(document, validation_rows)
        write_json_atomic(preregistration_path, frozen, overwrite=True)
        return {
            "status": frozen["status"],
            "holdout_sample_count": frozen["frozen_dataset"]["sample_count"],
            "preregistration": str(preregistration_path.resolve()),
        }

    temporal = _read_json(arguments.temporal_benchmark_json)
    soak_runs = _read_json(arguments.soak_runs) if arguments.soak_runs else []
    if not isinstance(temporal, dict):
        raise ValueError("temporal benchmark must be a JSON object")
    if not isinstance(soak_runs, list):
        raise ValueError("soak runs must be a JSON array")
    if document.get("status") != "frozen":
        raise ValueError("only a frozen, not-yet-evaluated preregistration can be evaluated")
    artifact = build_validation_holdout_benchmark_v1(
        validation_rows,
        document,
        temporal,
        soak_runs=soak_runs,
    )
    write_json_atomic(arguments.output, artifact)
    evaluated = mark_preregistration_evaluated(document)
    write_json_atomic(preregistration_path, evaluated, overwrite=True)
    return {
        "status": evaluated["status"],
        "gate": artifact["recommendation_gate"]["status"],
        "holdout_sample_count": artifact["dataset"]["total_samples"],
        "artifact": str(arguments.output.resolve()),
    }


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        result = run(arguments)
    except (FileExistsError, FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
