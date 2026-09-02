"""Spatial Segmentation V3: unidades operacionais agregadas sobre celulas raw."""

from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from statistics import median
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union


CLASSES = ("cortar", "nao_cortar", "inconclusivo")
DECISIVE_CLASSES = ("cortar", "nao_cortar")
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class OperationalAggregationConfig:
    resolutions_m: tuple[int, ...] = (20, 30, 50)
    dominance_thresholds: tuple[float, ...] = (0.70, 0.75, 0.80)
    min_louveira_zone_reduction_pct: float = 25.0
    max_class_reallocation_pct: float = 15.0
    max_frango_inconclusive_pct: float = 25.0

    def __post_init__(self) -> None:
        if not self.resolutions_m or any(value <= 0 for value in self.resolutions_m):
            raise ValueError("Operational resolutions must be positive.")
        if not self.dominance_thresholds or any(
            not 0.5 < value <= 1.0 for value in self.dominance_thresholds
        ):
            raise ValueError("Dominance thresholds must be in (0.5, 1.0].")
        for value in (
            self.min_louveira_zone_reduction_pct,
            self.max_class_reallocation_pct,
            self.max_frango_inconclusive_pct,
        ):
            if not 0 <= value <= 100:
                raise ValueError("Selection guardrails must be in [0, 100].")


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _cell_area(cell: Mapping[str, Any]) -> float:
    return float(
        cell.get("effective_area_m2", cell.get("intersection_area_m2", 0.0))
    )


def _area_by_class(items: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {
        recommendation: float(
            sum(
                float(item["area_m2"])
                for item in items
                if item["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }


def _minimum_level(values: Sequence[str]) -> str:
    return min(values, key=lambda value: LEVEL_ORDER.get(value, -1), default="low")


def _level_summary(values: Sequence[str]) -> dict[str, Any]:
    counts = Counter(values)
    return {
        "minimum": _minimum_level(list(values)),
        "counts": {level: int(counts.get(level, 0)) for level in LEVEL_ORDER},
    }


def _unit_classification(
    cells: Sequence[dict[str, Any]], dominance_threshold: float
) -> tuple[str, dict[str, Any]]:
    total_area = float(sum(_cell_area(cell) for cell in cells))
    class_areas = {
        recommendation: float(
            sum(
                _cell_area(cell)
                for cell in cells
                if cell["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }
    percentages = {
        recommendation: (
            class_areas[recommendation] / total_area * 100.0
            if total_area > 0
            else 0.0
        )
        for recommendation in CLASSES
    }
    high_confidence_areas = {
        recommendation: float(
            sum(
                _cell_area(cell)
                for cell in cells
                if cell["recommendation"] == recommendation
                and cell.get("confidence") == "high"
            )
        )
        for recommendation in DECISIVE_CLASSES
    }
    high_confidence_conflict = all(
        high_confidence_areas[recommendation] > 0
        for recommendation in DECISIVE_CLASSES
    )
    if high_confidence_conflict:
        recommendation = "inconclusivo"
        reason = "CONFLICTING_HIGH_CONFIDENCE_EVIDENCE"
    elif percentages["cortar"] >= dominance_threshold * 100.0:
        recommendation = "cortar"
        reason = "CORTAR_AREA_DOMINANT"
    elif percentages["nao_cortar"] >= dominance_threshold * 100.0:
        recommendation = "nao_cortar"
        reason = "NAO_CORTAR_AREA_DOMINANT"
    else:
        recommendation = "inconclusivo"
        reason = "NO_DECISIVE_CLASS_REACHED_DOMINANCE"
    return recommendation, {
        "total_area_m2": total_area,
        "area_by_raw_class_m2": class_areas,
        "area_pct_by_raw_class": percentages,
        "high_confidence_cortar_area_m2": high_confidence_areas["cortar"],
        "high_confidence_nao_cortar_area_m2": high_confidence_areas["nao_cortar"],
        "high_confidence_conflict": high_confidence_conflict,
        "classification_reason": reason,
    }


def build_operational_units(
    raw_cells: Sequence[dict[str, Any]],
    *,
    resolution_m: int,
    dominance_threshold: float,
    raw_resolution_m: float = 10.0,
) -> list[dict[str, Any]]:
    """Agrupa celulas por blocos alinhados a origem row/column da grade raw."""
    block_size = int(round(resolution_m / raw_resolution_m))
    if block_size <= 0 or abs(block_size * raw_resolution_m - resolution_m) > 1e-9:
        raise ValueError("Operational resolution must be a multiple of raw grid resolution.")
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for raw_cell in sorted(raw_cells, key=lambda cell: (cell["row"], cell["column"])):
        unit_position = (
            int(raw_cell["row"]) // block_size,
            int(raw_cell["column"]) // block_size,
        )
        grouped.setdefault(unit_position, []).append(raw_cell)

    units: list[dict[str, Any]] = []
    for (unit_row, unit_column), cells in sorted(grouped.items()):
        recommendation, diagnostic = _unit_classification(
            cells, dominance_threshold
        )
        supporting_confidences = [
            str(cell.get("confidence", "low"))
            for cell in cells
            if cell["recommendation"] == recommendation
        ]
        confidence = (
            _minimum_level(supporting_confidences)
            if recommendation in DECISIVE_CLASSES and supporting_confidences
            else "low"
        )
        geometry = unary_union([shape(cell["geometry"]) for cell in cells])
        units.append(
            {
                "unit_id": (
                    f"unit_{resolution_m:03d}m_"
                    f"r{unit_row:06d}_c{unit_column:06d}"
                ),
                "unit_row": unit_row,
                "unit_column": unit_column,
                "operational_aggregation_resolution_m": resolution_m,
                "raw_grid_resolution_m": raw_resolution_m,
                "geometry": mapping(geometry),
                "effective_area_m2": diagnostic["total_area_m2"],
                "area_m2": diagnostic["total_area_m2"],
                "raw_cell_count": len(cells),
                "raw_cell_ids": [str(cell["cell_id"]) for cell in cells],
                "recommendation": recommendation,
                "confidence": confidence,
                "analysis_quality": _minimum_level(
                    [str(cell.get("analysis_quality", "low")) for cell in cells]
                ),
                **diagnostic,
            }
        )
    return units


def _unit_groups(units: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_position = {
        (int(unit["unit_row"]), int(unit["unit_column"])): unit for unit in units
    }
    pending = set(by_position)
    groups: list[list[dict[str, Any]]] = []
    for start in sorted(by_position):
        if start not in pending:
            continue
        expected = str(by_position[start]["recommendation"])
        queue: deque[tuple[int, int]] = deque([start])
        pending.remove(start)
        group: list[dict[str, Any]] = []
        while queue:
            position = queue.popleft()
            unit = by_position[position]
            group.append(unit)
            row, column = position
            for neighbor in (
                (row - 1, column),
                (row, column - 1),
                (row, column + 1),
                (row + 1, column),
            ):
                if (
                    neighbor in pending
                    and by_position[neighbor]["recommendation"] == expected
                ):
                    pending.remove(neighbor)
                    queue.append(neighbor)
        groups.append(
            sorted(group, key=lambda unit: (unit["unit_row"], unit["unit_column"]))
        )
    return groups


def build_operational_zones(units: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for index, group in enumerate(_unit_groups(units), start=1):
        zones.append(
            {
                "operational_zone_id": f"operational_zone_{index:04d}",
                "recommendation": group[0]["recommendation"],
                "geometry": mapping(
                    unary_union([shape(unit["geometry"]) for unit in group])
                ),
                "area_m2": float(sum(float(unit["area_m2"]) for unit in group)),
                "unit_count": len(group),
                "confidence_summary": _level_summary(
                    [str(unit["confidence"]) for unit in group]
                ),
                "quality_summary": _level_summary(
                    [str(unit["analysis_quality"]) for unit in group]
                ),
                "raw_cell_count": sum(int(unit["raw_cell_count"]) for unit in group),
                "high_confidence_conflict_unit_count": sum(
                    bool(unit["high_confidence_conflict"]) for unit in group
                ),
            }
        )
    return zones


def _utm_transformer(geometry: Any) -> Transformer:
    centroid = geometry.centroid
    zone = max(1, min(60, int((centroid.x + 180.0) // 6.0) + 1))
    epsg = (32600 if centroid.y >= 0 else 32700) + zone
    return Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)


def _outside_area_m2(zones: Sequence[dict[str, Any]], raw_cells: Sequence[dict[str, Any]]) -> float:
    raw_footprint = unary_union([shape(cell["geometry"]) for cell in raw_cells])
    zone_footprint = unary_union([shape(zone["geometry"]) for zone in zones])
    outside = zone_footprint.difference(raw_footprint)
    if outside.is_empty:
        return 0.0
    transformer = _utm_transformer(raw_footprint)
    return float(transform(transformer.transform, outside).area)


def evaluate_operational_aggregation(
    raw_segmentation: dict[str, Any],
    *,
    resolution_m: int,
    dominance_threshold: float,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Avalia uma configuracao V3 sem mutar o payload raw."""
    started = clock()
    raw_before = _canonical_hash(raw_segmentation)
    raw_cells = deepcopy(list(raw_segmentation.get("cells") or []))
    if not raw_cells:
        raise ValueError("Raw segmentation has no cells.")
    raw_resolution = float(
        (raw_segmentation.get("grid_resolution") or {}).get("x", 10.0)
    )
    units = build_operational_units(
        raw_cells,
        resolution_m=resolution_m,
        dominance_threshold=dominance_threshold,
        raw_resolution_m=raw_resolution,
    )
    zones = build_operational_zones(units)
    raw_areas = {
        recommendation: float(
            sum(
                _cell_area(cell)
                for cell in raw_cells
                if cell["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }
    operational_areas = _area_by_class(zones)
    total_area = float(sum(raw_areas.values()))
    class_deltas = {
        recommendation: {
            "area_delta_m2": operational_areas[recommendation]
            - raw_areas[recommendation],
            "area_delta_pct_of_raw_class": (
                (operational_areas[recommendation] - raw_areas[recommendation])
                / raw_areas[recommendation]
                * 100.0
                if raw_areas[recommendation] > 0
                else (
                    0.0
                    if operational_areas[recommendation] == 0
                    else None
                )
            ),
        }
        for recommendation in CLASSES
    }
    class_reallocation_pct = (
        sum(abs(value["area_delta_m2"]) for value in class_deltas.values())
        / (2.0 * total_area)
        * 100.0
        if total_area
        else 0.0
    )
    zone_areas = [float(zone["area_m2"]) for zone in zones]
    raw_zone_count = len(raw_segmentation.get("zones") or [])
    confidence_increase_count = sum(
        unit["confidence"] == "high"
        and any(
            cell["confidence"] != "high"
            for cell in raw_cells
            if cell["cell_id"] in unit["raw_cell_ids"]
            and cell["recommendation"] == unit["recommendation"]
        )
        for unit in units
        if unit["recommendation"] in DECISIVE_CLASSES
    )
    conflicting_high_confidence_decisive_count = sum(
        bool(unit["high_confidence_conflict"])
        and unit["recommendation"] != "inconclusivo"
        for unit in units
    )
    geometry_outside_aoi_m2 = _outside_area_m2(zones, raw_cells)
    raw_after = _canonical_hash(raw_segmentation)
    elapsed = max(0.0, clock() - started)
    return {
        "resolution_m": resolution_m,
        "dominance_threshold": dominance_threshold,
        "raw_zone_count": raw_zone_count,
        "operational_unit_count": len(units),
        "operational_zone_count": len(zones),
        "zone_reduction_pct": (
            (raw_zone_count - len(zones)) / raw_zone_count * 100.0
            if raw_zone_count
            else 0.0
        ),
        "area_by_class_m2": operational_areas,
        "area_pct_by_class": {
            recommendation: (
                operational_areas[recommendation] / total_area * 100.0
                if total_area
                else 0.0
            )
            for recommendation in CLASSES
        },
        "raw_area_by_class_m2": raw_areas,
        "class_area_deltas": class_deltas,
        "class_area_reallocation_pct": class_reallocation_pct,
        "decisive_area_pct": (
            (operational_areas["cortar"] + operational_areas["nao_cortar"])
            / total_area
            * 100.0
            if total_area
            else 0.0
        ),
        "inconclusive_area_pct": (
            operational_areas["inconclusivo"] / total_area * 100.0
            if total_area
            else 0.0
        ),
        "largest_zone_area_m2": max(zone_areas, default=0.0),
        "median_zone_area_m2": float(median(zone_areas)) if zone_areas else 0.0,
        "processing_time_seconds": round(elapsed, 6),
        "geometry_outside_aoi_m2": geometry_outside_aoi_m2,
        "confidence_increase_count": int(confidence_increase_count),
        "conflicting_high_confidence_decisive_count": int(
            conflicting_high_confidence_decisive_count
        ),
        "raw_hash_before": raw_before,
        "raw_hash_after": raw_after,
        "raw_unchanged": raw_before == raw_after,
        "units": units,
        "zones": zones,
    }


def evaluate_operational_matrix(
    cases: Mapping[str, dict[str, Any]],
    *,
    configuration: OperationalAggregationConfig | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Executa 3x3 nos dois casos e seleciona somente configuracao segura."""
    config = configuration or OperationalAggregationConfig()
    case_results: dict[tuple[str, int, float], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for case_name in sorted(cases):
        for resolution in sorted(set(config.resolutions_m)):
            for dominance in sorted(set(config.dominance_thresholds)):
                result = evaluate_operational_aggregation(
                    cases[case_name],
                    resolution_m=resolution,
                    dominance_threshold=dominance,
                    clock=clock,
                )
                case_results[(case_name, resolution, dominance)] = result
                rows.append(
                    {
                        key: value
                        for key, value in result.items()
                        if key not in {"units", "zones"}
                    }
                    | {"case": case_name}
                )

    configuration_rows: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for resolution in sorted(set(config.resolutions_m)):
        for dominance in sorted(set(config.dominance_thresholds)):
            by_case = {
                case_name: case_results[(case_name, resolution, dominance)]
                for case_name in sorted(cases)
            }
            louveira = by_case.get("louveira")
            frango = by_case.get("frango_assado")
            reasons: list[str] = []
            if louveira is None or frango is None:
                reasons.append("REQUIRED_REFERENCE_CASE_MISSING")
            else:
                if (
                    louveira["zone_reduction_pct"]
                    < config.min_louveira_zone_reduction_pct
                ):
                    reasons.append("LOUVEIRA_FRAGMENTATION_REDUCTION_NOT_MATERIAL")
                if any(
                    result["class_area_reallocation_pct"]
                    > config.max_class_reallocation_pct
                    for result in by_case.values()
                ):
                    reasons.append("CLASS_AREA_DISTORTION_EXCEEDED")
                if (
                    frango["inconclusive_area_pct"]
                    > config.max_frango_inconclusive_pct
                ):
                    reasons.append("FRANGO_BECAME_TOO_INCONCLUSIVE")
                if any(result["geometry_outside_aoi_m2"] > 1e-6 for result in by_case.values()):
                    reasons.append("GEOMETRY_OUTSIDE_AOI")
                if any(result["confidence_increase_count"] for result in by_case.values()):
                    reasons.append("CONFIDENCE_ARTIFICIALLY_INCREASED")
                if any(
                    result["conflicting_high_confidence_decisive_count"]
                    for result in by_case.values()
                ):
                    reasons.append("HIGH_CONFIDENCE_CONFLICT_NOT_PRESERVED")
                if any(not result["raw_unchanged"] for result in by_case.values()):
                    reasons.append("RAW_SEGMENTATION_CHANGED")
            row = {
                "resolution_m": resolution,
                "dominance_threshold": dominance,
                "eligible": not reasons,
                "rejection_reasons": reasons,
                "combined_class_area_reallocation_pct": sum(
                    result["class_area_reallocation_pct"]
                    for result in by_case.values()
                ),
                "case_metrics": {
                    case_name: {
                        "operational_zone_count": result["operational_zone_count"],
                        "zone_reduction_pct": result["zone_reduction_pct"],
                        "class_area_reallocation_pct": result[
                            "class_area_reallocation_pct"
                        ],
                        "inconclusive_area_pct": result["inconclusive_area_pct"],
                    }
                    for case_name, result in by_case.items()
                },
            }
            configuration_rows.append(row)
            if row["eligible"]:
                eligible.append(row)

    recommended = min(
        eligible,
        key=lambda row: (
            row["combined_class_area_reallocation_pct"],
            row["case_metrics"]["frango_assado"]["inconclusive_area_pct"],
            row["resolution_m"],
            -row["dominance_threshold"],
        ),
        default=None,
    )
    recommended_resolution = recommended["resolution_m"] if recommended else None
    recommended_dominance = recommended["dominance_threshold"] if recommended else None
    recommended_outputs = (
        {
            case_name: case_results[
                (case_name, recommended_resolution, recommended_dominance)
            ]
            for case_name in sorted(cases)
        }
        if recommended is not None
        else {}
    )
    return {
        "status": "experimental",
        "mode": "shadow",
        "configuration": asdict(config),
        "experiments": rows,
        "candidate_evaluation": configuration_rows,
        "recommended_operational_resolution": recommended_resolution,
        "recommended_dominance_threshold": recommended_dominance,
        "recommended_outputs": recommended_outputs,
    }
