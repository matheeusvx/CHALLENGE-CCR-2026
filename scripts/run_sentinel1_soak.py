"""Run a reproducible, fail-soft Sentinel-1 soak over external or validation AOIs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.satellite_monitoring.config import STAC_ENDPOINT
from src.satellite_monitoring.multisource.models import CollectionPeriod
from src.satellite_monitoring.multisource.providers.sentinel1 import Sentinel1Provider
from src.satellite_monitoring.sentinel1_soak import (
    SoakInterrupted,
    Sentinel1SoakRunner,
    load_aoi_dataset,
    load_validation_aois,
    run_incremental_soak,
)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use date format YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--aois",
        type=Path,
        help="JSON or GeoJSON AOI dataset",
    )
    source.add_argument(
        "--validation-db",
        type=Path,
        help="Validation SQLite read in mode=ro; only identifiers and geometry are loaded",
    )
    parser.add_argument("--start-date", required=True, type=_date)
    parser.add_argument("--end-date", required=True, type=_date)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--analysis-output-root",
        type=Path,
        default=Path("outputs/satellite_monitoring"),
        help="Read-only fallback for legacy validation AOI artifacts",
    )
    parser.add_argument("--endpoint", default=STAC_ENDPOINT)
    parser.add_argument("--collection", default="sentinel-1-grd")
    parser.add_argument("--max-scenes", type=int, default=8)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a compatible partial run without duplicating completed work",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-run progress and ETA output",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, object]:
    aois = (
        load_aoi_dataset(arguments.aois)
        if arguments.aois is not None
        else load_validation_aois(
            arguments.validation_db,
            analysis_output_root=arguments.analysis_output_root,
        )
    )
    period = CollectionPeriod(arguments.start_date, arguments.end_date)
    provider = Sentinel1Provider(
        endpoint=arguments.endpoint,
        collection=arguments.collection,
        max_scenes=arguments.max_scenes,
    )
    records = run_incremental_soak(
        Sentinel1SoakRunner(provider),
        aois,
        period,
        arguments.output_dir,
        repetitions=arguments.repetitions,
        resume=arguments.resume,
        quiet=arguments.quiet,
    )
    return {
        "aoi_count": len(aois),
        "run_count": len(records),
        "output_directory": str(arguments.output_dir.resolve()),
    }


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        result = run(arguments)
    except SoakInterrupted:
        raise SystemExit(130) from None
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    if not arguments.quiet:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
