from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import (
    AnalysisRegistry,
    get_analysis_registry,
    get_validation_repository,
)
from apps.api.app.main import app
from apps.api.app.validation.models import ValidationSampleCreate
from apps.api.app.validation.repository import ValidationSampleRepository
from apps.api.app.validation.service import create_record
from src.satellite_monitoring.service import AnalysisResult


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [-46.962, -23.109],
            [-46.960, -23.109],
            [-46.960, -23.107],
            [-46.962, -23.107],
            [-46.962, -23.109],
        ]
    ],
}


def make_validation_result(analysis_id: str) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=analysis_id,
        status="completed",
        exit_code=0,
        recommendation={
            "recommendation": "nao_cortar",
            "confidence": "high",
            "metrics": {
                "observation_count": 6,
                "current_ndvi_mean": 0.62,
                "current_ndvi_median": 0.61,
                "historical_median": 0.55,
                "historical_mean": 0.54,
                "historical_standard_deviation": 0.08,
                "current_percentile": 72.0,
                "recent_trend": 0.03,
                "recent_trend_status": "increasing",
                "last_absolute_change": 0.02,
                "last_relative_change_percentage": 3.3,
                "significant_drop_detected": False,
                "significant_drop_confirmed": False,
                "max_observation_gap_days": 12,
                "first_date": "2026-06-01",
                "last_date": "2026-09-01",
            },
        },
        aoi=GEOMETRY,
        summary={
            "analysis_quality": {"score": 91.0, "status": "high"},
            "aoi": {
                "centroid": [-46.961, -23.108],
                "bbox": [-46.962, -23.109, -46.960, -23.107],
            },
        },
        timeseries=[{"vegetation_fraction": 0.74}],
        scenes=[],
        selected_area_m2=4419.4,
        effective_analysis_area_m2=4113.2,
        effective_analysis_pct=93.07,
        multisource={
            "enabled": True,
            "fusion_mode": "shadow",
            "official_recommendation_changed": False,
            "sources": [
                {
                    "source": "sentinel-1",
                    "status": "available",
                    "quality": 92.0,
                    "coverage": 96.0,
                    "metrics": {
                        "observation_count": 8,
                        "calibrated_observation_count": 7,
                        "uncalibrated_observation_count": 1,
                        "relative_orbits": [53, 126],
                        "canonical_relative_orbit": 53,
                        "canonical_observation_count": 5,
                        "temporal_comparability": (
                            "multiple_relative_orbits_requires_grouping"
                        ),
                        "radiometric_calibration_status": "partial",
                        "canonical_metrics": {
                            "vv_sigma0_median_linear": 0.04,
                            "vh_sigma0_median_linear": 0.01,
                            "vv_sigma0_median_db": -13.9794,
                            "vh_sigma0_median_db": -20.0,
                            "vh_vv_sigma0_ratio_median": 0.25,
                            "vh_minus_vv_db_median": -6.0206,
                            "mean_valid_pixel_percentage": 88.0,
                            "mean_coverage": 95.0,
                        },
                    },
                    "warnings": ["Multiple relative orbits."],
                    "provenance": {
                        "radiometric_calibration": "sigma0",
                        "terrain_correction": False,
                        "temporal_grouping": "sat:relative_orbit",
                    },
                }
            ],
        },
        artifacts={},
    )


def request_payload(analysis_id: str, **updates) -> dict:
    payload = {
        "analysis_id": analysis_id,
        "vegetation_class": "shrub",
        "maintenance_truth": "cut",
        "validation_source": "field_inspection",
        "reference_date": "2026-09-04",
        "notes": "Vegetacao arbustiva densa.",
    }
    payload.update(updates)
    return payload


@pytest.fixture
def validation_api(client: TestClient, tmp_path: Path):
    repository = ValidationSampleRepository(tmp_path / "validation.sqlite3")
    registry = AnalysisRegistry()
    app.dependency_overrides[get_validation_repository] = lambda: repository
    app.dependency_overrides[get_analysis_registry] = lambda: registry
    yield client, repository, registry


def test_repository_initializes_empty_database_and_parent_directory(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "validation.sqlite3"
    repository = ValidationSampleRepository(database)

    assert database.is_file()
    assert repository.all_rows() == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version FROM validation_schema_migrations"
        ).fetchone() == (1,)


def test_create_captures_aoi_sentinel2_and_calibrated_sentinel1(validation_api) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))

    response = client.post("/api/validation-samples", json=request_payload(analysis_id))

    assert response.status_code == 201
    body = response.json()
    assert body["selected_area_m2"] == pytest.approx(4419.4)
    assert body["s2_ndvi_mean"] == pytest.approx(0.62)
    assert body["s1_canonical_relative_orbit"] == 53
    assert body["s1_vv_sigma0_db"] == pytest.approx(-13.9794)
    snapshot = body["snapshot"]
    assert snapshot["aoi"]["geometry_geojson"] == GEOMETRY
    assert snapshot["aoi"]["effective_analysis_area_m2"] == pytest.approx(4113.2)
    assert snapshot["sentinel2"]["historical_standard_deviation"] == 0.08
    assert snapshot["sentinel2"]["vegetation_fraction"] == 0.74
    assert snapshot["sentinel1"]["calibrated_observation_count"] == 7
    assert snapshot["sentinel1"]["radiometric_calibration_status"] == "partial"
    assert snapshot["sentinel1"]["canonical_metrics"][
        "vh_minus_vv_db_median"
    ] == pytest.approx(-6.0206)
    assert snapshot["sentinel1"]["provenance"]["terrain_correction"] is False


def test_missing_metrics_remain_null(validation_api) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    result = make_validation_result(analysis_id)
    result.recommendation["metrics"].pop("current_ndvi_median")
    result.multisource = None
    registry.add(result)

    body = client.post(
        "/api/validation-samples", json=request_payload(analysis_id)
    ).json()

    assert body["s2_ndvi_median"] is None
    assert body["s1_vv_sigma0_db"] is None
    assert body["snapshot"]["sentinel1"]["source_status"] is None
    assert body["snapshot"]["sentinel1"]["canonical_metrics"][
        "vv_sigma0_median_linear"
    ] is None


def test_sample_survives_repository_restart_and_registry_clear(validation_api) -> None:
    client, repository, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))
    created = client.post(
        "/api/validation-samples", json=request_payload(analysis_id)
    ).json()
    registry.clear()

    restarted = ValidationSampleRepository(repository.database_path)
    persisted = restarted.get(created["sample_id"])

    assert persisted is not None
    assert persisted["analysis_id"] == analysis_id
    assert persisted["snapshot"]["sentinel1"]["canonical_relative_orbit"] == 53
    detail = client.get(f"/api/validation-samples/{created['sample_id']}")
    assert detail.status_code == 200


def test_duplicate_analysis_is_conflict(validation_api) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))
    assert client.post(
        "/api/validation-samples", json=request_payload(analysis_id)
    ).status_code == 201

    duplicate = client.post(
        "/api/validation-samples", json=request_payload(analysis_id)
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "VALIDATION_SAMPLE_EXISTS"


def test_missing_analysis_is_not_found(validation_api) -> None:
    client, _, _ = validation_api
    response = client.post(
        "/api/validation-samples", json=request_payload(str(uuid4()))
    )
    assert response.status_code == 404
    assert "execute-a novamente" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vegetation_class", "forest"),
        ("maintenance_truth", "maybe"),
        ("validation_source", "guess"),
        ("reference_date", "04/09/2026"),
    ],
)
def test_invalid_ground_truth_is_rejected(validation_api, field: str, value: str) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))
    response = client.post(
        "/api/validation-samples", json=request_payload(analysis_id, **{field: value})
    )
    assert response.status_code == 422


def test_excessive_notes_are_rejected(validation_api) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))
    response = client.post(
        "/api/validation-samples",
        json=request_payload(analysis_id, notes="x" * 1001),
    )
    assert response.status_code == 422


def test_list_filters_paginates_and_detail_returns_snapshot(validation_api) -> None:
    client, _, registry = validation_api
    created_ids = []
    for vegetation_class in ("shrub", "tree", "shrub"):
        analysis_id = str(uuid4())
        registry.add(make_validation_result(analysis_id))
        response = client.post(
            "/api/validation-samples",
            json=request_payload(analysis_id, vegetation_class=vegetation_class),
        )
        created_ids.append(response.json()["sample_id"])

    filtered = client.get(
        "/api/validation-samples",
        params={"vegetation_class": "shrub", "limit": 1, "offset": 1},
    )

    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert len(filtered.json()["items"]) == 1
    assert "snapshot" not in filtered.json()["items"][0]
    detail = client.get(f"/api/validation-samples/{created_ids[0]}")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["ground_truth"]["vegetation_class"] == "shrub"
    assert client.get(f"/api/validation-samples/{uuid4()}").status_code == 404


def test_list_supports_all_ground_truth_filters(validation_api) -> None:
    client, _, registry = validation_api
    for truth, source in (
        ("cut", "field_inspection"),
        ("no_cut", "aerial_imagery"),
    ):
        analysis_id = str(uuid4())
        registry.add(make_validation_result(analysis_id))
        client.post(
            "/api/validation-samples",
            json=request_payload(
                analysis_id,
                maintenance_truth=truth,
                validation_source=source,
            ),
        )

    by_truth = client.get(
        "/api/validation-samples", params={"maintenance_truth": "no_cut"}
    ).json()
    by_source = client.get(
        "/api/validation-samples", params={"validation_source": "field_inspection"}
    ).json()

    assert by_truth["total"] == 1
    assert by_truth["items"][0]["validation_source"] == "aerial_imagery"
    assert by_source["total"] == 1
    assert by_source["items"][0]["maintenance_truth"] == "cut"


def test_empty_summary_has_all_classes_and_targets(validation_api) -> None:
    client, _, _ = validation_api
    body = client.get("/api/validation-summary").json()
    assert body["total_samples"] == 0
    assert body["target_total"] == 30
    assert body["remaining_total"] == 30
    assert body["counts_by_vegetation_class"]["tree"] == 0
    assert set(body["by_vegetation_class"]) == {
        "low_grass", "tall_dense_grass", "shrub", "tree", "mixed"
    }
    assert body["by_vegetation_class"]["tree"]["statistics"] == {
        "sentinel2": {}, "sentinel1": {}
    }


def test_summary_progress_and_statistics_by_class(tmp_path: Path) -> None:
    repository = ValidationSampleRepository(tmp_path / "summary.sqlite3")
    for index, ndvi in enumerate((0.4, 0.6, 0.8)):
        analysis_id = str(uuid4())
        result = make_validation_result(analysis_id)
        result.recommendation["metrics"]["current_ndvi_mean"] = ndvi
        result.multisource["sources"][0]["metrics"]["canonical_metrics"][
            "vv_sigma0_median_db"
        ] = -15.0 + index
        payload = ValidationSampleCreate.model_validate(
            request_payload(analysis_id, vegetation_class="tree")
        )
        repository.insert(
            create_record(
                result,
                payload,
                created_at=datetime(2026, 9, index + 1, tzinfo=timezone.utc),
            )
        )

    from apps.api.app.validation.service import build_summary

    summary = build_summary(repository.all_rows())
    tree = summary["by_vegetation_class"]["tree"]
    assert summary["total_samples"] == 3
    assert summary["remaining_total"] == 27
    assert tree["count"] == 3
    assert tree["remaining"] == 3
    assert tree["statistics"]["sentinel2"]["current_ndvi_mean"] == {
        "count": 3,
        "median": pytest.approx(0.6),
        "mean": pytest.approx(0.6),
        "min": pytest.approx(0.4),
        "max": pytest.approx(0.8),
        "q25": pytest.approx(0.5),
        "q75": pytest.approx(0.7),
    }
    assert tree["statistics"]["sentinel1"]["canonical_vv_sigma0_db"][
        "median"
    ] == pytest.approx(-14.0)
    assert summary["counts_by_maintenance_truth"]["cut"] == 3


def test_summary_progress_stops_at_target_without_becoming_negative() -> None:
    from apps.api.app.validation.service import build_summary

    row = {
        "vegetation_class": "mixed",
        "maintenance_truth": "uncertain",
    }
    at_target = build_summary([dict(row) for _ in range(30)])
    beyond_target = build_summary([dict(row) for _ in range(31)])

    assert at_target["remaining_total"] == 0
    assert beyond_target["remaining_total"] == 0
    assert at_target["by_vegetation_class"]["mixed"]["remaining"] == 0


def test_csv_export_is_flat_and_excludes_snapshot_json(validation_api) -> None:
    client, _, registry = validation_api
    analysis_id = str(uuid4())
    registry.add(make_validation_result(analysis_id))
    client.post("/api/validation-samples", json=request_payload(analysis_id))

    response = client.get("/api/validation-export")

    assert response.status_code == 200
    assert "validation_samples.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["analysis_id"] == analysis_id
    assert rows[0]["s1_vv_sigma0_db"] == "-13.9794"
    assert "snapshot_json" not in rows[0]
    assert "geometry_geojson" not in rows[0]


def test_operational_contract_remains_shadow_and_guia_is_registered() -> None:
    result = make_validation_result(str(uuid4()))
    assert result.recommendation["recommendation"] == "nao_cortar"
    assert result.multisource["fusion_mode"] == "shadow"
    assert result.multisource["official_recommendation_changed"] is False
    route_paths = {route.path for route in app.routes}
    assert "/api/analyses/run" in route_paths
    assert any(path.startswith("/api/guia") for path in route_paths)
