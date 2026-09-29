"""Integracao do servico com dependencias deterministicas e sem rede."""

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from affine import Affine

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.raster_processing import RasterSceneData
from src.satellite_monitoring.service import PipelineDependencies, run_monitoring_analysis
from src.satellite_monitoring.stac_client import Scene, SceneSearchResult


def _stable_baseline(result) -> dict:
    metrics = result.recommendation["metrics"]
    analysis_quality = result.summary["analysis_quality"]
    return {
        "status": result.status,
        "decision": result.recommendation["recommendation"],
        "confidence": result.recommendation["confidence"],
        "reasons": result.recommendation["reasons"],
        "blocking_reasons": result.recommendation["blocking_reasons"],
        "daily_observation_count": result.summary["daily_observation_count"],
        "analysis_quality": {
            "status": analysis_quality["status"],
            "score": round(analysis_quality["score"], 3),
        },
        "metrics": {
            "observation_count": metrics["observation_count"],
            "current_ndvi_mean": round(metrics["current_ndvi_mean"], 6),
            "historical_median": round(metrics["historical_median"], 6),
            "current_percentile": round(metrics["current_percentile"], 3),
            "recent_trend": round(metrics["recent_trend"], 6),
        },
        "timeseries": [
            (record["datetime"][:10], round(record["ndvi_mean"], 6))
            for record in result.timeseries
        ],
    }


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


def test_sentinel_service_baseline_processes_candidates_before_final_temporal_limit(
    tmp_path,
) -> None:
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
    assert _stable_baseline(result) == {
        "status": "completed",
        "decision": "cortar",
        "confidence": "high",
        "reasons": [
            "current_percentile_at_or_above_high_threshold",
            "positive_or_stable_high_recent_trend",
            "no_recent_confirmed_significant_drop",
        ],
        "blocking_reasons": [],
        "daily_observation_count": 12,
        "analysis_quality": {"status": "high", "score": 99.2},
        "metrics": {
            "observation_count": 12,
            "current_ndvi_mean": 0.439776,
            "historical_median": 0.435427,
            "current_percentile": 95.833,
            "recent_trend": 0.000787,
        },
        "timeseries": [
            ("2026-07-03", 0.43101),
            ("2026-07-04", 0.431818),
            ("2026-07-05", 0.432624),
            ("2026-07-06", 0.433428),
            ("2026-07-07", 0.434229),
            ("2026-07-08", 0.435028),
            ("2026-07-09", 0.435825),
            ("2026-07-10", 0.43662),
            ("2026-07-11", 0.437412),
            ("2026-07-12", 0.438202),
            ("2026-07-13", 0.43899),
            ("2026-07-14", 0.439776),
        ],
    }


def test_height_estimator_does_not_change_recommendation(tmp_path) -> None:
    scenes = [_scene(day) for day in (1, 8, 15, 22)]
    extraction_calls = 0

    def fake_search(config, geometry):
        return SceneSearchResult(scenes=scenes, discarded_scenes=[], total_matches=4)

    def fake_raster(item, geometry):
        return RasterSceneData(
            red=np.full((10, 10), 0.2, dtype=np.float32),
            nir=np.full((10, 10), 0.5, dtype=np.float32),
            valid_mask=np.ones((10, 10), dtype=bool),
            total_pixel_count=100,
            aoi_coverage_percentage=100.0,
            partial_raster_coverage=False,
            red_asset="B04",
            nir_asset="B08",
            scl_asset="SCL",
            scl_class_percentages={"vegetation": 100.0},
            quality_messages=[],
        )

    def extract_features(item, raster):
        nonlocal extraction_calls
        extraction_calls += 1
        return {
            "red_median_reflectance": 0.12,
            "nir_median_reflectance": 0.31,
            "ndvi_median": 0.44,
            "vegetation_fraction": 0.8,
            "height_valid_pixel_count": 80,
            "height_total_pixel_count": 100,
            "mixed_pixel_risk": "low",
            "height_purity_gate_passed": True,
            "height_purity_gate_reasons": [],
            "height_mask_configuration": {"min_ndvi": 0.15},
            "reflectance_scale_source": "test",
            "reflectance_scale": 0.0001,
            "reflectance_offset": -0.1,
        }

    def estimator(features):
        return {
            "status": "experimental",
            "estimated_class": "le_30_cm",
            "probability_gt_30_cm": 0.2,
            "confidence": "medium",
            "reference_threshold_cm": 30,
            "model_version": "height-estimator-v0",
        }

    def fake_write(run_directory, *args, **kwargs):
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

    def run(enabled: bool, directory: str):
        config = MonitoringConfig(
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]
                ],
            },
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            max_cloud_cover=30,
            min_valid_pixel_count=30,
            min_observations=4,
            output_root=tmp_path / directory,
            height_estimation_enabled=enabled,
        )
        return run_monitoring_analysis(
            config,
            dependencies=PipelineDependencies(
                search_scenes=fake_search,
                read_scene_bands=fake_raster,
                extract_height_features=extract_features,
                estimate_height=estimator,
                write_outputs=fake_write,
            ),
        )

    disabled = run(False, "disabled")
    assert extraction_calls == 0
    enabled = run(True, "enabled")

    assert disabled.recommendation == enabled.recommendation
    assert disabled.height_estimation["status"] == "disabled"
    assert enabled.height_estimation["status"] == "experimental"
    assert extraction_calls == 4


def test_spatial_accounting_does_not_change_recommendation(tmp_path) -> None:
    scenes = [_scene(day) for day in (1, 8, 15, 22)]

    def fake_search(config, geometry):
        return SceneSearchResult(scenes=scenes, discarded_scenes=[], total_matches=4)

    def fake_write(run_directory, *args, **kwargs):
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

    def run(with_spatial_metadata: bool, directory: str):
        def fake_raster(item, geometry):
            optional = (
                {
                    "spatial_transform": Affine(10, 0, 0, 0, 10, 0),
                    "spatial_aoi_geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[5, 0], [15, 0], [15, 10], [5, 10], [5, 0]]
                        ],
                    },
                    "spatial_crs": "EPSG:32723",
                    "spatial_crs_is_projected": True,
                }
                if with_spatial_metadata
                else {}
            )
            return RasterSceneData(
                red=np.full((1, 2), 0.2, dtype=np.float32),
                nir=np.full((1, 2), 0.5, dtype=np.float32),
                valid_mask=np.ones((1, 2), dtype=bool),
                total_pixel_count=2,
                aoi_coverage_percentage=100.0,
                partial_raster_coverage=False,
                red_asset="B04",
                nir_asset="B08",
                scl_asset="SCL",
                scl_class_percentages={"vegetation": 100.0},
                quality_messages=[],
                **optional,
            )

        config = MonitoringConfig(
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]
                ],
            },
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            min_valid_pixel_count=1,
            min_observations=4,
            output_root=tmp_path / directory,
        )
        return run_monitoring_analysis(
            config,
            dependencies=PipelineDependencies(
                search_scenes=fake_search,
                read_scene_bands=fake_raster,
                write_outputs=fake_write,
            ),
        )

    before = run(False, "without-accounting")
    after = run(True, "with-accounting")

    assert before.recommendation == after.recommendation
    assert before.effective_analysis_area_m2 is None
    assert after.effective_analysis_area_m2 == 100.0
    assert after.effective_analysis_pct == (
        after.effective_analysis_area_m2 / after.selected_area_m2 * 100.0
    )
    assert not any(
        warning["code"] == "EFFECTIVE_AREA_UNAVAILABLE"
        for warning in after.warnings
    )
    assert after.summary["spatial_accounting"]["metric_weighting_changed"] is False


def test_multisource_shadow_is_additive_and_preserves_recommendation(tmp_path) -> None:
    scenes = [_scene(day) for day in (1, 8, 15, 22)]
    collection_calls = 0

    def fake_raster(item, geometry):
        return RasterSceneData(
            red=np.full((10, 10), 0.2, dtype=np.float32),
            nir=np.full((10, 10), 0.5, dtype=np.float32),
            valid_mask=np.ones((10, 10), dtype=bool),
            total_pixel_count=100,
            aoi_coverage_percentage=100.0,
            partial_raster_coverage=False,
            red_asset="B04",
            nir_asset="B08",
            scl_asset="SCL",
            scl_class_percentages={"vegetation": 100.0},
            quality_messages=[],
        )

    def fake_write(run_directory, *args, **kwargs):
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

    def collect(config, geometry):
        nonlocal collection_calls
        collection_calls += 1
        assert config.datetime_range == "2026-07-01/2026-07-31"
        assert geometry["type"] == "Polygon"
        return {
            "enabled": True,
            "fusion_mode": config.multisource_fusion_mode,
            "official_recommendation_changed": False,
            "generated_at": "2026-08-01T00:00:00+00:00",
            "configuration": {
                "sentinel1_enabled": True,
                "sentinel1_collection": "sentinel-1-grd",
                "sentinel1_max_scenes": 8,
                "analysis_period": config.datetime_range,
            },
            "sources": [],
        }

    def run(
        enabled: bool,
        directory: str,
        collector=collect,
        fusion_mode: str | None = None,
        authorizer=lambda _config: {
            "requested": False,
            "authorized": False,
            "authorization_status": "not_requested",
            "authorization_reason": "operational_fusion_not_requested",
            "holdout_schema_version": None,
            "holdout_gate_status": None,
            "candidate_rule": "B",
        },
    ):
        config = MonitoringConfig(
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]
                ],
            },
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            min_valid_pixel_count=30,
            min_observations=4,
            output_root=tmp_path / directory,
            multisource_enabled=enabled,
            sentinel1_enabled=enabled,
            multisource_fusion_mode=(
                fusion_mode or ("shadow" if enabled else "disabled")
            ),
        )
        return run_monitoring_analysis(
            config,
            dependencies=PipelineDependencies(
                search_scenes=lambda *_: SceneSearchResult(
                    scenes=scenes, discarded_scenes=[], total_matches=4
                ),
                read_scene_bands=fake_raster,
                collect_multisource=collector,
                authorize_operational_fusion=authorizer,
                write_outputs=fake_write,
            ),
        )

    disabled = run(False, "disabled")
    enabled = run(True, "enabled")
    failed = run(
        True,
        "failed",
        collector=lambda *_: (_ for _ in ()).throw(RuntimeError("provider boom")),
    )

    assert collection_calls == 1
    assert disabled.recommendation == enabled.recommendation
    assert disabled.multisource is None
    assert "multisource" not in disabled.to_dict()
    assert enabled.multisource["official_recommendation_changed"] is False
    assert enabled.multisource["review"]["review_evaluable"] is False
    assert enabled.multisource["review"]["review_not_evaluable_reason"] == (
        "sentinel1_evidence_missing"
    )
    authorization_failed = run(
        True,
        "authorization-failed",
        authorizer=lambda _config: (_ for _ in ()).throw(RuntimeError("gate boom")),
    )
    experimental_failed = run(
        True,
        "experimental-failed",
        collector=lambda *_: (_ for _ in ()).throw(RuntimeError("provider boom")),
        fusion_mode="experimental",
    )
    assert enabled.summary["multisource"] == enabled.multisource
    artifact = enabled.artifacts["multisource_evidence"]
    assert artifact.name == "multisource_evidence.json"
    assert artifact.is_file()
    assert failed.status != "failed"
    assert failed.recommendation == disabled.recommendation
    assert failed.multisource["sources"][0]["status"] == "error"
    assert failed.multisource["review"]["review_evaluable"] is False
    assert failed.multisource["review"]["review_recommended"] is False
    assert failed.multisource["review"]["review_not_evaluable_reason"] == (
        "sentinel1_error"
    )
    assert failed.multisource["review"]["official_recommendation_changed"] is False
    assert authorization_failed.status != "failed"
    assert authorization_failed.recommendation == disabled.recommendation
    assert authorization_failed.multisource["operational_fusion"]["authorized"] is False
    assert authorization_failed.multisource["operational_fusion"]["authorization_reason"] == (
        "authorization_layer_error"
    )
    assert authorization_failed.multisource["operational_fusion"][
        "official_recommendation_changed"
    ] is False
    assert experimental_failed.status != "failed"
    assert experimental_failed.recommendation == disabled.recommendation
    experimental_audit = experimental_failed.multisource["experimental_fusion"]
    assert experimental_audit["multisource_recommendation"] == (
        disabled.recommendation["recommendation"]
    )
    assert experimental_audit["sentinel1_influenced_decision"] is False
    assert experimental_audit["fusion_not_evaluable_reason"] == "sentinel1_error"
    assert experimental_audit["operationally_authorized"] is False
    assert any(
        warning["code"] == "MULTISOURCE_SOURCE_UNAVAILABLE"
        for warning in failed.warnings
    )


def test_height_estimator_exception_is_fail_soft(tmp_path) -> None:
    scene = _scene(1)

    def fake_raster(item, geometry):
        return RasterSceneData(
            red=np.full((10, 10), 0.2, dtype=np.float32),
            nir=np.full((10, 10), 0.5, dtype=np.float32),
            valid_mask=np.ones((10, 10), dtype=bool),
            total_pixel_count=100,
            aoi_coverage_percentage=100.0,
            partial_raster_coverage=False,
            red_asset="B04",
            nir_asset="B08",
            scl_asset="SCL",
            scl_class_percentages={"vegetation": 100.0},
            quality_messages=[],
        )

    config = MonitoringConfig(
        geometry={
            "type": "Polygon",
            "coordinates": [[[-47, -23], [-46.99, -23], [-46.99, -22.99], [-47, -23]]],
        },
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        min_observations=1,
        output_root=tmp_path,
        height_estimation_enabled=True,
    )
    result = run_monitoring_analysis(
        config,
        dependencies=PipelineDependencies(
            search_scenes=lambda *_: SceneSearchResult(
                scenes=[scene], discarded_scenes=[], total_matches=1
            ),
            read_scene_bands=fake_raster,
            extract_height_features=lambda *_: {
                "red_median_reflectance": 0.12,
                "nir_median_reflectance": 0.31,
            },
            estimate_height=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
            write_outputs=lambda run_directory, *_, **__: {
                key: Path(run_directory) / name
                for key, name in {
                    "scenes": "scenes.csv",
                    "timeseries": "timeseries.csv",
                    "raw_timeseries": "raw.csv",
                    "summary": "summary.json",
                    "quality_report": "quality.json",
                    "plot": "plot.png",
                    "aoi": "aoi.geojson",
                    "recommendation_json": "recommendation.json",
                    "recommendation_csv": "recommendation.csv",
                }.items()
            },
        ),
    )

    assert result.status != "failed"
    assert result.height_estimation["status"] == "unavailable"
    assert result.recommendation["recommendation"] in {
        "cortar",
        "nao_cortar",
        "inconclusivo",
    }
