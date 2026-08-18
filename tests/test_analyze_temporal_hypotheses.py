"""Testes offline da ferramenta exploratoria de hipoteses temporais."""

from __future__ import annotations

from datetime import date

import pytest
from shapely.geometry import Polygon

from scripts.analyze_temporal_hypotheses import (
    KmMarker,
    ManagementFeature,
    SpatialMatch,
    build_spatial_diagnostics,
    hypothesis_window,
    make_output_row,
    resolve_spatial_match,
    select_best_scene,
    stratified_sample,
    summarize_by_class,
)


def _scene(
    item_id: str,
    observed: str,
    *,
    accepted: bool = True,
    score: float = 90,
    valid: float = 90,
    cloud: float = 5,
    ndvi: float = 0.5,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "datetime": observed,
        "accepted_for_timeseries": accepted,
        "scene_quality_score": score,
        "valid_pixel_percentage": valid,
        "cloud_cover": cloud,
        "ndvi_median": ndvi,
    }


def test_hypothesis_windows_are_plus_minus_15_days() -> None:
    assert hypothesis_window(date(2025, 3, 28)) == (
        date(2025, 3, 13),
        date(2025, 4, 12),
    )
    assert hypothesis_window(date(2026, 3, 13)) == (
        date(2026, 2, 26),
        date(2026, 3, 28),
    )
    assert hypothesis_window(date(2026, 3, 20)) == (
        date(2026, 3, 5),
        date(2026, 4, 4),
    )


def test_scene_selection_prefers_closest_accepted_scene() -> None:
    selected = select_best_scene(
        [
            _scene("far", "2026-03-10T10:00:00Z", score=99),
            _scene("close", "2026-03-12T10:00:00Z", score=70),
            _scene("invalid", "2026-03-13T10:00:00Z", accepted=False),
        ],
        date(2026, 3, 13),
    )

    assert selected is not None
    assert selected["item_id"] == "close"
    assert selected["temporal_delta_days"] == -1


def test_scene_selection_tiebreaks_by_quality_then_valid_cloud_and_id() -> None:
    target = date(2026, 3, 13)
    assert select_best_scene(
        [
            _scene("lower-score", "2026-03-12", score=80, valid=99, cloud=0),
            _scene("higher-score", "2026-03-14", score=90, valid=70, cloud=20),
        ],
        target,
    )["item_id"] == "higher-score"
    assert select_best_scene(
        [
            _scene("lower-valid", "2026-03-12", valid=80),
            _scene("higher-valid", "2026-03-14", valid=90),
        ],
        target,
    )["item_id"] == "higher-valid"
    assert select_best_scene(
        [
            _scene("cloudy", "2026-03-12", cloud=10),
            _scene("clear", "2026-03-14", cloud=2),
        ],
        target,
    )["item_id"] == "clear"
    assert select_best_scene(
        [_scene("b", "2026-03-12"), _scene("a", "2026-03-14")], target
    )["item_id"] == "a"


def test_no_valid_scene_is_not_replaced_by_rejected_scene() -> None:
    assert select_best_scene(
        [_scene("rejected", "2026-03-13", accepted=False)],
        date(2026, 3, 13),
    ) is None
    match = SpatialMatch(
        "SPATIAL_MATCH_RESOLVED",
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
        "test",
        "explicit_sample_id",
        "test",
        1,
    )
    row = make_output_row(
        {"sample_id": "s1", "km": "1", "height_level": "1"},
        "H_B1",
        date(2026, 3, 13),
        match,
        None,
    )
    assert row["scene_selection_status"] == "NO_VALID_SCENE"
    assert row["ndvi_median"] is None


def test_ambiguous_spatial_match_stays_unresolved() -> None:
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    features = [
        ManagementFeature("one", None, None, {}, polygon),
        ManagementFeature("two", None, None, {}, polygon),
    ]
    row = {
        "sample_id": "s1",
        "asset_component": "lateral",
        "km": "1",
        "latitude": "1",
        "longitude": "1",
        "coordinate_method": "FIELD_SURVEY_POINT",
    }

    match = resolve_spatial_match(row, features, "management.kmz")

    assert match.status == "SPATIAL_MATCH_AMBIGUOUS"
    assert match.geometry is None
    assert match.candidate_count == 2


def test_axis_marker_point_is_never_automatically_used_as_polygon() -> None:
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    feature = ManagementFeature("one", "Apenas manual", "1", {}, polygon)
    row = {
        "sample_id": "s1",
        "asset_component": "lateral",
        "km": "1",
        "latitude": "1",
        "longitude": "1",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
    }

    match = resolve_spatial_match(row, [feature], "management.kmz")

    assert match.status == "SPATIAL_MATCH_UNRESOLVED"
    assert match.candidate_count == 1
    assert "no component or side metadata" in match.reason


def test_multiple_km_and_method_candidates_are_ambiguous_without_component_metadata() -> None:
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    features = [
        ManagementFeature("one", "Apenas manual", "1", {}, polygon),
        ManagementFeature("two", "Apenas manual", "1", {}, polygon),
    ]
    row = {
        "sample_id": "s1",
        "asset_component": "CANT. CENTRAL INTERNA",
        "km": "1",
        "latitude": "1",
        "longitude": "1",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
    }

    match = resolve_spatial_match(row, features, "management.kmz")

    assert match.status == "SPATIAL_MATCH_AMBIGUOUS"
    assert match.geometry is None
    assert match.candidate_count == 2


def test_spatial_diagnostics_rank_three_polygons_with_metric_distances() -> None:
    def polygon_at(longitude: float) -> Polygon:
        return Polygon(
            [
                (longitude, -0.0001),
                (longitude + 0.0001, -0.0001),
                (longitude + 0.0001, 0.0001),
                (longitude, 0.0001),
                (longitude, -0.0001),
            ]
        )

    features = [
        ManagementFeature(
            f"polygon-{index}", "Apenas manual", "1", {}, polygon_at(longitude)
        )
        for index, longitude in enumerate((0.001, 0.002, 0.003), start=1)
    ]
    sample = {
        "sample_id": "s1",
        "height_level": "1",
        "asset_component": "CANT. LATERAL INTERNA",
        "km": "1",
        "latitude": "0",
        "longitude": "0",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
    }

    rows, summary = build_spatial_diagnostics(
        [sample], features, {1: KmMarker(1, 0.0, 0.0, "marker-1")}
    )

    assert rows[0]["nearest_polygon_1_id"] == "polygon-1"
    assert rows[0]["nearest_polygon_2_id"] == "polygon-2"
    assert rows[0]["nearest_polygon_3_id"] == "polygon-3"
    assert 100 < rows[0]["nearest_polygon_1_distance_m"] < 120
    assert rows[0]["candidate_polygon_count"] == 3
    assert rows[0]["spatial_match_status"] == "SPATIAL_MATCH_AMBIGUOUS"
    assert summary["sentinel_2_queried"] is False


def test_stratified_sample_is_balanced_deterministic_and_spatially_distributed() -> None:
    rows = [
        {
            "sample_id": f"c{level}-km{km}-r{replica}",
            "height_level": str(level),
            "km": str(km),
            "asset_component": "lateral",
            "usable_for_height_classification": "YES",
        }
        for level in (1, 2, 3)
        for km in range(12)
        for replica in range(2)
    ]

    first = stratified_sample(rows, 30, seed=123)
    second = stratified_sample(rows, 30, seed=123)

    assert [row["sample_id"] for row in first] == [row["sample_id"] for row in second]
    assert {
        level: sum(int(row["height_level"]) == level for row in first)
        for level in (1, 2, 3)
    } == {1: 10, 2: 10, 3: 10}
    for level in (1, 2, 3):
        kms = {row["km"] for row in first if int(row["height_level"]) == level}
        assert len(kms) == 10


def test_summary_statistics_by_hypothesis_and_class() -> None:
    rows = []
    for height_class, values in {1: [0.2, 0.4], 2: [0.5, None], 3: [0.8, 1.0]}.items():
        for index, value in enumerate(values):
            rows.append(
                {
                    "hypothesis": "H_A",
                    "height_class": height_class,
                    "scene_selection_status": (
                        "VALID_SCENE_SELECTED" if value is not None else "NO_VALID_SCENE"
                    ),
                    "ndvi_median": value,
                    "temporal_delta_days": -index if value is not None else None,
                }
            )

    summary = {
        row["height_class"]: row
        for row in summarize_by_class(rows)
        if row["hypothesis"] == "H_A"
    }

    assert summary[1]["n_total"] == 2
    assert summary[1]["n_with_valid_scene"] == 2
    assert summary[1]["valid_scene_percentage"] == 100
    assert summary[1]["ndvi_mean"] == pytest.approx(0.3)
    assert summary[1]["ndvi_median"] == pytest.approx(0.3)
    assert summary[1]["ndvi_q1"] == pytest.approx(0.25)
    assert summary[1]["ndvi_q3"] == pytest.approx(0.35)
    assert summary[2]["n_with_valid_scene"] == 1
    assert summary[2]["valid_scene_percentage"] == 50
