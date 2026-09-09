"""Testes offline das unidades operacionais da Spatial Segmentation V3."""

from __future__ import annotations

from copy import deepcopy

import pytest
from shapely.geometry import box, shape
from shapely.ops import unary_union

from src.satellite_monitoring.spatial_operational_aggregation import (
    OperationalAggregationConfig,
    build_operational_units,
    build_operational_zones,
    evaluate_operational_aggregation,
    evaluate_operational_matrix,
)


def _cell(
    row: int,
    column: int,
    recommendation: str,
    *,
    area_m2: float = 100.0,
    confidence: str = "medium",
) -> dict:
    size = 0.0001
    west = -47.0 + column * size
    north = -23.0 - row * size
    return {
        "cell_id": f"cell_r{row:06d}_c{column:06d}",
        "row": row,
        "column": column,
        "geometry": box(
            west, north - size, west + size, north
        ).__geo_interface__,
        "cell_area_m2": 100.0,
        "intersection_area_m2": area_m2,
        "effective_area_m2": area_m2,
        "recommendation": recommendation,
        "confidence": confidence,
        "analysis_quality": "high",
    }


def _raw(cells: list[dict], zone_count: int = 4) -> dict:
    return {
        "status": "experimental",
        "grid_resolution": {"x": 10.0, "y": 10.0},
        "cells": cells,
        "zones": [{"zone_id": f"zone_{index}"} for index in range(zone_count)],
    }


def test_operational_units_are_deterministic_and_grid_aligned() -> None:
    cells = [
        _cell(row, column, "cortar")
        for row in range(3)
        for column in range(3)
    ]

    first = build_operational_units(
        cells, resolution_m=20, dominance_threshold=0.70
    )
    second = build_operational_units(
        cells, resolution_m=20, dominance_threshold=0.70
    )

    assert first == second
    assert [unit["unit_id"] for unit in first] == [
        "unit_020m_r000000_c000000",
        "unit_020m_r000000_c000001",
        "unit_020m_r000001_c000000",
        "unit_020m_r000001_c000001",
    ]
    assert first[0]["raw_cell_count"] == 4


def test_aggregation_is_weighted_by_effective_area_not_cell_count() -> None:
    cells = [
        _cell(0, 0, "cortar", area_m2=20.0),
        _cell(0, 1, "cortar", area_m2=20.0),
        _cell(1, 0, "nao_cortar", area_m2=60.0),
    ]
    unit = build_operational_units(
        cells, resolution_m=20, dominance_threshold=0.70
    )[0]

    assert unit["raw_cell_count"] == 3
    assert unit["area_by_raw_class_m2"]["cortar"] == 40.0
    assert unit["area_by_raw_class_m2"]["nao_cortar"] == 60.0
    assert unit["recommendation"] == "inconclusivo"


@pytest.mark.parametrize(
    ("dominance", "expected"),
    [(0.70, "cortar"), (0.75, "cortar"), (0.80, "inconclusivo")],
)
def test_dominance_thresholds_70_75_80(dominance: float, expected: str) -> None:
    cells = [
        _cell(0, 0, "cortar", area_m2=76.0),
        _cell(0, 1, "nao_cortar", area_m2=24.0),
    ]

    unit = build_operational_units(
        cells, resolution_m=20, dominance_threshold=dominance
    )[0]

    assert unit["recommendation"] == expected


def test_conflicting_high_confidence_evidence_becomes_inconclusive() -> None:
    unit = build_operational_units(
        [
            _cell(0, 0, "cortar", area_m2=75.0, confidence="high"),
            _cell(0, 1, "nao_cortar", area_m2=25.0, confidence="high"),
        ],
        resolution_m=20,
        dominance_threshold=0.70,
    )[0]

    assert unit["high_confidence_conflict"] is True
    assert unit["recommendation"] == "inconclusivo"
    assert unit["confidence"] == "low"


def test_absence_of_dominance_is_inconclusive() -> None:
    unit = build_operational_units(
        [
            _cell(0, 0, "cortar", area_m2=55.0),
            _cell(0, 1, "nao_cortar", area_m2=45.0),
        ],
        resolution_m=20,
        dominance_threshold=0.70,
    )[0]

    assert unit["recommendation"] == "inconclusivo"


def test_operational_zones_use_four_neighbor_not_diagonal() -> None:
    def unit(row: int, column: int) -> dict:
        return {
            "unit_row": row,
            "unit_column": column,
            "recommendation": "cortar",
            "geometry": _cell(row, column, "cortar")["geometry"],
            "area_m2": 100.0,
            "confidence": "medium",
            "analysis_quality": "high",
            "raw_cell_count": 1,
            "high_confidence_conflict": False,
        }

    diagonal = build_operational_zones([unit(0, 0), unit(1, 1)])
    orthogonal = build_operational_zones([unit(0, 0), unit(0, 1)])

    assert len(diagonal) == 2
    assert len(orthogonal) == 1


def test_clipping_area_raw_and_output_are_preserved() -> None:
    raw = _raw(
        [
            _cell(0, 0, "cortar", area_m2=35.0),
            _cell(0, 1, "cortar", area_m2=100.0),
            _cell(1, 0, "nao_cortar", area_m2=65.0),
        ]
    )
    before = deepcopy(raw)
    result = evaluate_operational_aggregation(
        raw,
        resolution_m=20,
        dominance_threshold=0.70,
        clock=lambda: 1.0,
    )
    raw_footprint = unary_union([shape(cell["geometry"]) for cell in raw["cells"]])
    operational_footprint = unary_union(
        [shape(zone["geometry"]) for zone in result["zones"]]
    )

    assert raw == before
    assert result["raw_unchanged"] is True
    assert result["geometry_outside_aoi_m2"] == 0.0
    assert operational_footprint.difference(raw_footprint).area < 1e-18
    assert sum(result["area_by_class_m2"].values()) == 200.0
    assert sum(unit["effective_area_m2"] for unit in result["units"]) == 200.0


def test_matrix_has_nine_configurations_per_case_and_is_deterministic() -> None:
    cells = [
        _cell(row, column, "cortar" if column < 3 else "nao_cortar")
        for row in range(5)
        for column in range(5)
    ]
    cases = {
        "frango_assado": _raw(cells, zone_count=5),
        "louveira": _raw(cells, zone_count=20),
    }
    config = OperationalAggregationConfig(
        min_louveira_zone_reduction_pct=0.0,
        max_class_reallocation_pct=100.0,
        max_frango_inconclusive_pct=100.0,
    )

    first = evaluate_operational_matrix(cases, configuration=config, clock=lambda: 2.0)
    second = evaluate_operational_matrix(cases, configuration=config, clock=lambda: 2.0)

    assert first == second
    assert len(first["experiments"]) == 18
    assert len(first["candidate_evaluation"]) == 9
    assert first["recommended_operational_resolution"] in {20, 30, 50}
    assert first["recommended_dominance_threshold"] in {0.70, 0.75, 0.80}

