"""Testes offline da segmentacao espacial experimental."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from affine import Affine
from rasterio.warp import transform_geom
from shapely.geometry import box, shape

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.cut_recommendation import RecommendationInput, recommend_cut
from src.satellite_monitoring.raster_processing import RasterSceneData
from src.satellite_monitoring.service import PipelineDependencies, run_monitoring_analysis
from src.satellite_monitoring.spatial_segmentation import (
    SpatialRasterObservation,
    _thresholds,
    align_observations_to_reference,
    build_cell_timeseries,
    build_grid_cells,
    classify_cell_timeseries,
    group_cells_4_neighbor,
    run_spatial_segmentation,
)
from src.satellite_monitoring.stac_client import Scene, SceneSearchResult


TRANSFORM = Affine(10, 0, 0, 0, -10, 20)
AOI = box(0, 0, 20, 20)


def _config(tmp_path: Path | None = None, **overrides) -> MonitoringConfig:
    values = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]],
        },
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 24),
        "min_valid_pixel_count": 1,
        "min_observations": 4,
        "output_root": tmp_path or Path("outputs/test-spatial-segmentation"),
    }
    values.update(overrides)
    return MonitoringConfig(**values)


def _observation(
    item_id: str,
    day: int,
    values: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    aoi=AOI,
) -> SpatialRasterObservation:
    mask = np.ones(values.shape, dtype=bool) if valid_mask is None else valid_mask
    ndvi = np.where(mask, np.asarray(values, dtype=float), np.nan)
    return SpatialRasterObservation(
        item_id=item_id,
        datetime=datetime(2026, 8, day, tzinfo=timezone.utc).isoformat(),
        ndvi=ndvi,
        valid_mask=mask,
        inside_aoi_mask=np.ones(values.shape, dtype=bool),
        transform=TRANSFORM,
        aoi_geometry=dict(aoi.__geo_interface__),
        crs="EPSG:3857",
        crs_is_projected=True,
        scene_quality_score=95.0,
        quality_status="high",
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


def test_grid_is_deterministic_and_accounts_for_partial_cells() -> None:
    partial_aoi = box(0, 0, 15, 20)
    observation = _observation("scene", 22, np.full((2, 2), 0.5), aoi=partial_aoi)

    first = build_grid_cells(observation)
    second = build_grid_cells(observation)

    assert [cell.cell_id for cell in first] == [cell.cell_id for cell in second]
    assert [cell.cell_id for cell in first] == [
        "cell_r000000_c000000",
        "cell_r000000_c000001",
        "cell_r000001_c000000",
        "cell_r000001_c000001",
    ]
    assert first[1].cell_area_m2 == 100.0
    assert first[1].intersection_area_m2 == 50.0
    assert first[1].intersection_fraction == 0.5


def test_cell_timeseries_uses_existing_arrays_and_preserves_invalid_observation() -> None:
    first = _observation("first", 1, np.array([[0.2, 0.3], [0.4, 0.5]]))
    second = _observation(
        "second",
        8,
        np.array([[0.3, 0.4], [0.5, 0.6]]),
        valid_mask=np.array([[False, True], [True, True]]),
    )
    series, audit = build_cell_timeseries(
        row=0,
        column=0,
        daily_records=_daily([first, second]),
        observations_by_id={first.item_id: first, second.item_id: second},
        reference=first,
    )

    assert [row["ndvi_mean"] for row in series] == [0.2]
    assert audit[1]["valid"] is False
    assert audit[1]["rejection_reasons"] == ["QUALITY_MASK_REJECTED"]


def test_loaded_scene_is_aligned_once_to_reference_red_grid() -> None:
    reference = _observation("reference", 22, np.zeros((2, 2)))
    source = SpatialRasterObservation(
        item_id="shifted",
        datetime="2026-08-15T00:00:00+00:00",
        ndvi=np.array([[9.0, 0.1, 0.2], [9.0, 0.3, 0.4]]),
        valid_mask=np.ones((2, 3), dtype=bool),
        inside_aoi_mask=np.ones((2, 3), dtype=bool),
        transform=Affine(10, 0, -10, 0, -10, 20),
        aoi_geometry=dict(box(-10, 0, 20, 20).__geo_interface__),
        crs="EPSG:3857",
        crs_is_projected=True,
        scene_quality_score=95.0,
        quality_status="high",
    )

    aligned = align_observations_to_reference([source], reference)[0]

    assert aligned.ndvi.shape == reference.ndvi.shape
    assert np.allclose(aligned.ndvi, [[0.1, 0.2], [0.3, 0.4]])
    assert tuple(aligned.transform) == tuple(reference.transform)


def test_cell_without_minimum_observations_is_inconclusive() -> None:
    recommendation, _, _ = classify_cell_timeseries(
        [
            {
                "item_id": "one",
                "datetime": "2026-08-22T00:00:00+00:00",
                "ndvi_mean": 0.5,
                "ndvi_median": 0.5,
                "valid_pixel_percentage": 100.0,
                "aoi_coverage_percentage": 100.0,
                "scene_quality_score": 95.0,
                "quality_status": "high",
                "accepted_for_timeseries": True,
            }
        ],
        config=_config(),
    )

    assert recommendation["recommendation"] == "inconclusivo"
    assert "insufficient_observations" in recommendation["blocking_reasons"]


def test_local_classification_reuses_central_recommendation_function() -> None:
    records = [
        {
            "item_id": f"scene-{day}",
            "datetime": f"2026-08-{day:02d}T00:00:00+00:00",
            "ndvi_mean": value,
            "ndvi_median": value,
            "valid_pixel_percentage": 100.0,
            "aoi_coverage_percentage": 100.0,
            "partial_raster_coverage": False,
            "scene_quality_score": 95.0,
            "quality_status": "high",
            "accepted_for_timeseries": True,
        }
        for day, value in ((1, 0.2), (8, 0.3), (15, 0.4), (22, 0.5))
    ]
    config = _config()
    local, _, analyzed = classify_cell_timeseries(records, config=config)
    central = recommend_cut(
        RecommendationInput(
            observations=analyzed,
            thresholds=_thresholds(config),
            reference_date=config.end_date,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        )
    ).to_dict()

    assert local == central


def test_four_neighbor_grouping_does_not_join_diagonal_cells() -> None:
    cells = [
        {"row": 0, "column": 0, "recommendation": "cortar"},
        {"row": 0, "column": 1, "recommendation": "nao_cortar"},
        {"row": 1, "column": 0, "recommendation": "nao_cortar"},
        {"row": 1, "column": 1, "recommendation": "cortar"},
    ]

    groups = group_cells_4_neighbor(cells)

    cortar_groups = [group for group in groups if group[0]["recommendation"] == "cortar"]
    assert len(cortar_groups) == 2
    assert all(len(group) == 1 for group in cortar_groups)


def test_zone_union_stays_inside_aoi_and_area_sum_is_deterministic() -> None:
    observations = [
        _observation(f"scene-{day}", day, np.full((2, 2), value))
        for day, value in ((1, 0.2), (8, 0.3), (15, 0.4), (22, 0.5))
    ]
    ticks = iter([10.0, 10.25, 10.0, 10.25])
    kwargs = {
        "observations": observations,
        "daily_records": _daily(observations),
        "config": _config(),
        "selected_area_m2": 400.0,
        "effective_analysis_area_m2": 400.0,
        "clock": lambda: next(ticks),
    }
    first = run_spatial_segmentation(**kwargs)
    second = run_spatial_segmentation(**kwargs)

    assert first == second
    assert first["performance"]["number_of_cells"] == 4
    assert sum(zone["area_m2"] for zone in first["zones"]) == 400.0
    assert first["segmented_area_m2"] == 400.0
    assert first["unclassified_area_m2"] == 0.0
    aoi_wgs84 = shape(transform_geom("EPSG:3857", "EPSG:4326", AOI.__geo_interface__))
    for zone in first["zones"]:
        assert shape(zone["geometry"]).difference(aoi_wgs84).area < 1e-15


def _scene(day: int) -> Scene:
    timestamp = datetime(2026, 8, day, tzinfo=timezone.utc)
    return Scene(
        SimpleNamespace(
            id=f"scene-{day}",
            datetime=timestamp,
            properties={
                "datetime": timestamp.isoformat(),
                "eo:cloud_cover": 1.0,
                "platform": "sentinel-2",
                "s2:mgrs_tile": "23KLP",
            },
            assets={},
        )
    )


def _fake_outputs(run_directory, *args, **kwargs):
    names = {
        "scenes": "scenes.csv",
        "timeseries": "timeseries.csv",
        "raw_timeseries": "raw.csv",
        "summary": "summary.json",
        "quality_report": "quality.json",
        "plot": "plot.png",
        "aoi": "aoi.geojson",
        "recommendation_json": "recommendation.json",
        "recommendation_csv": "recommendation.csv",
    }
    return {key: Path(run_directory) / value for key, value in names.items()}


def _service_run(
    tmp_path: Path,
    enabled: bool,
    segment_spatial,
    *,
    regularization_enabled: bool = False,
    regularize_spatial=lambda raw: raw,
):
    scenes = [_scene(day) for day in (1, 8, 15, 22)]

    def fake_raster(item, geometry):
        value = 0.4 + int(item.id.split("-")[-1]) / 100.0
        return RasterSceneData(
            red=np.full((2, 2), 0.2),
            nir=np.full((2, 2), value),
            valid_mask=np.ones((2, 2), dtype=bool),
            total_pixel_count=4,
            aoi_coverage_percentage=100.0,
            partial_raster_coverage=False,
            red_asset="B04",
            nir_asset="B08",
            scl_asset="SCL",
            scl_class_percentages={"vegetation": 100.0},
            quality_messages=[],
            inside_aoi_mask=np.ones((2, 2), dtype=bool),
            spatial_transform=TRANSFORM,
            spatial_aoi_geometry=dict(AOI.__geo_interface__),
            spatial_crs="EPSG:3857",
            spatial_crs_is_projected=True,
        )

    return run_monitoring_analysis(
        _config(
            tmp_path,
            spatial_segmentation_enabled=enabled,
            spatial_regularization_enabled=regularization_enabled,
        ),
        dependencies=PipelineDependencies(
            search_scenes=lambda *_: SceneSearchResult(
                scenes=scenes, discarded_scenes=[], total_matches=4
            ),
            read_scene_bands=fake_raster,
            segment_spatial=segment_spatial,
            regularize_spatial=regularize_spatial,
            write_outputs=_fake_outputs,
        ),
    )


def test_feature_flag_off_preserves_output_and_does_not_call_segmentation(tmp_path) -> None:
    calls = 0

    def forbidden(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("segmentacao nao deveria executar")

    result = _service_run(tmp_path, False, forbidden)

    assert calls == 0
    assert result.spatial_segmentation is None
    assert "spatial_segmentation" not in result.to_dict()
    assert "spatial_segmentation_enabled" not in _config().to_dict()


def test_segmentation_error_is_fail_soft_and_keeps_global_recommendation(tmp_path) -> None:
    baseline = _service_run(tmp_path / "off", False, lambda **_: {})
    failed = _service_run(
        tmp_path / "on",
        True,
        lambda **_: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    assert failed.status == baseline.status
    assert failed.recommendation == baseline.recommendation
    assert failed.spatial_segmentation["status"] == "unavailable"
    assert failed.spatial_segmentation["official_recommendation_changed"] is False
    assert any(
        warning["code"] == "SPATIAL_SEGMENTATION_UNAVAILABLE"
        for warning in failed.warnings
    )


def test_v2_feature_flag_preserves_v1_and_global_recommendation(tmp_path) -> None:
    raw = {
        "status": "experimental",
        "cells": [{"cell_id": "raw-cell"}],
        "zones": [],
    }
    v1 = _service_run(tmp_path / "v1", True, lambda **_: raw)
    v2 = _service_run(
        tmp_path / "v2",
        True,
        lambda **_: raw,
        regularization_enabled=True,
        regularize_spatial=lambda received: {
            "status": "experimental",
            "mode": "shadow",
            "raw_segmentation": received,
            "operational_segmentation": {"zones": []},
            "regularization": {"recommended_mmu": 2},
        },
    )

    assert v2.recommendation == v1.recommendation
    assert v1.spatial_segmentation == raw
    assert v2.spatial_segmentation["raw_segmentation"] == raw
    assert v2.spatial_segmentation["official_recommendation_changed"] is False


def test_regularization_failure_is_fail_soft_and_preserves_raw(tmp_path) -> None:
    raw = {"status": "experimental", "cells": [], "zones": []}
    baseline = _service_run(tmp_path / "baseline", True, lambda **_: raw)
    failed = _service_run(
        tmp_path / "failed",
        True,
        lambda **_: raw,
        regularization_enabled=True,
        regularize_spatial=lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert failed.recommendation == baseline.recommendation
    assert failed.spatial_segmentation["raw_segmentation"] == raw
    assert failed.spatial_segmentation["operational_segmentation"]["status"] == "unavailable"
    assert any(
        warning["code"] == "SPATIAL_REGULARIZATION_UNAVAILABLE"
        for warning in failed.warnings
    )
