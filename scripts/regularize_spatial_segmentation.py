"""Compara MMU 2/3/4 offline usando exclusivamente um resultado V1 existente."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.satellite_monitoring.outputs import to_json_compatible
from src.satellite_monitoring.spatial_regularization import (
    evaluate_spatial_regularization,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regulariza offline uma segmentacao V1 preservando o raw."
    )
    parser.add_argument("--v1-json", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation",
    )
    return parser


def _feature_collection(
    zones: list[dict[str, Any]], *, case_name: str, representation: str
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": zone["geometry"],
                "properties": {
                    key: value for key, value in zone.items() if key != "geometry"
                },
            }
            for zone in zones
        ],
        "properties": {
            "case": case_name,
            "representation": representation,
            "shadow_mode": True,
        },
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            to_json_compatible(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _parser().parse_args()
    source = json.loads(args.v1_json.resolve().read_text(encoding="utf-8"))
    spatial = source.get("spatial_segmentation", source)
    if "raw_segmentation" in spatial:
        spatial = spatial["raw_segmentation"]
    result = evaluate_spatial_regularization(spatial)
    raw = result["raw_segmentation"]
    operational = result["operational_segmentation"]
    report = {
        "case": args.output_prefix,
        "source_v1_json": str(args.v1_json.resolve()),
        "global_recommendation": source.get("global_recommendation"),
        "recommended_mmu": result["recommended_mmu"],
        "configuration": result["configuration"],
        "raw": result["regularization"]["raw"],
        "operational": result["regularization"]["operational"],
        "regularization": result["regularization"],
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_root / f"{args.output_prefix}_raw.geojson"
    operational_path = args.output_root / f"{args.output_prefix}_operational.geojson"
    report_path = args.output_root / f"{args.output_prefix}_regularization_report.json"
    _write_json(
        raw_path,
        _feature_collection(
            list(raw.get("zones") or []),
            case_name=args.output_prefix,
            representation="raw",
        ),
    )
    _write_json(
        operational_path,
        _feature_collection(
            list(operational.get("zones") or []),
            case_name=args.output_prefix,
            representation="operational",
        ),
    )
    _write_json(report_path, report)
    print(
        json.dumps(
            {
                "case": args.output_prefix,
                "recommended_mmu": result["recommended_mmu"],
                "zones_before": result["regularization"]["raw"]["zone_count"],
                "zones_after": result["regularization"]["operational"]["zone_count"],
                "raw_geojson": str(raw_path),
                "operational_geojson": str(operational_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

