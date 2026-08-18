"""Testes offline da analise espectral temporal por pares."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from shapely.geometry import Polygon

from scripts.analyze_temporal_hypotheses import (
    EXPANDED_SPATIAL_SCENARIOS,
    SPECTRAL_FEATURE_NAMES,
    CandidatePolygon,
    ManagementFeature,
    apply_reflectance_scaling,
    build_spectral_feature_ranking,
    build_spectral_pair_outputs,
    build_temporal_pairs,
    calculate_spectral_indices,
    classify_binary_transition,
    classify_class_transition,
    select_best_causal_scene,
    summarize_spectral_cross_sectional,
)


def _row(sample_id: str, snapshot: str, component: str, height: int) -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "source_file": f"field-{snapshot}.xlsx",
        "source_snapshot_date": snapshot,
        "road": "SP-021",
        "km_m": "1000",
        "km": "1",
        "asset_component": component,
        "height_level": str(height),
        "usable_for_height_classification": "YES",
        "coordinate_method": "KM_MARKER_EXACT",
        "mowing_method_dominant": "Apenas manual",
        "mowing_method_km_bucket": "1",
    }


def test_requested_spectral_indices_use_documented_formulas() -> None:
    bands = {
        "blue": np.asarray([0.1]),
        "red": np.asarray([0.2]),
        "red_edge_1": np.asarray([0.25]),
        "nir": np.asarray([0.6]),
        "narrow_nir": np.asarray([0.5]),
    }

    indices = calculate_spectral_indices(bands)

    assert indices["ndvi"][0] == pytest.approx(0.5)
    assert indices["evi"][0] == pytest.approx(1.0 / 2.05)
    assert indices["savi"][0] == pytest.approx(0.6 / 1.3)
    assert indices["ndre"][0] == pytest.approx(1.0 / 3.0)


def test_reflectance_scaling_applies_metadata_scale_and_offset() -> None:
    raw = np.asarray([1979.0, 3683.0])

    physical = apply_reflectance_scaling(raw, scale=0.0001, offset=-0.1)

    assert physical == pytest.approx([0.0979, 0.2683])


def test_evi_and_savi_are_computed_from_physical_reflectance() -> None:
    raw = {
        "blue": np.asarray([1753.0]),
        "red": np.asarray([1979.0]),
        "red_edge_1": np.asarray([2500.0]),
        "nir": np.asarray([3683.0]),
        "narrow_nir": np.asarray([3400.0]),
    }
    physical = {
        name: apply_reflectance_scaling(values, scale=0.0001, offset=-0.1)
        for name, values in raw.items()
    }

    indices = calculate_spectral_indices(physical)

    blue, red, nir = physical["blue"][0], physical["red"][0], physical["nir"][0]
    assert indices["evi"][0] == pytest.approx(
        2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)
    )
    assert indices["savi"][0] == pytest.approx(
        1.5 * (nir - red) / (nir + red + 0.5)
    )
    assert abs(indices["evi"][0]) < 2
    assert abs(indices["savi"][0]) < 2


def test_ndvi_and_ndre_are_invariant_to_positive_multiplicative_scale() -> None:
    raw = {
        "blue": np.asarray([1200.0, 1400.0]),
        "red": np.asarray([2000.0, 2400.0]),
        "red_edge_1": np.asarray([2500.0, 2700.0]),
        "nir": np.asarray([4000.0, 4200.0]),
        "narrow_nir": np.asarray([3600.0, 3900.0]),
    }
    scaled = {name: values * 0.0001 for name, values in raw.items()}

    raw_indices = calculate_spectral_indices(raw)
    scaled_indices = calculate_spectral_indices(scaled)

    assert scaled_indices["ndvi"] == pytest.approx(raw_indices["ndvi"])
    assert scaled_indices["ndre"] == pytest.approx(raw_indices["ndre"])


def _scene_record(
    scene_date: str, item_id: str, *, accepted: bool = True, score: float = 80.0
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "datetime": f"{scene_date}T13:00:00Z",
        "accepted_for_timeseries": accepted,
        "scene_quality_score": score,
        "valid_pixel_percentage": 90.0,
        "cloud_cover": 5.0,
    }


def test_causal_scene_selection_uses_closest_accepted_past_scene() -> None:
    selected = select_best_causal_scene(
        [
            _scene_record("2026-03-10", "past"),
            _scene_record("2026-03-16", "future", score=100.0),
            _scene_record("2026-03-12", "rejected", accepted=False),
        ],
        date(2026, 3, 13),
    )

    assert selected is not None
    assert selected["item_id"] == "past"
    assert selected["sentinel_scene_date"] == "2026-03-10"
    assert selected["temporal_lag_days"] == 3


def test_causal_scene_selection_rejects_future_and_outside_lookback() -> None:
    assert (
        select_best_causal_scene(
            [
                _scene_record("2026-03-14", "future"),
                _scene_record("2026-02-11", "too-old"),
            ],
            date(2026, 3, 13),
        )
        is None
    )


def test_temporal_pairing_uses_explicit_key_and_classifies_transitions() -> None:
    rows = [
        _row("a-before", "2026-03-13", "LATERAL", 1),
        _row("a-after", "2026-03-20", "LATERAL", 3),
        _row("b-before", "2026-03-13", "CENTRAL", 3),
        _row("b-after", "2026-03-20", "CENTRAL", 2),
    ]

    pairs, excluded = build_temporal_pairs(rows)

    assert excluded == []
    assert len(pairs) == 2
    assert {pair.component for pair in pairs} == {"LATERAL", "CENTRAL"}
    by_component = {pair.component: pair for pair in pairs}
    assert by_component["LATERAL"].class_delta == 2
    assert by_component["LATERAL"].class_transition == "INCREASE"
    assert by_component["LATERAL"].binary_transition == "CROSSED_ABOVE_30"
    assert by_component["CENTRAL"].class_delta == -1
    assert by_component["CENTRAL"].class_transition == "DECREASE"
    assert (
        by_component["CENTRAL"].binary_transition
        == "CROSSED_BELOW_OR_EQUAL_30"
    )


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [(1, 1, "STABLE"), (1, 2, "INCREASE"), (3, 2, "DECREASE")],
)
def test_class_transition_classification(before: int, after: int, expected: str) -> None:
    assert classify_class_transition(before, after) == expected


def test_binary_transition_classification() -> None:
    assert classify_binary_transition(2, 3) == "CROSSED_ABOVE_30"
    assert classify_binary_transition(3, 2) == "CROSSED_BELOW_OR_EQUAL_30"
    assert classify_binary_transition(1, 2) == "STAYED_SAME_SIDE"


def test_pair_delta_is_after_minus_before_on_shared_geometry() -> None:
    rows = [
        _row("before", "2026-03-13", "LATERAL", 1),
        _row("after", "2026-03-20", "LATERAL", 2),
    ]
    pairs, _ = build_temporal_pairs(rows)
    pair = pairs[0]
    geometry = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    feature = ManagementFeature("geometry-1", "Apenas manual", "1", {}, geometry)
    candidate = CandidatePolygon(feature, 10.0, "compatible_in_both_snapshots")
    invalid_feature = ManagementFeature(
        "geometry-without-scenes", "Apenas manual", "1", {}, geometry
    )
    invalid_candidate = CandidatePolygon(
        invalid_feature, 12.0, "compatible_in_both_snapshots"
    )
    candidate_sets = {
        "before": {
            "D50": [candidate, invalid_candidate],
            "D100": [candidate, invalid_candidate],
        },
        "after": {
            "D50": [candidate, invalid_candidate],
            "D100": [candidate, invalid_candidate],
        },
    }
    before_values = {feature_name: 1.0 for feature_name in SPECTRAL_FEATURE_NAMES}
    after_values = {feature_name: 1.25 for feature_name in SPECTRAL_FEATURE_NAMES}
    observations = {
        "geometry-1": {
            date(2026, 3, 13): {
                **before_values,
                "item_id": "scene-before",
                "sentinel_scene_date": "2026-03-13",
                "temporal_delta_days": 0,
                "temporal_lag_days": 0,
                "quality_status": "high",
                "scene_quality_score": 90,
                "spectral_processing_status": "VALID_SPECTRAL_FEATURES",
                "spectral_valid_pixel_count": 5,
                "spectral_valid_pixel_percentage": 100,
                "effective_resolution_m": 10,
            },
            date(2026, 3, 20): {
                **after_values,
                "item_id": "scene-after",
                "sentinel_scene_date": "2026-03-20",
                "temporal_delta_days": 0,
                "temporal_lag_days": 0,
                "quality_status": "high",
                "scene_quality_score": 90,
                "spectral_processing_status": "VALID_SPECTRAL_FEATURES",
                "spectral_valid_pixel_count": 5,
                "spectral_valid_pixel_percentage": 100,
                "effective_resolution_m": 10,
            },
        }
    }

    feature_rows, paired_rows, delta_rows = build_spectral_pair_outputs(
        [pair], candidate_sets, observations
    )

    assert len(feature_rows) == 8
    assert len(paired_rows) == 2
    assert len(delta_rows) == 2
    assert all(row["candidate_with_valid_pair_count"] == 1 for row in delta_rows)
    assert all(row["delta_ndvi"] == pytest.approx(0.25) for row in delta_rows)
    assert all(row["class_delta"] == 1 for row in delta_rows)
    assert all(row["group_geometry_id"] == "geometry-1" for row in delta_rows)
    assert all(
        row["candidate_geometry_id"] == "geometry-1;geometry-without-scenes"
        for row in delta_rows
    )
    _, cross_metrics = summarize_spectral_cross_sectional(delta_rows)
    assert cross_metrics["D50"]["ndvi"]["unique_geometry_count"] == 1


def test_pair_marks_same_acquisition_and_preserves_zero_delta() -> None:
    rows = [
        _row("before", "2026-03-13", "LATERAL", 1),
        _row("after", "2026-03-20", "LATERAL", 1),
    ]
    pairs, _ = build_temporal_pairs(rows)
    geometry = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    feature = ManagementFeature("geometry-1", "Apenas manual", "1", {}, geometry)
    candidate = CandidatePolygon(feature, 10.0, "compatible_in_both_snapshots")
    candidate_sets = {
        sample_id: {scenario: [candidate] for scenario in EXPANDED_SPATIAL_SCENARIOS}
        for sample_id in ("before", "after")
    }
    values = {feature_name: 0.25 for feature_name in SPECTRAL_FEATURE_NAMES}
    observation = {
        **values,
        "item_id": "same-scene",
        "sentinel_scene_date": "2026-03-10",
        "temporal_delta_days": -3,
        "temporal_lag_days": 3,
        "quality_status": "high",
        "scene_quality_score": 90,
        "spectral_processing_status": "VALID_SPECTRAL_FEATURES",
        "spectral_valid_pixel_count": 5,
        "spectral_valid_pixel_percentage": 100,
        "effective_resolution_m": 10,
    }
    observations = {
        "geometry-1": {
            date(2026, 3, 13): observation,
            date(2026, 3, 20): {**observation, "temporal_lag_days": 10},
        }
    }

    _, paired_rows, delta_rows = build_spectral_pair_outputs(
        pairs, candidate_sets, observations
    )

    assert all(row["same_acquisition_before_after"] is True for row in paired_rows)
    assert all(row["delta_ndvi"] == pytest.approx(0.0) for row in delta_rows)


def test_feature_ranking_is_deterministic() -> None:
    delta_metrics = {
        scenario: {
            feature: {
                "spearman": {
                    "status": "CALCULATED",
                    "n": 40,
                    "correlation": 0.3,
                    "p_value": 0.05,
                },
                "valid_pair_count": 40,
                "eligible_pair_count": 50,
                "coverage": 0.8,
                "class_transition_median_range": 0.2,
                "normalized_transition_separation": 0.6,
            }
            for feature in SPECTRAL_FEATURE_NAMES
        }
        for scenario in EXPANDED_SPATIAL_SCENARIOS
    }
    cross_metrics = {
        scenario: {
            feature: {
                "n": 80,
                "low_n": 60,
                "high_n": 20,
                "auc_feature": 0.3,
                "auc_directional_inverted": 0.7,
                "directional_auc": 0.7,
                "valid_pair_local_count": 40,
                "unique_geometry_count": 20,
                "unique_sentinel_acquisition_count": 6,
            }
            for feature in SPECTRAL_FEATURE_NAMES
        }
        for scenario in EXPANDED_SPATIAL_SCENARIOS
    }

    first = build_spectral_feature_ranking(
        delta_metrics, cross_metrics, unique_sentinel_acquisitions=6
    )
    second = build_spectral_feature_ranking(
        delta_metrics, cross_metrics, unique_sentinel_acquisitions=6
    )

    assert first["ranking"] == second["ranking"]
    assert first["best_cross_sectional_feature"] == second["best_cross_sectional_feature"]
    assert all(item["classification"] == "PROMISING" for item in first["ranking"])
    assert [item["feature"] for item in first["ranking"]] == sorted(
        SPECTRAL_FEATURE_NAMES
    )
    assert first["model_experiment_decision"] == "GO_TO_GROUP_AWARE_MODEL_EXPERIMENT"
