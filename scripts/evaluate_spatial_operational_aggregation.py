"""Executa offline a matriz V3 sobre os resultados raw V1 existentes."""

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
from src.satellite_monitoring.spatial_operational_aggregation import (
    evaluate_operational_matrix,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compara unidades operacionais 20/30/50 m sem acessar a rede."
    )
    parser.add_argument(
        "--frango-v1-json",
        type=Path,
        default=ROOT
        / "outputs"
        / "spatial_segmentation"
        / "frango_assado_area_audit.json",
    )
    parser.add_argument(
        "--louveira-v1-json",
        type=Path,
        default=ROOT
        / "outputs"
        / "spatial_segmentation"
        / "louveira_lateral.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation",
    )
    return parser


def _raw_spatial(path: Path) -> dict[str, Any]:
    document = json.loads(path.resolve().read_text(encoding="utf-8"))
    spatial = document.get("spatial_segmentation", document)
    return spatial.get("raw_segmentation", spatial)


def _write(path: Path, payload: Any) -> None:
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


def _feature_collection(
    zones: list[dict[str, Any]], *, case_name: str, resolution: int, dominance: float
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
            "representation": "operational_aggregation_v3",
            "operational_aggregation_resolution_m": resolution,
            "dominance_threshold": dominance,
            "shadow_mode": True,
        },
    }


def main() -> int:
    args = _parser().parse_args()
    cases = {
        "frango_assado": _raw_spatial(args.frango_v1_json),
        "louveira": _raw_spatial(args.louveira_v1_json),
    }
    result = evaluate_operational_matrix(cases)
    recommended_resolution = result["recommended_operational_resolution"]
    recommended_dominance = result["recommended_dominance_threshold"]
    report = {
        key: value for key, value in result.items() if key != "recommended_outputs"
    }
    if recommended_resolution is not None and recommended_dominance is not None:
        report["recommended_case_metrics"] = {
            case_name: {
                key: value
                for key, value in case_result.items()
                if key not in {"units", "zones"}
            }
            for case_name, case_result in result["recommended_outputs"].items()
        }
    args.output_root.mkdir(parents=True, exist_ok=True)
    report_path = args.output_root / "spatial_operational_aggregation_report.json"
    _write(report_path, report)
    written_outputs: dict[str, str] = {}
    if recommended_resolution is not None and recommended_dominance is not None:
        for case_name, filename in (
            ("louveira", "louveira_operational_v3.geojson"),
            ("frango_assado", "frango_assado_operational_v3.geojson"),
        ):
            destination = args.output_root / filename
            _write(
                destination,
                _feature_collection(
                    result["recommended_outputs"][case_name]["zones"],
                    case_name=case_name,
                    resolution=recommended_resolution,
                    dominance=recommended_dominance,
                ),
            )
            written_outputs[case_name] = str(destination)
    print(
        json.dumps(
            {
                "recommended_operational_resolution": recommended_resolution,
                "recommended_dominance_threshold": recommended_dominance,
                "report": str(report_path),
                "outputs": written_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

