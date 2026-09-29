"""Testes offline da segmentacao longitudinal alinhada a rodovias."""

from __future__ import annotations

from copy import deepcopy

import pytest
from shapely.geometry import LineString, box, shape
from shapely.ops import unary_union

from src.satellite_monitoring.road_aligned_segmentation import (
    RoadAlignedConfig,
    associate_road,
    build_road_sections,
    evaluate_road_aligned_configuration,
    evaluate_road_aligned_matrix,
    merge_consecutive_sections,
    metric_crs_for_geometry,
)


def _cell(
    column: int,
    recommendation: str,
    *,
    area_m2: float = 100.0,
    confidence: str = "medium",
    row: int = 0,
) -> dict:
    size = 0.0001
    west = -47.0 + column * size
    south = -23.0 + row * size
    return {
        "cell_id": f"cell_r{row:06d}_c{column:06d}",
        "row": row,
        "column": column,
        "geometry": box(west, south, west + size, south + size).__geo_interface__,
        "cell_area_m2": 100.0,
        "intersection_area_m2": area_m2,
        "effective_area_m2": area_m2,
        "recommendation": recommendation,
        "confidence": confidence,
        "analysis_quality": "high",
    }


def _roads(*lines: tuple[str, float]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "feature_id": f"feature-{index}",
                    "road_ref": road_ref,
                    "road_name": f"Road {road_ref}",
                },
                "geometry": LineString(
                    [(-47.001, latitude), (-46.995, latitude)]
                ).__geo_interface__,
            }
            for index, (road_ref, latitude) in enumerate(lines)
        ],
    }


def _raw(cells: list[dict], *, zones: int = 20) -> dict:
    return {
        "status": "experimental",
        "selected_area_m2": sum(cell["effective_area_m2"] for cell in cells),
        "cells": cells,
        "zones": [{"zone_id": f"zone_{index:04d}"} for index in range(zones)],
    }


def _association(cells: list[dict]):
    result = associate_road(cells, _roads(("SP-001", -22.99995)))
    assert result.status == "ASSOCIATED"
    return result


def test_road_association_is_metric_deterministic_and_reports_ambiguity() -> None:
    cells = [_cell(0, "cortar")]
    unique = associate_road(
        cells,
        _roads(("SP-001", -22.99995), ("SP-002", -22.99)),
        ambiguity_tolerance_m=10.0,
    )
    ambiguous = associate_road(
        cells,
        _roads(("SP-001", -22.99994), ("SP-002", -23.00004)),
        ambiguity_tolerance_m=25.0,
    )

    assert unique.status == "ASSOCIATED"
    assert unique.road_ref == "SP-001"
    assert unique.metric_crs == "EPSG:32723"
    assert ambiguous.status == "ROAD_ASSOCIATION_AMBIGUOUS"
    assert ambiguous.road_ref is None
    assert metric_crs_for_geometry(shape(cells[0]["geometry"])).to_epsg() == 32723


@pytest.mark.parametrize("length", [25, 50, 100])
def test_chainage_sections_have_requested_lengths_and_are_deterministic(length: int) -> None:
    cells = [_cell(index, "cortar") for index in range(12)]
    association = _association(cells)

    first = build_road_sections(
        cells, association, section_length_m=length, dominance_threshold=0.60
    )
    second = build_road_sections(
        cells, association, section_length_m=length, dominance_threshold=0.60
    )

    assert first == second
    assert all(0 < section["length_m"] <= length for section in first)
    assert all(
        section["section_id"] == f"section_{index:04d}"
        for index, section in enumerate(first, start=1)
    )


def test_sections_clip_to_raw_footprint_without_material_overlap() -> None:
    cells = [_cell(index, "cortar" if index < 3 else "nao_cortar") for index in range(6)]
    result = evaluate_road_aligned_configuration(
        _raw(cells),
        _association(cells),
        section_length_m=25,
        dominance_threshold=0.60,
        clock=lambda: 1.0,
    )
    raw_footprint = unary_union([shape(cell["geometry"]) for cell in cells])
    section_footprint = unary_union(
        [shape(section["geometry"]) for section in result["sections"]]
    )

    assert result["geometry_outside_aoi_m2"] <= 1e-6
    assert result["material_section_overlap_m2"] <= 1e-6
    assert section_footprint.difference(raw_footprint).area < 1e-12
    assert sum(result["area_by_class_m2"].values()) == pytest.approx(600.0)


@pytest.mark.parametrize(
    ("dominance", "expected"),
    [(0.60, "cortar"), (0.70, "inconclusivo"), (0.75, "inconclusivo")],
)
def test_area_weighted_dominance_60_70_75(dominance: float, expected: str) -> None:
    cells = [
        _cell(0, "cortar", area_m2=65.0),
        _cell(1, "nao_cortar", area_m2=35.0),
    ]
    sections = build_road_sections(
        cells,
        _association(cells),
        section_length_m=100,
        dominance_threshold=dominance,
    )

    assert len(sections) == 1
    assert sections[0]["cortar_area_m2"] == pytest.approx(65.0, abs=0.25)
    assert sections[0]["nao_cortar_area_m2"] == pytest.approx(35.0, abs=0.25)
    assert sections[0]["recommendation"] == expected


def test_conflicting_high_confidence_evidence_is_inconclusive() -> None:
    cells = [
        _cell(0, "cortar", area_m2=80.0, confidence="high"),
        _cell(1, "nao_cortar", area_m2=20.0, confidence="high"),
    ]
    section = build_road_sections(
        cells,
        _association(cells),
        section_length_m=100,
        dominance_threshold=0.60,
    )[0]

    assert section["high_confidence_conflict"] is True
    assert section["recommendation"] == "inconclusivo"
    assert section["classification_reason"] == "CONFLICTING_HIGH_CONFIDENCE_EVIDENCE"


def _section(sequence: int, recommendation: str, *, start: float | None = None) -> dict:
    start_distance = float(start if start is not None else (sequence - 1) * 25)
    return {
        "section_id": f"section_{sequence:04d}",
        "sequence_index": sequence,
        "road_ref": "SP-001",
        "road_name": "Road SP-001",
        "start_distance_m": start_distance,
        "end_distance_m": start_distance + 25.0,
        "recommendation": recommendation,
        "area_m2": 100.0,
        "raw_cell_ids": [f"cell-{sequence}"],
        "confidence": "medium",
        "analysis_quality": "high",
        "geometry": box(sequence, 0, sequence + 0.5, 0.5).__geo_interface__,
    }


def test_merge_only_joins_consecutive_equivalent_sections() -> None:
    zones = merge_consecutive_sections(
        [
            _section(1, "cortar"),
            _section(2, "cortar"),
            _section(3, "nao_cortar"),
            _section(4, "nao_cortar", start=100.0),
        ]
    )

    assert [zone["section_count"] for zone in zones] == [2, 1, 1]
    assert zones[0]["length_m"] == 50.0


def test_matrix_is_offline_deterministic_and_keeps_raw_unchanged() -> None:
    cells = [
        _cell(index, "cortar" if index < 6 else "nao_cortar")
        for index in range(12)
    ]
    cases = {
        "frango_assado": _raw(cells, zones=5),
        "louveira": _raw(cells, zones=20),
    }
    before = deepcopy(cases)
    config = RoadAlignedConfig(
        min_louveira_zone_reduction_pct=0.0,
        max_class_reallocation_pct=100.0,
        max_unassigned_area_pct=100.0,
        max_frango_inconclusive_pct=100.0,
    )

    first = evaluate_road_aligned_matrix(
        cases, _roads(("SP-001", -22.99995)), configuration=config, clock=lambda: 2.0
    )
    second = evaluate_road_aligned_matrix(
        cases, _roads(("SP-001", -22.99995)), configuration=config, clock=lambda: 2.0
    )

    assert first == second
    assert cases == before
    assert len(first["experiments"]) == 18
    assert len(first["candidate_evaluation"]) == 9
    assert all(row["raw_unchanged"] for row in first["experiments"])


def test_matrix_does_not_call_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def reject_network(*_args, **_kwargs):
        raise AssertionError("road-aligned segmentation attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    cells = [_cell(index, "cortar") for index in range(4)]
    cases = {
        "frango_assado": _raw(cells, zones=5),
        "louveira": _raw(cells, zones=20),
    }

    result = evaluate_road_aligned_matrix(
        cases,
        _roads(("SP-001", -22.99995)),
        configuration=RoadAlignedConfig(
            section_lengths_m=(25,),
            dominance_thresholds=(0.60,),
            min_louveira_zone_reduction_pct=0.0,
            max_class_reallocation_pct=100.0,
            max_unassigned_area_pct=100.0,
            max_frango_inconclusive_pct=100.0,
        ),
        clock=lambda: 3.0,
    )

    assert len(result["experiments"]) == 2
