"""Integracao do servico com dependencias deterministicas e sem rede."""

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.raster_processing import RasterSceneData
from src.satellite_monitoring.service import PipelineDependencies, run_monitoring_analysis
from src.satellite_monitoring.stac_client import Scene, SceneSearchResult


def _scene(day: int, *, item_id: str | None = None, hour: int = 10) -> Scene:
    observed_at = datetime(2026, 7, day, hour, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=item_id or f"scene-{day:02d}",
        datetime=observed_at,
        properties={
            "datetime": observed_at.isoformat(),
            "eo:cloud_cover": float(day),
            "platform": "sentinel-2b",
            "s2:mgrs_tile": "23KLP",
        },
        assets={},
    )
    return Scene(item)


def test_service_processes_candidates_before_final_temporal_limit(tmp_path) -> None:
    scenes = [_scene(day) for day in range(1, 15)] + [
        _scene(14, item_id="scene-15", hour=11)
    ]
    captured: dict = {}

    def fake_search(config, geometry):
        return SceneSearchResult(scenes=scenes, discarded_scenes=[], total_matches=15)

    def fake_raster(item, geometry):
        valid_mask = np.ones((10, 10), dtype=bool)
        if item.id == "scene-15":
            valid_mask[:, 2:] = False
        return RasterSceneData(
            red=np.full((10, 10), 0.2, dtype=np.float32),
            nir=np.full((10, 10), 0.5 + int(item.id[-2:]) / 1000, dtype=np.float32),
            valid_mask=valid_mask,
            total_pixel_count=100,
            aoi_coverage_percentage=100.0,
            partial_raster_coverage=False,
            red_asset="B04",
            nir_asset="B08",
            scl_asset="SCL",
            scl_class_percentages={"vegetation": 100.0},
            quality_messages=[],
        )

    def fake_write(run_directory, scenes_arg, timeseries_arg, summary, aoi, recommendation, **kwargs):
        captured.update(
            scenes=scenes_arg,
            timeseries=timeseries_arg,
            summary=summary,
            raw=kwargs["raw_daily_records"],
            quality_report=kwargs["quality_report"],
        )
        names = {
            "scenes": "scenes.csv",
            "timeseries": "ndvi_timeseries.csv",
            "raw_timeseries": "raw_daily_timeseries.csv",
            "summary": "summary.json",
            "quality_report": "quality_report.json",
            "plot": "ndvi_timeseries.png",
            "aoi": "aoi.geojson",
            "recommendation_json": "cut_recommendation.json",
            "recommendation_csv": "cut_recommendation.csv",
        }
        return {key: Path(run_directory) / value for key, value in names.items()}

    config = MonitoringConfig(
        geometry={
            "type": "Polygon",
            "coordinates": [[[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]],
        },
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        max_cloud_cover=30,
        max_scenes=12,
        max_candidate_scenes=40,
        min_valid_pixel_percentage=70,
        min_valid_pixel_count=30,
        min_aoi_coverage_percentage=95,
        output_root=tmp_path,
    )
    result = run_monitoring_analysis(
        config,
        dependencies=PipelineDependencies(
            search_scenes=fake_search,
            read_scene_bands=fake_raster,
            write_outputs=fake_write,
        ),
    )

    assert result.summary["candidate_scene_count"] == 15
    assert result.summary["scene_count_processed"] == 15
    assert result.summary["accepted_scene_count"] == 14
    assert result.summary["rejected_scene_count"] == 1
    assert result.summary["scene_count_selected"] == 12
    assert result.summary["scene_count_not_selected_after_quality"] == 2
    assert len(captured["raw"]) == 12
    assert captured["quality_report"]["configuration"]["max_candidate_scenes"] == 40
    day_14 = next(day for day in result.summary["daily_aggregation"]["days"] if day["date"] == "2026-07-14")
    assert day_14["all_candidate_item_ids"] == ["scene-14", "scene-15"]
    rejected_candidate = next(
        candidate for candidate in day_14["candidate_ranking_inputs"]
        if candidate["item_id"] == "scene-15"
    )
    assert rejected_candidate["accepted"] is False
    rejected = next(record for record in captured["scenes"] if record["item_id"] == "scene-15")
    assert rejected["accepted_for_timeseries"] is False
    assert "insufficient_valid_pixels" in rejected["quality_reasons"]
