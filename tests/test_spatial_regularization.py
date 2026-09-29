"""Testes offline da regularizacao espacial V2."""

from __future__ import annotations

from copy import deepcopy

from shapely.geometry import box, shape
from shapely.ops import unary_union

from src.satellite_monitoring.spatial_regularization import (
    SpatialRegularizationConfig,
    evaluate_spatial_regularization,
)


def _cell(
    row: int,
    column: int,
    recommendation: str,
    *,
    confidence: str = "high",
    area_m2: float = 100.0,
) -> dict:
    size = 0.0001
    west = -47.0 + column * size
    north = -23.0 - row * size
    geometry = box(west, north - size, west + size, north)
    return {
        "cell_id": f"cell_r{row:06d}_c{column:06d}",
        "row": row,
        "column": column,
        "geometry": geometry.__geo_interface__,
        "cell_area_m2": 100.0,
        "intersection_area_m2": area_m2,
        "effective_area_m2": area_m2,
        "intersection_fraction": area_m2 / 100.0,
        "recommendation": recommendation,
        "confidence": confidence,
        "analysis_quality": "high",
        "reason_codes": ["synthetic_test_reason"],
    }


def _cross(center_class: str = "cortar", center_confidence: str = "low") -> dict:
    cells = [
        _cell(1, 1, center_class, confidence=center_confidence, area_m2=50.0),
        _cell(0, 1, "nao_cortar"),
        _cell(1, 0, "nao_cortar"),
        _cell(1, 2, "nao_cortar"),
        _cell(2, 1, "nao_cortar"),
    ]
    return {"status": "experimental", "cells": cells, "zones": [{"raw": True}]}


def _relaxed_config(**overrides) -> SpatialRegularizationConfig:
    values = {
        "max_changed_area_pct": 100.0,
        "max_class_area_delta_pct": 100.0,
        "min_zone_reduction_pct": 0.0,
    }
    values.update(overrides)
    return SpatialRegularizationConfig(**values)


def test_compares_mmu_2_3_4_and_identifies_microzone() -> None:
    result = evaluate_spatial_regularization(
        _cross(), configuration=_relaxed_config(), clock=lambda: 1.0
    )

    assert [item["mmu_cells"] for item in result["regularization"]["candidates"]] == [2, 3, 4]
    mmu2 = result["regularization"]["candidates"][0]
    assert mmu2["microzones_before"] == 5
    assert any(
        diagnostic["decision"] == "REGULARIZED"
        for diagnostic in mmu2["component_diagnostics"]
    )
    assert mmu2["zone_count_after"] < mmu2["zone_count_before"]


def test_high_confidence_island_is_never_changed() -> None:
    result = evaluate_spatial_regularization(
        _cross(center_confidence="high"),
        configuration=_relaxed_config(),
        clock=lambda: 1.0,
    )

    center = next(
        cell
        for cell in result["operational_segmentation"]["cells"]
        if (cell["row"], cell["column"]) == (1, 1)
    )
    assert center["recommendation"] == "cortar"
    assert center["regularized"] is False
    assert center["protected_high_confidence_zone"] is True
    assert all(
        candidate["high_confidence_changed_count"] == 0
        for candidate in result["regularization"]["candidates"]
    )


def test_inconclusive_is_never_converted_to_decisive() -> None:
    result = evaluate_spatial_regularization(
        _cross(center_class="inconclusivo"),
        configuration=_relaxed_config(),
        clock=lambda: 1.0,
    )

    center = next(
        cell
        for cell in result["operational_segmentation"]["cells"]
        if (cell["row"], cell["column"]) == (1, 1)
    )
    assert center["recommendation"] == "inconclusivo"
    assert center["regularized"] is False
    assert all(
        candidate["inconclusive_to_decisive_count"] == 0
        for candidate in result["regularization"]["candidates"]
    )


def test_boundary_dominance_requires_at_least_75_percent() -> None:
    raw = {
        "cells": [
            _cell(1, 1, "cortar", confidence="low"),
            _cell(0, 1, "nao_cortar"),
            _cell(2, 1, "nao_cortar"),
            _cell(1, 0, "inconclusivo"),
            _cell(1, 2, "inconclusivo"),
        ],
        "zones": [],
    }
    result = evaluate_spatial_regularization(
        raw, configuration=_relaxed_config(), clock=lambda: 1.0
    )
    center_diagnostic = next(
        item
        for item in result["regularization"]["candidates"][0]["component_diagnostics"]
        if item["recommendation"] == "cortar"
    )

    assert center_diagnostic["dominant_boundary_fraction"] < 0.75
    assert center_diagnostic["decision"] == "BOUNDARY_NOT_DOMINANT"


def test_diagonal_cells_are_not_neighbors() -> None:
    raw = {
        "cells": [
            _cell(0, 0, "cortar", confidence="low"),
            _cell(1, 1, "nao_cortar"),
        ],
        "zones": [],
    }
    result = evaluate_spatial_regularization(
        raw, configuration=_relaxed_config(), clock=lambda: 1.0
    )

    assert result["regularization"]["candidates"][0]["zone_count_after"] == 2
    assert not any(
        cell["regularized"] for cell in result["operational_segmentation"]["cells"]
    )


def test_geometry_and_area_are_preserved_and_raw_is_unchanged() -> None:
    raw = _cross()
    raw_before = deepcopy(raw)
    result = evaluate_spatial_regularization(
        raw, configuration=_relaxed_config(), clock=lambda: 1.0
    )
    operational = result["operational_segmentation"]
    raw_union = unary_union([shape(cell["geometry"]) for cell in raw["cells"]])
    operational_union = unary_union(
        [shape(zone["geometry"]) for zone in operational["zones"]]
    )

    assert raw == raw_before
    assert result["raw_segmentation"] == raw_before
    assert operational_union.difference(raw_union).area < 1e-18
    assert sum(operational["area_by_class_m2"].values()) == sum(
        cell["effective_area_m2"] for cell in raw["cells"]
    )


def test_output_is_deterministic_with_injected_clock() -> None:
    raw = _cross()
    first = evaluate_spatial_regularization(
        raw, configuration=_relaxed_config(), clock=lambda: 10.0
    )
    second = evaluate_spatial_regularization(
        raw, configuration=_relaxed_config(), clock=lambda: 10.0
    )

    assert first == second
    assert first["recommended_mmu"] == 2
    zone = first["operational_segmentation"]["zones"][0]
    assert {
        "zone_id",
        "recommendation",
        "area_m2",
        "cell_count",
        "confidence",
        "analysis_quality",
        "regularized",
        "source_zone_count",
    } <= set(zone)

