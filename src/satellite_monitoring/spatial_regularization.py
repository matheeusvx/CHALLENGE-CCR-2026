"""Regularizacao espacial V2 conservadora sobre a classificacao raw da V1."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from statistics import median
from time import perf_counter
from typing import Any, Callable, Sequence

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union


CLASSES = ("cortar", "nao_cortar", "inconclusivo")
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class SpatialRegularizationConfig:
    mmu_candidates: tuple[int, ...] = (2, 3, 4)
    boundary_dominance_threshold: float = 0.75
    max_changed_area_pct: float = 5.0
    max_class_area_delta_pct: float = 5.0
    min_zone_reduction_pct: float = 5.0

    def __post_init__(self) -> None:
        if not self.mmu_candidates or any(value < 2 for value in self.mmu_candidates):
            raise ValueError("MMU candidates must contain integers >= 2.")
        if not 0.5 < self.boundary_dominance_threshold <= 1.0:
            raise ValueError("Boundary dominance must be in (0.5, 1.0].")
        for value in (
            self.max_changed_area_pct,
            self.max_class_area_delta_pct,
            self.min_zone_reduction_pct,
        ):
            if not 0 <= value <= 100:
                raise ValueError("Regularization percentages must be in [0, 100].")


@dataclass(frozen=True)
class _Component:
    component_id: str
    recommendation: str
    cells: tuple[dict[str, Any], ...]
    area_m2: float
    cell_count: int
    has_high_confidence: bool


def _cell_area(cell: dict[str, Any]) -> float:
    return float(cell.get("effective_area_m2", cell.get("intersection_area_m2", 0.0)))


def _groups(cells: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_position = {(int(cell["row"]), int(cell["column"])): cell for cell in cells}
    pending = set(by_position)
    groups: list[list[dict[str, Any]]] = []
    for start in sorted(by_position):
        if start not in pending:
            continue
        recommendation = str(by_position[start]["recommendation"])
        queue: deque[tuple[int, int]] = deque([start])
        pending.remove(start)
        group: list[dict[str, Any]] = []
        while queue:
            row, column = queue.popleft()
            cell = by_position[(row, column)]
            group.append(cell)
            for neighbor in (
                (row - 1, column),
                (row, column - 1),
                (row, column + 1),
                (row + 1, column),
            ):
                if (
                    neighbor in pending
                    and str(by_position[neighbor]["recommendation"]) == recommendation
                ):
                    pending.remove(neighbor)
                    queue.append(neighbor)
        groups.append(sorted(group, key=lambda item: (item["row"], item["column"])))
    return groups


def _components(cells: Sequence[dict[str, Any]]) -> list[_Component]:
    return [
        _Component(
            component_id=f"component_{index:04d}",
            recommendation=str(group[0]["recommendation"]),
            cells=tuple(group),
            area_m2=float(sum(_cell_area(cell) for cell in group)),
            cell_count=len(group),
            has_high_confidence=any(
                str(cell.get("confidence", "low")) == "high" for cell in group
            ),
        )
        for index, group in enumerate(_groups(cells), start=1)
    ]


def _utm_epsg(cells: Sequence[dict[str, Any]]) -> int:
    geometries = [shape(cell["geometry"]) for cell in cells]
    centroid = unary_union(geometries).centroid
    zone = max(1, min(60, int((centroid.x + 180.0) // 6.0) + 1))
    return (32600 if centroid.y >= 0 else 32700) + zone


def _metric_geometries(cells: Sequence[dict[str, Any]]) -> dict[str, Any]:
    transformer = Transformer.from_crs(
        "EPSG:4326", f"EPSG:{_utm_epsg(cells)}", always_xy=True
    )
    return {
        str(cell["cell_id"]): transform(transformer.transform, shape(cell["geometry"]))
        for cell in cells
    }


def _component_adjacency(
    components: Sequence[_Component],
    metric_geometries: dict[str, Any],
) -> dict[str, dict[str, float]]:
    component_by_position: dict[tuple[int, int], _Component] = {}
    cell_by_position: dict[tuple[int, int], dict[str, Any]] = {}
    for component in components:
        for cell in component.cells:
            position = (int(cell["row"]), int(cell["column"]))
            component_by_position[position] = component
            cell_by_position[position] = cell
    shared: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for position in sorted(component_by_position):
        row, column = position
        source = component_by_position[position]
        source_cell = cell_by_position[position]
        for neighbor_position in ((row, column + 1), (row + 1, column)):
            neighbor = component_by_position.get(neighbor_position)
            if neighbor is None or neighbor.component_id == source.component_id:
                continue
            neighbor_cell = cell_by_position[neighbor_position]
            length = float(
                metric_geometries[str(source_cell["cell_id"])]
                .boundary.intersection(
                    metric_geometries[str(neighbor_cell["cell_id"])].boundary
                )
                .length
            )
            if length <= 0:
                continue
            shared[source.component_id][neighbor.recommendation] += length
            shared[neighbor.component_id][source.recommendation] += length
    return {key: dict(value) for key, value in shared.items()}


def _area_by_class(cells: Sequence[dict[str, Any]]) -> dict[str, float]:
    return {
        recommendation: float(
            sum(
                _cell_area(cell)
                for cell in cells
                if cell["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }


def _conservative_level(cells: Sequence[dict[str, Any]], field: str) -> str:
    return min(
        (str(cell.get(field, "low")) for cell in cells),
        key=lambda value: LEVEL_ORDER.get(value, -1),
        default="low",
    )


def _reason_codes(cells: Sequence[dict[str, Any]]) -> list[str]:
    counts = Counter(
        code for cell in cells for code in (cell.get("reason_codes") or [])
    )
    return [code for code, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _zones(
    cells: Sequence[dict[str, Any]],
    *,
    prefix: str,
    raw_zone_by_cell: dict[str, str],
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for index, group in enumerate(_groups(cells), start=1):
        geometry = unary_union([shape(cell["geometry"]) for cell in group])
        source_zone_ids = sorted(
            {raw_zone_by_cell[str(cell["cell_id"])] for cell in group}
        )
        zones.append(
            {
                "zone_id": f"{prefix}_zone_{index:04d}",
                "recommendation": group[0]["recommendation"],
                "geometry": mapping(geometry),
                "area_m2": float(sum(_cell_area(cell) for cell in group)),
                "cell_count": len(group),
                "confidence": _conservative_level(group, "confidence"),
                "analysis_quality": _conservative_level(group, "analysis_quality"),
                "representative_reason_codes": _reason_codes(group),
                "regularized": any(bool(cell.get("regularized")) for cell in group),
                "source_zone_count": len(source_zone_ids),
                "protected_high_confidence_zone": any(
                    bool(cell.get("protected_high_confidence_zone")) for cell in group
                ),
            }
        )
    return zones


def _summary(cells: Sequence[dict[str, Any]], zones: Sequence[dict[str, Any]]) -> dict[str, Any]:
    areas = _area_by_class(cells)
    zone_areas = [float(zone["area_m2"]) for zone in zones]
    return {
        "cell_count": len(cells),
        "zone_count": len(zones),
        "area_by_class_m2": areas,
        "segmented_area_m2": float(sum(areas.values())),
        "largest_zone_area_m2": max(zone_areas, default=0.0),
        "median_zone_area_m2": float(median(zone_areas)) if zone_areas else 0.0,
    }


def _delta_pct(delta: float, baseline: float) -> float | None:
    return delta / baseline * 100.0 if baseline > 0 else (0.0 if delta == 0 else None)


def _candidate(
    raw_cells: Sequence[dict[str, Any]],
    *,
    mmu_cells: int,
    nominal_cell_area_m2: float,
    configuration: SpatialRegularizationConfig,
    clock: Callable[[], float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = clock()
    raw_components = _components(raw_cells)
    raw_zone_by_cell = {
        str(cell["cell_id"]): component.component_id
        for component in raw_components
        for cell in component.cells
    }
    metric_geometries = _metric_geometries(raw_cells)
    adjacency = _component_adjacency(raw_components, metric_geometries)
    mmu_area_m2 = mmu_cells * nominal_cell_area_m2
    raw_areas = _area_by_class(raw_cells)
    segmented_area = sum(raw_areas.values())
    max_changed_area = segmented_area * configuration.max_changed_area_pct / 100.0
    deltas = {recommendation: 0.0 for recommendation in CLASSES}
    proposals: list[tuple[_Component, str, float]] = []
    protected_components: set[str] = set()
    diagnostics: list[dict[str, Any]] = []

    for component in raw_components:
        is_microzone = component.area_m2 < mmu_area_m2
        diagnostic = {
            "component_id": component.component_id,
            "recommendation": component.recommendation,
            "area_m2": component.area_m2,
            "cell_count": component.cell_count,
            "confidence_levels": sorted(
                {str(cell.get("confidence", "low")) for cell in component.cells}
            ),
            "analysis_quality": _conservative_level(
                component.cells, "analysis_quality"
            ),
            "is_microzone": is_microzone,
            "protected_high_confidence_zone": False,
            "dominant_neighbor_class": None,
            "dominant_boundary_fraction": None,
            "shared_boundary_total_m": 0.0,
            "shared_boundary_by_class_m": {},
            "eligible": False,
            "decision": "NOT_BELOW_MMU",
        }
        if not is_microzone:
            diagnostics.append(diagnostic)
            continue
        if component.recommendation == "inconclusivo":
            diagnostic["decision"] = "INCONCLUSIVE_PRESERVED"
            diagnostics.append(diagnostic)
            continue
        if component.has_high_confidence:
            protected_components.add(component.component_id)
            diagnostic.update(
                {
                    "protected_high_confidence_zone": True,
                    "decision": "HIGH_CONFIDENCE_PROTECTED",
                }
            )
            diagnostics.append(diagnostic)
            continue
        boundary_by_class = adjacency.get(component.component_id, {})
        total_boundary = sum(boundary_by_class.values())
        diagnostic["shared_boundary_total_m"] = total_boundary
        diagnostic["shared_boundary_by_class_m"] = boundary_by_class
        if total_boundary <= 0:
            diagnostic["decision"] = "NO_SHARED_BOUNDARY"
            diagnostics.append(diagnostic)
            continue
        dominant_class, dominant_length = max(
            boundary_by_class.items(), key=lambda item: (item[1], item[0])
        )
        dominance = dominant_length / total_boundary
        diagnostic.update(
            {
                "dominant_neighbor_class": dominant_class,
                "dominant_boundary_fraction": dominance,
            }
        )
        if dominance < configuration.boundary_dominance_threshold:
            diagnostic["decision"] = "BOUNDARY_NOT_DOMINANT"
            diagnostics.append(diagnostic)
            continue
        diagnostic.update({"eligible": True, "decision": "PENDING_STABILITY"})
        proposals.append((component, dominant_class, dominance))
        diagnostics.append(diagnostic)

    accepted_components: dict[str, str] = {}
    changed_area = 0.0
    diagnostics_by_id = {item["component_id"]: item for item in diagnostics}
    for component, target, _ in sorted(
        proposals, key=lambda item: (item[0].area_m2, item[0].component_id)
    ):
        source = component.recommendation
        next_changed = changed_area + component.area_m2
        next_deltas = dict(deltas)
        next_deltas[source] -= component.area_m2
        next_deltas[target] += component.area_m2
        within_total = next_changed <= max_changed_area + 1e-9
        within_classes = all(
            (
                abs(_delta_pct(next_deltas[value], raw_areas[value]) or 0.0)
                <= configuration.max_class_area_delta_pct + 1e-9
            )
            if raw_areas[value] > 0
            else next_deltas[value] == 0
            for value in CLASSES
        )
        diagnostic = diagnostics_by_id[component.component_id]
        if not within_total or not within_classes:
            diagnostic["decision"] = "STABILITY_LIMIT_REACHED"
            continue
        accepted_components[component.component_id] = target
        changed_area = next_changed
        deltas = next_deltas
        diagnostic["decision"] = "REGULARIZED"

    operational_cells: list[dict[str, Any]] = []
    high_confidence_changed_count = 0
    inconclusive_to_decisive_count = 0
    for raw_cell in raw_cells:
        cell = deepcopy(raw_cell)
        component_id = raw_zone_by_cell[str(raw_cell["cell_id"])]
        target = accepted_components.get(component_id)
        cell["regularized"] = target is not None
        cell["source_recommendation"] = raw_cell["recommendation"]
        cell["source_zone_id"] = component_id
        cell["protected_high_confidence_zone"] = component_id in protected_components
        if target is not None:
            if raw_cell.get("confidence") == "high":
                high_confidence_changed_count += 1
            if raw_cell["recommendation"] == "inconclusivo" and target != "inconclusivo":
                inconclusive_to_decisive_count += 1
            cell["recommendation"] = target
        operational_cells.append(cell)

    raw_zones = _zones(raw_cells, prefix="raw", raw_zone_by_cell=raw_zone_by_cell)
    operational_zones = _zones(
        operational_cells,
        prefix="operational",
        raw_zone_by_cell=raw_zone_by_cell,
    )
    operational_areas = _area_by_class(operational_cells)
    class_stability = {
        recommendation: {
            "area_delta_m2": operational_areas[recommendation] - raw_areas[recommendation],
            "area_delta_pct": _delta_pct(
                operational_areas[recommendation] - raw_areas[recommendation],
                raw_areas[recommendation],
            ),
        }
        for recommendation in CLASSES
    }
    zone_reduction_pct = (
        (len(raw_zones) - len(operational_zones)) / len(raw_zones) * 100.0
        if raw_zones
        else 0.0
    )
    elapsed = max(0.0, clock() - started)
    report = {
        "mmu_cells": mmu_cells,
        "mmu_effective_area_m2": mmu_area_m2,
        "zone_count_before": len(raw_zones),
        "zone_count_after": len(operational_zones),
        "zone_reduction_pct": zone_reduction_pct,
        "area_by_class_before_m2": raw_areas,
        "area_by_class_after_m2": operational_areas,
        "class_stability": class_stability,
        "changed_cell_count": sum(bool(cell["regularized"]) for cell in operational_cells),
        "changed_area_m2": changed_area,
        "changed_area_pct": changed_area / segmented_area * 100.0 if segmented_area else 0.0,
        "high_confidence_changed_count": high_confidence_changed_count,
        "inconclusive_to_decisive_count": inconclusive_to_decisive_count,
        "largest_zone_area_m2": max(
            (float(zone["area_m2"]) for zone in operational_zones), default=0.0
        ),
        "median_zone_area_m2": float(
            median([float(zone["area_m2"]) for zone in operational_zones])
        )
        if operational_zones
        else 0.0,
        "microzones_before": sum(
            component.area_m2 < mmu_area_m2 for component in raw_components
        ),
        "microzones_after": sum(
            float(zone["area_m2"]) < mmu_area_m2 for zone in operational_zones
        ),
        "processing_time_seconds": round(elapsed, 6),
        "stability_limits_satisfied": (
            changed_area <= max_changed_area + 1e-9
            and all(
                value["area_delta_pct"] is None
                or abs(float(value["area_delta_pct"]))
                <= configuration.max_class_area_delta_pct + 1e-9
                for value in class_stability.values()
            )
        ),
        "material_fragmentation_reduction": (
            zone_reduction_pct > 0
            and zone_reduction_pct >= configuration.min_zone_reduction_pct
        ),
        "component_diagnostics": diagnostics,
    }
    operational = {
        "mmu_cells": mmu_cells,
        "cells": operational_cells,
        "zones": operational_zones,
        **_summary(operational_cells, operational_zones),
    }
    return report, operational


def evaluate_spatial_regularization(
    raw_segmentation: dict[str, Any],
    *,
    configuration: SpatialRegularizationConfig | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Compara MMU 2/3/4 e recomenda apenas a menor opcao conservadora."""
    config = configuration or SpatialRegularizationConfig()
    raw_cells = deepcopy(list(raw_segmentation.get("cells") or []))
    if not raw_cells:
        raise ValueError("Raw segmentation has no cells to regularize.")
    nominal_areas = [
        float(cell.get("cell_area_m2", 0.0))
        for cell in raw_cells
        if float(cell.get("cell_area_m2", 0.0)) > 0
    ]
    if not nominal_areas:
        raise ValueError("Raw segmentation has no nominal cell area.")
    nominal_cell_area_m2 = float(median(nominal_areas))
    raw_components = _components(raw_cells)
    raw_zone_by_cell = {
        str(cell["cell_id"]): component.component_id
        for component in raw_components
        for cell in component.cells
    }
    raw_zones = _zones(raw_cells, prefix="raw", raw_zone_by_cell=raw_zone_by_cell)
    raw_payload = deepcopy(raw_segmentation)

    reports: list[dict[str, Any]] = []
    operational_by_mmu: dict[int, dict[str, Any]] = {}
    for mmu in sorted(set(config.mmu_candidates)):
        report, operational = _candidate(
            raw_cells,
            mmu_cells=mmu,
            nominal_cell_area_m2=nominal_cell_area_m2,
            configuration=config,
            clock=clock,
        )
        reports.append(report)
        operational_by_mmu[mmu] = operational

    eligible = [
        report
        for report in reports
        if report["stability_limits_satisfied"]
        and report["material_fragmentation_reduction"]
        and report["high_confidence_changed_count"] == 0
        and report["inconclusive_to_decisive_count"] == 0
    ]
    recommended_mmu = min(
        (int(report["mmu_cells"]) for report in eligible), default=None
    )
    max_mmu_area_m2 = max(config.mmu_candidates) * nominal_cell_area_m2
    protected_identity_components = {
        component.component_id
        for component in raw_components
        if component.has_high_confidence and component.area_m2 < max_mmu_area_m2
    }
    identity_cells: list[dict[str, Any]] = []
    for raw_cell in raw_cells:
        cell = deepcopy(raw_cell)
        component_id = raw_zone_by_cell[str(raw_cell["cell_id"])]
        cell.update(
            {
                "regularized": False,
                "source_recommendation": raw_cell["recommendation"],
                "source_zone_id": component_id,
                "protected_high_confidence_zone": (
                    component_id in protected_identity_components
                ),
            }
        )
        identity_cells.append(cell)
    identity_zones = _zones(
        identity_cells,
        prefix="operational",
        raw_zone_by_cell=raw_zone_by_cell,
    )
    operational = (
        operational_by_mmu[recommended_mmu]
        if recommended_mmu is not None
        else {
            "mmu_cells": None,
            "cells": identity_cells,
            "zones": identity_zones,
            **_summary(identity_cells, identity_zones),
        }
    )
    selected_report = next(
        (report for report in reports if report["mmu_cells"] == recommended_mmu),
        None,
    )
    return {
        "status": "experimental",
        "mode": "shadow",
        "recommended_mmu": recommended_mmu,
        "configuration": asdict(config),
        "raw_segmentation": raw_payload,
        "operational_segmentation": operational,
        "regularization": {
            "recommended_mmu": recommended_mmu,
            "raw": _summary(raw_cells, raw_zones),
            "operational": _summary(operational["cells"], operational["zones"]),
            "candidates": reports,
            "changed_cell_count": (
                selected_report["changed_cell_count"] if selected_report else 0
            ),
            "changed_area_m2": (
                selected_report["changed_area_m2"] if selected_report else 0.0
            ),
            "changed_area_pct": (
                selected_report["changed_area_pct"] if selected_report else 0.0
            ),
            "high_confidence_changed_count": (
                selected_report["high_confidence_changed_count"] if selected_report else 0
            ),
            "inconclusive_to_decisive_count": (
                selected_report["inconclusive_to_decisive_count"] if selected_report else 0
            ),
        },
    }
