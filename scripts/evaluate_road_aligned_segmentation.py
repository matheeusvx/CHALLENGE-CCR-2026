"""Executa offline a matriz longitudinal sobre resultados RAW V1 existentes."""

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
from src.satellite_monitoring.road_aligned_segmentation import (
    evaluate_road_aligned_matrix,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compara secoes rodoviarias 25/50/100 m sem acessar a rede."
    )
    parser.add_argument(
        "--frango-v1-json",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation" / "frango_assado_area_audit.json",
    )
    parser.add_argument(
        "--louveira-v1-json",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation" / "louveira_lateral.json",
    )
    parser.add_argument(
        "--roads-geojson",
        type=Path,
        default=ROOT / "data" / "roads" / "processed" / "motiva-sp-roads-state.geojson",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "spatial_segmentation",
    )
    return parser


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.resolve().read_text(encoding="utf-8"))


def _raw_spatial(path: Path) -> dict[str, Any]:
    document = _read(path)
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
    zones: list[dict[str, Any]],
    *,
    case_name: str,
    section_length_m: int,
    dominance: float,
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": zone["geometry"],
                "properties": {key: value for key, value in zone.items() if key != "geometry"},
            }
            for zone in zones
        ],
        "properties": {
            "case": case_name,
            "representation": "road_aligned_segmentation",
            "section_length_m": section_length_m,
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
    result = evaluate_road_aligned_matrix(cases, _read(args.roads_geojson))
    recommended_length = result["recommended_section_length"]
    recommended_dominance = result["recommended_dominance"]
    report = {key: value for key, value in result.items() if key != "recommended_outputs"}
    if recommended_length is not None and recommended_dominance is not None:
        report["recommended_case_metrics"] = {
            case_name: {
                key: value
                for key, value in case_result.items()
                if key not in {"sections", "zones"}
            }
            for case_name, case_result in result["recommended_outputs"].items()
        }
    args.output_root.mkdir(parents=True, exist_ok=True)
    report_path = args.output_root / "road_aligned_segmentation_report.json"
    _write(report_path, report)
    written_outputs: dict[str, str] = {}
    if recommended_length is not None and recommended_dominance is not None:
        for case_name, filename in (
            ("frango_assado", "frango_assado_road_aligned.geojson"),
            ("louveira", "louveira_road_aligned.geojson"),
        ):
            destination = args.output_root / filename
            _write(
                destination,
                _feature_collection(
                    result["recommended_outputs"][case_name]["zones"],
                    case_name=case_name,
                    section_length_m=recommended_length,
                    dominance=recommended_dominance,
                ),
            )
            written_outputs[case_name] = str(destination)
    print(
        json.dumps(
            {
                "recommended_section_length": recommended_length,
                "recommended_dominance": recommended_dominance,
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
