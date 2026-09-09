"""Testes offline dos conjuntos ambiguos de poligonos candidatos."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from scripts.analyze_temporal_hypotheses import (
    CandidatePolygon,
    KmMarker,
    ManagementFeature,
    aggregate_candidate_ndvi,
    build_candidate_polygon_ndvi_rows,
    build_candidate_polygon_sets,
)


def _polygon_at(longitude: float) -> Polygon:
    return Polygon(
        [
            (longitude, -0.00005),
            (longitude + 0.00002, -0.00005),
            (longitude + 0.00002, 0.00005),
            (longitude, 0.00005),
            (longitude, -0.00005),
        ]
    )


def _sample(sample_id: str = "sample-1") -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "height_level": "1",
        "asset_component": "CANT_LATERAL_INTERNA",
        "km": "1",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
    }


def _feature(feature_id: str, longitude: float, method: str = "Apenas manual") -> ManagementFeature:
    return ManagementFeature(feature_id, method, "1", {}, _polygon_at(longitude))


def test_candidate_sets_are_cumulative_at_d20_d50_d100_and_stop_at_100m() -> None:
    features = [
        _feature("within-20", 0.0001),
        _feature("within-50", 0.0003),
        _feature("within-100", 0.0008),
        _feature("beyond-100", 0.0012),
    ]

    sets = build_candidate_polygon_sets(
        [_sample()], features, {1: KmMarker(1, 0.0, 0.0, "marker-1")}
    )["sample-1"]

    assert [item.feature.feature_id for item in sets["D20"]] == ["within-20"]
    assert [item.feature.feature_id for item in sets["D50"]] == [
        "within-20",
        "within-50",
    ]
    assert [item.feature.feature_id for item in sets["D100"]] == [
        "within-20",
        "within-50",
        "within-100",
    ]
    assert all(item.distance_m <= 100 for item in sets["D100"])


def test_candidate_sets_require_mowing_method_compatibility_when_available() -> None:
    sets = build_candidate_polygon_sets(
        [_sample()],
        [
            _feature("compatible", 0.0001),
            _feature("wrong-method", 0.00005, "Trator com braco articulado"),
        ],
        {1: KmMarker(1, 0.0, 0.0, "marker-1")},
    )["sample-1"]

    assert [item.feature.feature_id for item in sets["D100"]] == ["compatible"]


def test_candidate_sets_preserve_absence_without_forcing_nearest_polygon() -> None:
    sets = build_candidate_polygon_sets(
        [_sample()],
        [
            _feature("too-far", 0.002),
            ManagementFeature("wrong-km", "Apenas manual", "2", {}, _polygon_at(0.00001)),
        ],
        {1: KmMarker(1, 0.0, 0.0, "marker-1")},
    )["sample-1"]

    assert sets == {"D20": [], "D50": [], "D100": []}


def test_candidate_ndvi_aggregation_reports_median_and_dispersion() -> None:
    sample = _sample()
    candidates = [
        CandidatePolygon(_feature("one", 0.0001), 10.0, "km_and_optional_mowing_method_only"),
        CandidatePolygon(_feature("two", 0.0002), 20.0, "km_and_optional_mowing_method_only"),
        CandidatePolygon(_feature("three", 0.0003), 30.0, "km_and_optional_mowing_method_only"),
    ]
    candidate_sets = {
        "sample-1": {"D20": candidates, "D50": candidates, "D100": candidates}
    }
    rows = [
        {
            "sample_id": "sample-1",
            "hypothesis": "H_A",
            "distance_scenario": "D20",
            "scene_selection_status": "VALID_SCENE_SELECTED",
            "ndvi_median": value,
        }
        for value in (0.2, 0.4, 0.8)
    ]

    aggregate = next(
        row
        for row in aggregate_candidate_ndvi([sample], candidate_sets, rows)
        if row["hypothesis"] == "H_A" and row["distance_scenario"] == "D20"
    )

    assert aggregate["candidate_polygon_count"] == 3
    assert aggregate["candidate_with_valid_scene_count"] == 3
    assert aggregate["ndvi_candidate_median"] == pytest.approx(0.4)
    assert aggregate["ndvi_candidate_q1"] == pytest.approx(0.3)
    assert aggregate["ndvi_candidate_q3"] == pytest.approx(0.6)
    assert aggregate["ndvi_candidate_std"] == pytest.approx(0.2494438258)
    assert aggregate["ndvi_candidate_iqr"] == pytest.approx(0.3)
    assert aggregate["ndvi_candidate_range"] == pytest.approx(0.6)
    assert aggregate["spatial_uncertainty_status"] == "AMBIGUOUS_CANDIDATE_SET"
    assert aggregate["ground_truth_assignment"] is False


def test_candidate_processing_is_cached_and_never_marks_ground_truth() -> None:
    sample = _sample()
    candidate = CandidatePolygon(
        _feature("shared", 0.0001), 10.0, "km_and_optional_mowing_method_only"
    )
    candidate_sets = {
        "sample-1": {"D20": [candidate], "D50": [candidate], "D100": [candidate]}
    }
    calls: list[tuple[object, object]] = []

    def query_scenes(geometry, start_date, end_date):
        calls.append((start_date, end_date))
        return (
            [
                {
                    "item_id": "scene",
                    "datetime": "2026-03-13",
                    "accepted_for_timeseries": True,
                    "scene_quality_score": 90,
                    "valid_pixel_percentage": 90,
                    "cloud_cover": 1,
                    "ndvi_median": 0.5,
                }
            ],
            None,
        )

    rows, errors, unique_count = build_candidate_polygon_ndvi_rows(
        [sample], candidate_sets, query_scenes=query_scenes
    )

    assert len(calls) == 2
    assert unique_count == 1
    assert errors == []
    assert len(rows) == 9
    assert all(row["spatial_uncertainty_status"] == "AMBIGUOUS_CANDIDATE_SET" for row in rows)
    assert all(row["ground_truth_assignment"] is False for row in rows)


def test_no_candidates_produces_explicit_empty_uncertainty_rows() -> None:
    sample = _sample()
    candidate_sets = {"sample-1": {"D20": [], "D50": [], "D100": []}}

    rows = aggregate_candidate_ndvi([sample], candidate_sets, [])

    assert len(rows) == 9
    assert all(row["candidate_polygon_count"] == 0 for row in rows)
    assert all(row["candidate_with_valid_scene_count"] == 0 for row in rows)
    assert all(row["ndvi_candidate_median"] is None for row in rows)
    assert all(row["spatial_uncertainty_status"] == "NO_ELIGIBLE_CANDIDATES" for row in rows)
    assert all(row["ground_truth_assignment"] is False for row in rows)
