"""Testes offline da calibracao temporal ampliada."""

from __future__ import annotations

from datetime import date

import pytest
from shapely.geometry import Polygon

from scripts.analyze_temporal_hypotheses import (
    CandidatePolygon,
    KmMarker,
    ManagementFeature,
    bootstrap_binary_metrics,
    build_expanded_candidate_rows,
    build_expanded_preflight,
    calculate_binary_metrics,
    expanded_target_date,
    resolve_filename_target_date,
    select_expanded_balanced_sample,
)


def _polygon() -> Polygon:
    return Polygon(
        [
            (-0.0001, -0.0001),
            (0.0001, -0.0001),
            (0.0001, 0.0001),
            (-0.0001, 0.0001),
            (-0.0001, -0.0001),
        ]
    )


def _row(sample_id: str, height: int, snapshot: str) -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "source_file": f"field-{snapshot}.xlsx",
        "source_snapshot_date": snapshot,
        "height_level": str(height),
        "asset_component": "CANT_LATERAL_INTERNA",
        "km": "1",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
        "usable_for_height_classification": "YES",
    }


def test_expanded_hypotheses_use_embedded_constant_and_explicit_snapshot() -> None:
    march_13 = _row("misleading_20260320_name", 1, "2026-03-13")
    march_20 = _row("misleading_20260313_name", 1, "2026-03-20")

    assert expanded_target_date(march_13, "H_EMBEDDED") == date(2025, 3, 28)
    assert expanded_target_date(march_20, "H_EMBEDDED") == date(2025, 3, 28)
    assert expanded_target_date(march_13, "H_FILENAME") == date(2026, 3, 13)
    assert expanded_target_date(march_20, "H_FILENAME") == date(2026, 3, 20)
    assert resolve_filename_target_date(march_13)[1] == "source_snapshot_date"


def test_preflight_and_selection_are_balanced_without_duplicates() -> None:
    rows = [
        _row(f"c{height}-{snapshot}-{index}", height, snapshot)
        for height in (1, 2, 3)
        for snapshot in ("2026-03-13", "2026-03-20")
        for index in range(2)
    ]
    feature = ManagementFeature("geometry-1", "Apenas manual", "1", {}, _polygon())
    preflight, eligible, candidate_sets = build_expanded_preflight(
        rows, [feature], {1: KmMarker(1, 0.0, 0.0, "marker-1")}
    )

    assert preflight["maximum_balanced_per_class_by_scenario"] == {
        "D50": 4,
        "D100": 4,
    }
    assert preflight["recommended_balanced_sample"]["total_records"] == 12
    selected = select_expanded_balanced_sample(
        eligible, candidate_sets, maximum_total=6, seed=123
    )
    assert len(selected) == 6
    assert len({row["sample_id"] for row in selected}) == 6
    assert {
        height: sum(int(row["height_level"]) == height for row in selected)
        for height in (1, 2, 3)
    } == {1: 2, 2: 2, 3: 2}
    assert {
        snapshot: sum(row["source_snapshot_date"] == snapshot for row in selected)
        for snapshot in ("2026-03-13", "2026-03-20")
    } == {"2026-03-13": 3, "2026-03-20": 3}


def test_binary_analysis_calculates_both_auc_directions() -> None:
    rows = [
        {"height_class": height, "ndvi_candidate_median": ndvi}
        for height, ndvi in (
            (1, 0.8),
            (1, 0.7),
            (2, 0.6),
            (2, 0.5),
            (3, 0.1),
            (3, 0.2),
            (3, 0.3),
        )
    ]

    metrics = calculate_binary_metrics(rows, bootstrap_seed=77)

    assert metrics["low_or_acceptable_le_30cm"]["n"] == 4
    assert metrics["high_gt_30cm"]["n"] == 3
    assert metrics["median_difference_high_minus_low"] == pytest.approx(-0.45)
    assert metrics["auc_ndvi"]["value"] == pytest.approx(0.0)
    assert metrics["auc_negative_ndvi"]["value"] == pytest.approx(1.0)
    assert metrics["mann_whitney_u"]["status"] == "CALCULATED"


def test_bootstrap_is_deterministic() -> None:
    first = bootstrap_binary_metrics(
        [0.5, 0.6, 0.7, 0.8], [0.1, 0.2, 0.3], iterations=100, seed=99
    )
    second = bootstrap_binary_metrics(
        [0.5, 0.6, 0.7, 0.8], [0.1, 0.2, 0.3], iterations=100, seed=99
    )

    assert first == second
    assert first["status"] == "CALCULATED"
    assert first["iterations"] == 100
    assert first["auc_negative_ndvi_ci95"] == {"lower": 1.0, "upper": 1.0}


def test_shared_geometry_and_scene_are_reported_without_extra_processing() -> None:
    samples = [
        _row("sample-a", 1, "2026-03-13"),
        _row("sample-b", 3, "2026-03-13"),
    ]
    feature = ManagementFeature("shared-geometry", "Apenas manual", "1", {}, _polygon())
    candidate = CandidatePolygon(feature, 0.0, "km_and_optional_mowing_method_only")
    candidate_sets = {
        sample["sample_id"]: {"D50": [candidate], "D100": [candidate]}
        for sample in samples
    }
    calls = []

    def query_scenes(geometry, start_date, end_date):
        calls.append((start_date, end_date))
        if start_date.year == 2025:
            item_id, observed = "embedded-scene", "2025-03-28"
        else:
            item_id, observed = "filename-scene", "2026-03-13"
        return (
            [
                {
                    "item_id": item_id,
                    "datetime": observed,
                    "accepted_for_timeseries": True,
                    "scene_quality_score": 90,
                    "valid_pixel_percentage": 90,
                    "cloud_cover": 1,
                    "ndvi_median": 0.4,
                }
            ],
            None,
        )

    rows, errors, unique_geometries, unique_items = build_expanded_candidate_rows(
        samples, candidate_sets, query_scenes=query_scenes
    )

    assert len(calls) == 2
    assert unique_geometries == 1
    assert unique_items == 2
    assert errors == []
    assert len(rows) == 8
    assert all(row["shared_geometry_count"] == 2 for row in rows)
    assert all(row["shared_scene_count"] == 2 for row in rows)
    assert all(row["shared_km_geometry_count"] == 2 for row in rows)
    assert all(row["ground_truth_assignment"] is False for row in rows)
