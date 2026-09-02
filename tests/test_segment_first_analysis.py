"""Testes offline da arquitetura temporal segment-first."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import socket

from affine import Affine
import numpy as np
from pyproj import Transformer
import pytest
from rasterio.warp import transform_geom
from shapely.geometry import LineString, box, shape
from shapely.ops import unary_union

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.segment_first_analysis import (
    build_section_scene_record,
    build_section_timeseries,
    build_spatial_segmentation_contract,
    merge_segment_first_sections,
    run_segment_first_shadow_segmentation,
    run_segment_first_temporal_analysis,
)
from src.satellite_monitoring.spatial_segmentation import SpatialRasterObservation


TRANSFORM = Affine(10, 0, 0, 0, -10, 20)
AOI = box(0, 0, 100, 20)


def _config(**overrides) -> MonitoringConfig:
    values = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]],
        },
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 24),
        "min_valid_pixel_count": 1,
        "min_valid_pixel_percentage": 70.0,
        "min_aoi_coverage_percentage": 95.0,
        "min_observations": 4,
    }
    values.update(overrides)
    return MonitoringConfig(**values)


def _observation(item_id: str, day: int, values: np.ndarray) -> SpatialRasterObservation:
    return SpatialRasterObservation(
        item_id=item_id,
        datetime=datetime(2026, 8, day, tzinfo=timezone.utc).isoformat(),
        ndvi=np.asarray(values, dtype=float),
        valid_mask=np.isfinite(values),
        inside_aoi_mask=np.ones(values.shape, dtype=bool),
        transform=TRANSFORM,
        aoi_geometry=AOI.__geo_interface__,
        crs="EPSG:3857",
        crs_is_projected=True,
        scene_quality_score=95.0,
        quality_status="high",
        cloud_cover=1.0,
        has_scl=True,
        scl_values=np.full(values.shape, 4, dtype=np.int16),
        scl_class_percentages={"vegetation": 100.0},
    )


def _daily(observations: list[SpatialRasterObservation]) -> list[dict]:
    return [
        {
            "item_id": observation.item_id,
            "datetime": observation.datetime,
            "daily_aggregation": "best",
            "aggregation_source_item_ids": [observation.item_id],
            "aggregation_selected_item_id": observation.item_id,
        }
        for observation in observations
    ]


def _roads() -> dict:
    reverse = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    start = reverse.transform(-100, 10)
    end = reverse.transform(200, 10)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "feature_id": "synthetic:SP-001:1",
                    "road_ref": "SP-001",
                    "road_name": "Synthetic Road",
                },
                "geometry": LineString([start, end]).__geo_interface__,
            }
        ],
    }


def _observations() -> list[SpatialRasterObservation]:
    return [
        _observation(
            f"scene-{day}",
            day,
            np.hstack(
                [
                    np.full((2, 5), left),
                    np.full((2, 5), right),
                ]
            ),
        )
        for day, left, right in (
            (1, 0.20, 0.70),
            (8, 0.30, 0.65),
            (15, 0.40, 0.60),
            (22, 0.50, 0.55),
        )
    ]


def test_section_pixel_selection_uses_only_section_pixels_and_real_area() -> None:
    observation = _observations()[0]
    record = build_section_scene_record(
        section_geometry=box(0, 0, 50, 20),
        observation=observation,
        config=_config(),
    )

    assert record["total_pixel_count"] == 10
    assert record["valid_pixel_count"] == 10
    assert record["ndvi_mean"] == pytest.approx(0.20)
    assert record["effective_analysis_area_m2"] == pytest.approx(1000.0)


def test_section_timeseries_is_independent_between_longitudinal_sections() -> None:
    observations = _observations()
    daily = _daily(observations)
    left, _ = build_section_timeseries(
        section_geometry=box(0, 0, 50, 20),
        observations=observations,
        daily_records=daily,
        config=_config(),
    )
    right, _ = build_section_timeseries(
        section_geometry=box(50, 0, 100, 20),
        observations=observations,
        daily_records=daily,
        config=_config(),
    )

    assert [row["ndvi_mean"] for row in left] == pytest.approx([0.2, 0.3, 0.4, 0.5])
    assert [row["ndvi_mean"] for row in right] == pytest.approx([0.7, 0.65, 0.6, 0.55])


def test_segment_geometry_is_inside_aoi_positive_and_non_overlapping() -> None:
    observations = _observations()
    result = run_segment_first_temporal_analysis(
        observations=observations,
        daily_records=_daily(observations),
        config=_config(),
        selected_area_m2=2000.0,
        road_document=_roads(),
        section_lengths_m=(25,),
        clock=lambda: 1.0,
    )
    sections = result["experiments"][0]["sections"]
    geometries = [shape(section["geometry"]) for section in sections]
    aoi_wgs = shape(transform_geom("EPSG:3857", "EPSG:4326", AOI.__geo_interface__))

    assert sections
    assert all(section["selected_area_m2"] > 0 for section in sections)
    assert sum(
        geometries[index].intersection(other).area
        for index, geometry in enumerate(geometries)
        for other in geometries[index + 1 :]
    ) < 1e-15
    assert unary_union(geometries).difference(aoi_wgs).area < 1e-15


def test_enough_data_is_decisive_and_insufficient_data_is_inconclusive() -> None:
    enough = _observations()
    complete = run_segment_first_temporal_analysis(
        observations=enough,
        daily_records=_daily(enough),
        config=_config(),
        selected_area_m2=2000.0,
        road_document=_roads(),
        section_lengths_m=(50,),
        clock=lambda: 1.0,
    )["experiments"][0]["sections"]
    insufficient = run_segment_first_temporal_analysis(
        observations=enough[:1],
        daily_records=_daily(enough[:1]),
        config=_config(),
        selected_area_m2=2000.0,
        road_document=_roads(),
        section_lengths_m=(50,),
        clock=lambda: 1.0,
    )["experiments"][0]["sections"]

    assert any(section["recommendation"] != "inconclusivo" for section in complete)
    assert all(section["recommendation"] == "inconclusivo" for section in insufficient)
    assert all("insufficient_observations" in section["reasons"] for section in insufficient)


def test_merge_is_consecutive_and_does_not_cross_gap() -> None:
    def section(sequence: int, start: float, recommendation: str) -> dict:
        return {
            "section_id": f"section_{sequence:04d}",
            "sequence_index": sequence,
            "road_ref": "SP-001",
            "road_name": "Road",
            "start_distance_m": start,
            "end_distance_m": start + 25,
            "recommendation": recommendation,
            "selected_area_m2": 100.0,
            "effective_area_m2": 90.0,
            "confidence": "medium",
            "analysis_quality": "high",
            "geometry": box(sequence, 0, sequence + 0.5, 0.5).__geo_interface__,
        }

    zones = merge_segment_first_sections(
        [section(1, 0, "cortar"), section(2, 25, "cortar"), section(3, 75, "cortar")]
    )

    assert [zone["section_count"] for zone in zones] == [2, 1]


def test_raw_is_diagnostic_only_output_is_deterministic_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("segment-first attempted network access")
        ),
    )
    observations = _observations()
    raw = {
        "area_by_recommendation_m2": {"cortar": 1000.0, "nao_cortar": 1000.0},
        "cells": [{"cell_id": "diagnostic-only", "recommendation": "cortar"}],
    }
    before = deepcopy(raw)
    kwargs = {
        "observations": observations,
        "daily_records": _daily(observations),
        "config": _config(),
        "selected_area_m2": 2000.0,
        "road_document": _roads(),
        "raw_segmentation": raw,
        "section_lengths_m": (25, 50),
        "clock": lambda: 2.0,
    }

    first = run_segment_first_temporal_analysis(**kwargs)
    second = run_segment_first_temporal_analysis(**kwargs)

    assert first == second
    assert raw == before
    assert first["raw_segmentation_unchanged"] is True
    assert first["raw_classes_used_as_decision_input"] is False
    assert first["external_queries_per_section"] == 0


def test_eligibility_gate_uses_existing_temporal_support_and_configurable_length() -> None:
    observations = _observations()
    result = run_segment_first_shadow_segmentation(
        observations=observations,
        daily_records=_daily(observations),
        config=_config(spatial_section_length_m=50),
        selected_area_m2=2000.0,
        effective_analysis_area_m2=2000.0,
        road_document=_roads(),
        clock=lambda: 4.0,
    )
    not_applicable = run_segment_first_shadow_segmentation(
        observations=observations,
        daily_records=_daily(observations),
        config=_config(
            spatial_section_length_m=25,
            min_valid_pixel_count=30,
        ),
        selected_area_m2=2000.0,
        effective_analysis_area_m2=2000.0,
        road_document=_roads(),
        clock=lambda: 4.0,
    )

    assert result["section_length_m"] == 50
    assert result["status"] in {"available", "not_applicable"}
    experiment = run_segment_first_temporal_analysis(
        observations=observations,
        daily_records=_daily(observations),
        config=_config(),
        selected_area_m2=2000.0,
        road_document=_roads(),
        section_lengths_m=(50,),
        clock=lambda: 4.0,
    )["experiments"][0]
    for section in experiment["sections"]:
        section["effective_area_m2"] = max(1.0, section["effective_area_m2"])
        section["valid_observation_count"] = 4
    available = build_spatial_segmentation_contract(
        experiment,
        section_length_m=50,
        decision_min_observations=4,
    )

    assert available["status"] == "available"
    assert available["zones"]
    assert set(available["zones"][0]) == {
        "zone_id",
        "recommendation",
        "geometry",
        "area_m2",
        "confidence",
        "analysis_quality",
        "reasons",
        "start_distance_m",
        "end_distance_m",
        "road_ref",
    }
    assert not_applicable == {
        "status": "not_applicable",
        "experimental": True,
        "section_length_m": 25,
        "effective_coverage_pct": 0.0,
        "zones": [],
    }
