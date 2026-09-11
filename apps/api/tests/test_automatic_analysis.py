from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Barrier, Thread

import pytest
from pyproj import Transformer
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform

from apps.api.app.automatic_analysis import (
    AutomaticAnalysisCoordinator,
    AutomaticAnalysisPolicy,
    AutomaticAnalysisRepository,
    AutomaticPipelineFailure,
    canonicalize_viewport,
)
from apps.api.app.dependencies import (
    get_analysis_service,
    get_automatic_analysis_coordinator,
)
from apps.api.app.main import app
from apps.api.tests.conftest import make_result
from src.satellite_monitoring.multisource.experimental_fusion import (
    attach_experimental_fusion,
)
from src.satellite_monitoring.database import get_analysis, session_scope
from src.satellite_monitoring.road_geometry import LocalGeoJsonRoadGeometryProvider


BOUNDS = {"west": -46.9625, "south": -23.1095, "east": -46.9595, "north": -23.1065}
CENTER = {"lng": -46.9610, "lat": -23.1080}


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 10, 15, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class InlineExecutor:
    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


class HoldingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        self.calls.append((function, args))
        return Future()


def _policy(**changes):
    values = {
        "enabled": True,
        "min_zoom": 14,
        "max_zoom": 22,
        "canonical_tile_zoom": 17,
        "max_area_km2": 1000.0,
        "min_dimension_meters": 20,
        "max_dimension_meters": 50000,
        "cache_ttl_seconds": 60,
        "in_progress_ttl_seconds": 1800,
        "failure_ttl_seconds": 30,
        "force_refresh_cooldown_seconds": 10,
        "max_concurrent": 2,
    }
    values.update(changes)
    return AutomaticAnalysisPolicy(**values)


def _coordinator(
    tmp_path,
    *,
    executor=None,
    clock=None,
    road_geometry_provider=None,
    **policy_changes,
):
    return AutomaticAnalysisCoordinator(
        AutomaticAnalysisRepository(tmp_path / "automatic.sqlite3"),
        _policy(**policy_changes),
        executor=executor or InlineExecutor(),
        clock=clock or MutableClock(),
        road_geometry_provider=road_geometry_provider,
    )


def _road_provider(tmp_path: Path) -> tuple[LocalGeoJsonRoadGeometryProvider, dict]:
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32723", always_xy=True)
    reverse = Transformer.from_crs("EPSG:32723", "EPSG:4326", always_xy=True)
    origin_x, origin_y = forward.transform(-47.0, -23.0)
    metric_line = LineString(
        [(origin_x - 500, origin_y), (origin_x + 500, origin_y)]
    )
    line = transform(reverse.transform, metric_line)
    center_lng, center_lat = reverse.transform(origin_x, origin_y + 8)
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "feature_id": "test:SP-330:axis-1",
                    "road_id": "SP-330",
                    "road_ref": "SP-330",
                    "road_name": "Rodovia Anhanguera",
                    "concession_id": "autoban",
                    "geometry_status": "valid",
                    "source": "TEST",
                },
                "geometry": mapping(line),
            }
        ],
    }
    path = tmp_path / "roads.geojson"
    path.write_text(json.dumps(document), encoding="utf-8")
    return LocalGeoJsonRoadGeometryProvider(path), {
        "lng": center_lng,
        "lat": center_lat,
    }


def _request(coordinator, execute=lambda geometry, analysis_id: {"analysis_id": analysis_id}, **changes):
    values = {
        "bounds": BOUNDS,
        "center": CENTER,
        "zoom": 17,
        "force_refresh": False,
        "analysis_period": "2026-07-01/2026-07-31",
        "execute": execute,
    }
    values.update(changes)
    return coordinator.request(**values)


def test_feature_disabled_never_starts_analysis(tmp_path):
    calls = []
    coordinator = _coordinator(tmp_path, enabled=False)
    response = _request(coordinator, execute=lambda *args: calls.append(args))
    assert response["status"] == "disabled"
    assert response["analysis_started"] is False
    assert calls == []


def test_low_zoom_and_large_viewport_require_more_zoom(tmp_path):
    coordinator = _coordinator(tmp_path)
    low = _request(coordinator, zoom=13)
    large = _request(
        coordinator,
        bounds={"west": -47.5, "south": -23.5, "east": -46.5, "north": -22.5},
        center={"lng": -47.0, "lat": -23.0},
    )
    assert low["status"] == "zoom_required"
    assert low["reason"] == "zoom_below_minimum"
    assert large["status"] == "zoom_required"
    assert large["reason"] in {"viewport_too_large", "viewport_dimensions_too_large"}


def test_viewport_over_area_limit_is_rejected_even_with_valid_dimensions(tmp_path):
    response = _request(
        _coordinator(tmp_path, max_area_km2=0.5),
        bounds={"west": -46.965, "south": -23.112, "east": -46.957, "north": -23.104},
        center={"lng": -46.961, "lat": -23.108},
    )
    assert response["status"] == "zoom_required"
    assert response["reason"] == "viewport_too_large"


def test_zoom_14_is_accepted_and_canonicalizes_to_z17_tile(tmp_path):
    coordinator = _coordinator(tmp_path)
    response = _request(coordinator, zoom=14)
    assert response["status"] == "analysis_started"
    assert response["spatial_key"].startswith("tile:17/")
    assert response["canonical_bounds"] is not None


def test_roadside_zoom_13_wide_viewport_sends_fixed_aoi_to_pipeline(tmp_path):
    provider, center = _road_provider(tmp_path)
    calls = []
    coordinator = _coordinator(
        tmp_path,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )
    response = _request(
        coordinator,
        zoom=13,
        bounds={"west": -48.0, "south": -24.0, "east": -46.0, "north": -22.0},
        center=center,
        execute=lambda *args: calls.append(args),
    )

    assert response["status"] == "analysis_started"
    assert response["analysis_started"] is True
    assert response["spatial_key"].startswith("roadside:v1:")
    assert response["road"]["id"] == "SP-330"
    assert response["canonical_segment_geometry"]["type"] == "LineString"
    assert response["analyzed_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert len(calls) == 1
    assert calls[0][0] == response["analyzed_geometry"]
    assert response["roadside_metrics"]["area_m2"] == pytest.approx(24_000, rel=0.01)
    assert response["road"]["section_length_m"] == pytest.approx(300, abs=0.1)


def test_roadside_below_zoom_13_requires_context_without_calling_pipeline(tmp_path):
    provider, center = _road_provider(tmp_path)
    calls = []
    response = _request(
        _coordinator(
            tmp_path,
            spatial_strategy="roadside_v1",
            road_geometry_provider=provider,
        ),
        zoom=12.9,
        center=center,
        execute=lambda *args: calls.append(args),
    )
    assert response["status"] == "road_context_required"
    assert response["reason"] == "zoom_below_roadside_minimum"
    assert calls == []


def test_roadside_missing_dataset_fails_closed_without_sentinel(tmp_path):
    calls = []
    response = _request(
        _coordinator(
            tmp_path,
            spatial_strategy="roadside_v1",
            road_geometry_provider=LocalGeoJsonRoadGeometryProvider(
                tmp_path / "missing.geojson"
            ),
        ),
        zoom=13,
        execute=lambda *args: calls.append(args),
    )
    assert response["status"] == "road_geometry_unavailable"
    assert response["analysis_started"] is False
    assert calls == []


def test_tile_and_roadside_spatial_keys_cannot_collide(tmp_path):
    provider, center = _road_provider(tmp_path)
    tile = _request(_coordinator(tmp_path / "tile"))
    roadside = _request(
        _coordinator(
            tmp_path / "roadside",
            spatial_strategy="roadside_v1",
            road_geometry_provider=provider,
        ),
        zoom=13,
        center=center,
        bounds={"west": -48.0, "south": -24.0, "east": -46.0, "north": -22.0},
    )
    assert tile["spatial_key"].startswith("tile:17/")
    assert roadside["spatial_key"].startswith("roadside:v1:")
    assert tile["spatial_key"] != roadside["spatial_key"]


def test_roadside_cache_and_deduplication_use_same_canonical_aoi(tmp_path):
    provider, center = _road_provider(tmp_path)
    calls = []
    coordinator = _coordinator(
        tmp_path,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )
    request = {
        "zoom": 13,
        "center": center,
        "bounds": {"west": -48.0, "south": -24.0, "east": -46.0, "north": -22.0},
        "execute": lambda geometry, analysis_id: calls.append((geometry, analysis_id))
        or {"analysis_id": analysis_id},
    }
    first = _request(coordinator, **request)
    second = _request(coordinator, **request)

    assert first["status"] == "analysis_started"
    assert second["status"] == "cache_hit"
    assert second["spatial_key"] == first["spatial_key"]
    assert second["analyzed_geometry"] == first["analyzed_geometry"]
    assert len(calls) == 1

    holding = HoldingExecutor()
    pending_coordinator = _coordinator(
        tmp_path / "pending",
        executor=holding,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )
    pending = _request(pending_coordinator, **{**request, "execute": lambda *_: {}})
    duplicate = _request(pending_coordinator, **{**request, "execute": lambda *_: {}})
    assert pending["status"] == "analysis_started"
    assert duplicate["status"] == "in_progress"
    assert len(holding.calls) == 1


@pytest.mark.parametrize(
    ("bounds", "center", "reason"),
    [
        ({"west": 1, "south": 0, "east": 0, "north": 1}, {"lng": 0.5, "lat": 0.5}, "viewport_longitude_bounds_invalid"),
        (BOUNDS, {"lng": -40, "lat": -23.108}, "viewport_center_outside_bounds"),
        ({"west": -1, "south": -90, "east": 1, "north": -89}, {"lng": 0, "lat": -89.5}, "viewport_latitude_bounds_invalid"),
    ],
)
def test_invalid_viewports_are_controlled(tmp_path, bounds, center, reason):
    response = _request(_coordinator(tmp_path), bounds=bounds, center=center)
    assert response["status"] == "invalid_viewport"
    assert response["reason"] == reason


def test_new_area_starts_then_same_area_is_persistent_cache_hit(tmp_path):
    calls = []
    coordinator = _coordinator(tmp_path)

    def execute(geometry, analysis_id):
        calls.append((geometry, analysis_id))
        return {"analysis_id": analysis_id, "status": "completed"}

    first = _request(coordinator, execute=execute)
    second = _request(coordinator, execute=execute)
    assert first["status"] == "analysis_started"
    assert second["status"] == "cache_hit"
    assert second["cache_hit"] is True
    assert second["analysis_id"] == first["analysis_id"]
    assert second["result"]["status"] == "completed"
    assert len(calls) == 1


def test_cache_survives_coordinator_recreation(tmp_path):
    clock = MutableClock()
    first = _coordinator(tmp_path, clock=clock)
    started = _request(first)
    recreated = _coordinator(tmp_path, clock=clock)
    cached = _request(recreated)
    assert cached["status"] == "cache_hit"
    assert cached["analysis_id"] == started["analysis_id"]


def test_in_progress_request_is_deduplicated(tmp_path):
    executor = HoldingExecutor()
    coordinator = _coordinator(tmp_path, executor=executor)
    first = _request(coordinator)
    second = _request(coordinator)
    assert first["status"] == "analysis_started"
    assert second["status"] == "in_progress"
    assert second["analysis_id"] == first["analysis_id"]
    assert len(executor.calls) == 1


def test_global_concurrency_limit_rejects_a_different_area(tmp_path):
    executor = HoldingExecutor()
    coordinator = _coordinator(tmp_path, executor=executor, max_concurrent=1)
    first = _request(coordinator)
    shifted_bounds = {
        key: value + 0.01 if key in {"west", "east"} else value
        for key, value in BOUNDS.items()
    }
    second = _request(
        coordinator,
        bounds=shifted_bounds,
        center={"lng": CENTER["lng"] + 0.01, "lat": CENTER["lat"]},
    )
    assert first["status"] == "analysis_started"
    assert second["status"] == "in_progress"
    assert second["reason"] == "maximum_concurrent_analyses_reached"
    assert len(executor.calls) == 1


def test_expired_in_progress_lease_can_be_reclaimed_with_auditable_log(tmp_path, caplog):
    clock = MutableClock()
    executor = HoldingExecutor()
    coordinator = _coordinator(tmp_path, executor=executor, clock=clock)
    first = _request(coordinator)
    clock.advance(1801)
    second = _request(coordinator)
    assert second["status"] == "analysis_started"
    assert second["analysis_id"] != first["analysis_id"]
    assert len(executor.calls) == 2
    record = next(
        record
        for record in caplog.records
        if record.message == "automatic_analysis_lease_expired"
    )
    assert record.analysis_id == first["analysis_id"]
    assert record.failure_category == "in_progress_lease_expired"
    assert record.failure_detail == "worker_did_not_finish_before_persisted_lease_expiry"


def test_small_pan_variations_share_key_and_different_area_does_not():
    policy = _policy()
    original = canonicalize_viewport(bounds=BOUNDS, center=CENTER, zoom=17, policy=policy)
    nearby = canonicalize_viewport(
        bounds=BOUNDS,
        center={"lng": CENTER["lng"] + 0.00005, "lat": CENTER["lat"] + 0.00005},
        zoom=18,
        policy=policy,
    )
    shifted_bounds = {key: value + 0.01 if key in {"west", "east"} else value for key, value in BOUNDS.items()}
    shifted = canonicalize_viewport(
        bounds=shifted_bounds,
        center={"lng": CENTER["lng"] + 0.01, "lat": CENTER["lat"]},
        zoom=17,
        policy=policy,
    )
    assert nearby.spatial_key == original.spatial_key
    assert shifted.spatial_key != original.spatial_key


def test_force_refresh_obeys_cooldown_then_starts_new_run(tmp_path):
    clock = MutableClock()
    coordinator = _coordinator(tmp_path, clock=clock)
    first = _request(coordinator)
    limited = _request(coordinator, force_refresh=True)
    clock.advance(11)
    refreshed = _request(coordinator, force_refresh=True)
    assert limited["status"] == "cache_hit"
    assert limited["reason"] == "force_refresh_cooldown_active"
    assert refreshed["status"] == "analysis_started"
    assert refreshed["analysis_id"] != first["analysis_id"]


def test_expired_cache_starts_a_new_analysis(tmp_path):
    clock = MutableClock()
    coordinator = _coordinator(tmp_path, clock=clock, cache_ttl_seconds=60)
    first = _request(coordinator)
    clock.advance(61)
    second = _request(coordinator)
    assert second["status"] == "analysis_started"
    assert second["analysis_id"] != first["analysis_id"]


def test_pipeline_failure_is_cached_without_raising(tmp_path):
    coordinator = _coordinator(tmp_path)

    def fail(*args):
        raise RuntimeError("sentinel provider failed")

    started = _request(coordinator, execute=fail)
    failed = _request(coordinator, execute=fail)
    assert started["status"] == "analysis_started"
    assert failed["status"] == "failed"
    assert failed["reason"] == "unexpected_runtime_failure"


def test_remote_pipeline_failure_is_classified_and_logged(tmp_path, caplog):
    coordinator = _coordinator(tmp_path)

    def fail(*args):
        raise AutomaticPipelineFailure(
            "remote_stac_failure", "SATELLITE_PROVIDER_ERROR"
        )

    with caplog.at_level("ERROR", logger="apps.api.app.automatic_analysis"):
        _request(coordinator, execute=fail)
    failed = _request(coordinator, execute=fail)

    assert failed["status"] == "failed"
    assert failed["reason"] == "remote_stac_failure"
    record = next(
        item for item in caplog.records if item.message == "automatic_analysis_failed"
    )
    assert record.failure_category == "remote_stac_failure"
    assert record.failure_exception_type == "AutomaticPipelineFailure"
    assert record.failure_detail == "SATELLITE_PROVIDER_ERROR"


def test_roadside_builder_exception_is_sanitized_as_gis_failure(
    tmp_path, monkeypatch, caplog
):
    provider, center = _road_provider(tmp_path)
    coordinator = _coordinator(
        tmp_path,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )

    def fail_build(*args, **kwargs):
        raise RuntimeError("invalid polygon details")

    monkeypatch.setattr(
        "apps.api.app.automatic_analysis.build_roadside_aoi", fail_build
    )
    with caplog.at_level("ERROR", logger="apps.api.app.automatic_analysis"):
        response = _request(
            coordinator,
            zoom=13,
            center=center,
            bounds={"west": -48.0, "south": -24.0, "east": -46.0, "north": -22.0},
        )

    assert response["status"] == "roadside_geometry_invalid"
    assert response["reason"] == "roadside_aoi_build_failed"
    record = next(
        item for item in caplog.records if item.message == "automatic_roadside_aoi_failed"
    )
    assert record.failure_category == "gis_failure"
    assert record.failure_exception_type == "RuntimeError"
    assert record.failure_detail == "invalid polygon details"


@pytest.mark.parametrize(
    ("source_error_code", "expected_category"),
    [
        ("SATELLITE_PROVIDER_ERROR", "remote_stac_failure"),
        ("PROCESSING_ERROR", "sentinel2_pipeline_failure"),
    ],
)
def test_automatic_endpoint_classifies_pipeline_failures(
    client, tmp_path, source_error_code, expected_category
):
    coordinator = _coordinator(tmp_path)

    def service(config, *, analysis_id, **kwargs):
        result = make_result(analysis_id)
        result.status = "failed"
        result.exit_code = 1
        result.errors = [{"code": source_error_code, "message": "internal detail"}]
        return result

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {"bounds": BOUNDS, "center": CENTER, "zoom": 17}

    started = client.post("/api/analyses/automatic", json=payload)
    cached_failure = client.post("/api/analyses/automatic", json=payload)

    assert started.status_code == 200
    assert started.json()["status"] == "analysis_started"
    assert cached_failure.status_code == 200
    assert cached_failure.json()["status"] == "failed"
    assert cached_failure.json()["reason"] == expected_category
    assert "internal detail" not in cached_failure.text


def test_simulated_race_starts_only_one_analysis(tmp_path):
    executor = HoldingExecutor()
    coordinator = _coordinator(tmp_path, executor=executor)
    barrier = Barrier(3)
    responses = []

    def request():
        barrier.wait()
        responses.append(_request(coordinator))

    threads = [Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(response["status"] for response in responses) == [
        "analysis_started", "in_progress"
    ]
    assert len(executor.calls) == 1


def test_automatic_endpoint_reuses_current_pipeline_and_experimental_payload(
    client, tmp_path
):
    coordinator = _coordinator(tmp_path)

    def service(config, *, analysis_id, **kwargs):
        result = make_result(analysis_id)
        result.recommendation["recommendation"] = "cortar"
        result.multisource = attach_experimental_fusion(
            {
                "enabled": True,
                "fusion_mode": "experimental",
                "official_recommendation_changed": False,
                "generated_at": "2026-08-10T15:00:00+00:00",
                "configuration": {},
                "sources": [
                    {
                        "source": "sentinel-1",
                        "status": "available",
                        "quality": 90.0,
                        "coverage": 100.0,
                        "observed_at": None,
                        "observations": [],
                        "metrics": {
                            "calibrated_observation_count": 4,
                            "temporal_analysis": {
                                "enabled": True,
                                "status": "completed",
                                "combined_status": "mixed",
                            },
                        },
                        "provenance": {"provider": "sentinel-1"},
                        "warnings": [],
                    }
                ],
            },
            result.recommendation,
        )
        return result

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {"bounds": BOUNDS, "center": CENTER, "zoom": 17}
    started = client.post("/api/analyses/automatic", json=payload)
    analysis_id = started.json()["analysis_id"]
    with session_scope() as session:
        persisted = get_analysis(session, analysis_id)
        assert persisted is not None
        persisted_at = persisted.created_at
        assert persisted.geometry is not None
        assert persisted.payload["analysis_trigger"] == "automatic_viewport"
        assert "decision_support" in persisted.payload
    cached = client.post("/api/analyses/automatic", json=payload)
    assert started.status_code == 200
    assert started.json()["status"] == "analysis_started"
    assert cached.status_code == 200
    body = cached.json()
    assert body["status"] == "cache_hit"
    assert body["result"]["analysis_trigger"] == "automatic_viewport"
    assert body["result"]["recommendation"]["decision"] == "cortar"
    experimental = body["result"]["multisource"]["experimental_fusion"]
    assert experimental["multisource_recommendation"] == "cortar"
    assert experimental["final_recommendation"] == "cortar"
    assert experimental["sentinel1_influenced_decision"] is False
    assert experimental["multisource_disagreement"] is True
    assert experimental["review_recommended"] is True
    assert experimental["operationally_authorized"] is False
    with session_scope() as session:
        persisted_after_cache_hit = get_analysis(session, analysis_id)
        assert persisted_after_cache_hit is not None
        assert persisted_after_cache_hit.created_at == persisted_at


def test_automatic_endpoint_exposes_roadside_contract_and_reuses_pipeline(client, tmp_path):
    provider, center = _road_provider(tmp_path)
    coordinator = _coordinator(
        tmp_path,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )

    received_geometries = []

    def service(config, *, analysis_id, **kwargs):
        received_geometries.append(config.geometry)
        result = make_result(analysis_id)
        result.recommendation["recommendation"] = "cortar"
        result.multisource = attach_experimental_fusion(
            {
                "enabled": True,
                "fusion_mode": "experimental",
                "official_recommendation_changed": False,
                "generated_at": "2026-08-10T15:00:00+00:00",
                "configuration": {},
                "sources": [
                    {
                        "source": "sentinel-1",
                        "status": "available",
                        "quality": 90.0,
                        "coverage": 100.0,
                        "observed_at": None,
                        "observations": [],
                        "metrics": {
                            "calibrated_observation_count": 4,
                            "temporal_analysis": {
                                "enabled": True,
                                "status": "completed",
                                "combined_status": "mixed",
                            },
                        },
                        "provenance": {"provider": "sentinel-1"},
                        "warnings": [],
                    }
                ],
            },
            result.recommendation,
        )
        return result

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {
        "bounds": {
            "west": -48.0,
            "south": -24.0,
            "east": -46.0,
            "north": -22.0,
        },
        "center": center,
        "zoom": 13,
    }
    response = client.post(
        "/api/analyses/automatic",
        json=payload,
    )
    cached = client.post("/api/analyses/automatic", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "analysis_started"
    assert body["analysis_started"] is True
    assert body["road"] == {
        **body["road"],
        "id": "SP-330",
        "ref": "SP-330",
        "name": "Rodovia Anhanguera",
        "orientation_basis": "unavailable",
    }
    assert body["spatial_strategy"]["name"] == "roadside"
    assert body["spatial_strategy"]["version"] == "1.0"
    assert body["spatial_strategy"]["aoi_algorithm_version"] == "1.2"
    assert body["spatial_strategy"]["dataset"]["valid_feature_count"] == 1
    assert body["canonical_segment_geometry"]["type"] == "LineString"
    assert body["roadway_exclusion_m"] == 25
    assert body["lateral_width_m"] == 40
    assert body["centerline"]["type"] == "LineString"
    assert body["roadway_exclusion_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert body["side_a_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert body["side_b_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert body["analyzed_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert body["roadside_metrics"]["minimum_centerline_distance_m"] == pytest.approx(
        25, abs=0.1
    )
    assert body["roadside_metrics"]["roadway_exclusion_overlap_m2"] == pytest.approx(
        0, abs=1e-6
    )
    assert len(received_geometries) == 1
    assert shape(received_geometries[0]).equals(shape(body["analyzed_geometry"]))
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["status"] == "cache_hit"
    assert cached_body["result"]["recommendation"]["decision"] == "cortar"
    with session_scope() as session:
        persisted = get_analysis(session, body["analysis_id"])
        assert persisted is not None
        assert persisted.subject_kind == "road_section"
        assert persisted.subject_key == body["spatial_key"]
        assert persisted.spatial_key == body["spatial_key"]
        assert persisted.road_id == "SP-330"
        assert persisted.road_ref == "SP-330"
        assert persisted.road_name == "Rodovia Anhanguera"
        assert persisted.axis_id == body["road"]["axis_id"]
        assert persisted.section_id == body["road"]["section_id"]
        assert persisted.section_index == body["road"]["section_index"]
    fusion = cached_body["result"]["multisource"]["experimental_fusion"]
    assert fusion["multisource_recommendation"] == "cortar"
    assert fusion["final_recommendation"] == "cortar"
    assert fusion["sentinel1_influenced_decision"] is False
    assert fusion["multisource_disagreement"] is True
    assert fusion["review_recommended"] is True


def test_sentinel1_failure_remains_fail_soft_in_automatic_result(client, tmp_path):
    coordinator = _coordinator(tmp_path)

    def service(config, *, analysis_id, **kwargs):
        result = make_result(analysis_id)
        result.multisource = attach_experimental_fusion(
            {
                "enabled": True,
                "fusion_mode": "experimental",
                "official_recommendation_changed": False,
                "generated_at": "2026-08-10T15:00:00+00:00",
                "configuration": {},
                "sources": [
                    {
                        "source": "sentinel-1",
                        "status": "error",
                        "quality": None,
                        "coverage": None,
                        "observed_at": None,
                        "observations": [],
                        "metrics": {},
                        "provenance": {"provider": "sentinel-1"},
                        "warnings": ["Auxiliary provider failed."],
                    }
                ],
            },
            result.recommendation,
        )
        return result

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {"bounds": BOUNDS, "center": CENTER, "zoom": 17}
    client.post("/api/analyses/automatic", json=payload)
    body = client.post("/api/analyses/automatic", json=payload).json()
    assert body["status"] == "cache_hit"
    result = body["result"]
    assert result["status"] == "completed"
    assert result["recommendation"]["decision"] == "nao_cortar"
    fusion = result["multisource"]["experimental_fusion"]
    assert fusion["multisource_recommendation"] == "nao_cortar"
    assert fusion["sentinel1_influenced_decision"] is False
    assert fusion["fusion_not_evaluable_reason"] == "sentinel1_error"


def test_sentinel1_failure_remains_fail_soft_for_roadside_aoi(client, tmp_path):
    provider, center = _road_provider(tmp_path)
    coordinator = _coordinator(
        tmp_path,
        spatial_strategy="roadside_v1",
        road_geometry_provider=provider,
    )

    def service(config, *, analysis_id, **kwargs):
        result = make_result(analysis_id)
        result.multisource = attach_experimental_fusion(
            {
                "enabled": True,
                "fusion_mode": "experimental",
                "official_recommendation_changed": False,
                "generated_at": "2026-08-10T15:00:00+00:00",
                "configuration": {},
                "sources": [
                    {
                        "source": "sentinel-1",
                        "status": "error",
                        "quality": None,
                        "coverage": None,
                        "observed_at": None,
                        "observations": [],
                        "metrics": {},
                        "provenance": {"provider": "sentinel-1"},
                        "warnings": ["Auxiliary provider failed."],
                    }
                ],
            },
            result.recommendation,
        )
        return result

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {
        "bounds": {"west": -48.0, "south": -24.0, "east": -46.0, "north": -22.0},
        "center": center,
        "zoom": 13,
    }
    client.post("/api/analyses/automatic", json=payload)
    body = client.post("/api/analyses/automatic", json=payload).json()

    assert body["status"] == "cache_hit"
    assert body["result"]["recommendation"]["decision"] == "nao_cortar"
    fusion = body["result"]["multisource"]["experimental_fusion"]
    assert fusion["multisource_recommendation"] == "nao_cortar"
    assert fusion["sentinel1_influenced_decision"] is False
    assert fusion["fusion_not_evaluable_reason"] == "sentinel1_error"


def test_automatic_endpoint_disabled_and_manual_endpoint_remains_compatible(
    client, tmp_path, valid_payload
):
    disabled = _coordinator(tmp_path, enabled=False)
    calls = []

    def service(config, *, analysis_id, **kwargs):
        calls.append(config)
        return make_result(analysis_id)

    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: disabled
    app.dependency_overrides[get_analysis_service] = lambda: service
    automatic = client.post(
        "/api/analyses/automatic",
        json={"bounds": BOUNDS, "center": CENTER, "zoom": 17},
    )
    manual = client.post("/api/analyses/run", json=valid_payload)
    assert automatic.json()["status"] == "disabled"
    assert automatic.json()["analysis_started"] is False
    assert manual.status_code == 200
    assert manual.json()["analysis_trigger"] == "manual"
    assert len(calls) == 1
