from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from apps.api.app.dependencies import get_analysis_now, get_analysis_service
from apps.api.app.main import app
from src.satellite_monitoring.service import AnalysisResult


VALID_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [-46.9620, -23.1090],
            [-46.9600, -23.1090],
            [-46.9600, -23.1070],
            [-46.9620, -23.1070],
            [-46.9620, -23.1090],
        ]
    ],
}


def make_result(analysis_id: str, run_directory: Path | None = None) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=analysis_id,
        status="completed",
        exit_code=0,
        recommendation={
            "recommendation": "nao_cortar",
            "confidence": "high",
            "experimental": True,
            "summary": "Indicadores locais abaixo do nivel alto.",
            "reasons": ["current_percentile_below_or_equal_50"],
            "blocking_reasons": [],
            "limitations": ["Requer validacao de campo."],
            "metrics": {"current_ndvi_mean": 0.52, "historical_median": 0.58},
        },
        aoi={"source": "geojson_inline", "area_square_meters": 1000.0},
        summary={
            "daily_observation_count": 4,
            "parameters": {"output_root": "C:\\internal\\outputs", "max_scenes": 12},
        },
        timeseries=[],
        scenes=[],
        artifacts={},
        warnings=[],
        errors=[],
        run_directory=run_directory,
    )


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_analysis_now] = lambda: datetime(
        2026, 8, 10, 12, tzinfo=ZoneInfo("America/Sao_Paulo")
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def valid_payload() -> dict:
    return {
        "geometry": VALID_GEOMETRY,
        "start_date": "2026-05-01",
        "end_date": "2026-08-04",
        "max_cloud_cover": 30,
        "max_scenes": 12,
        "scene_order": "newest",
        "min_valid_pixel_percentage": 70,
        "min_observations": 4,
        "daily_aggregation": "best",
        "decision": {},
    }


@pytest.fixture
def injected_success() -> str:
    analysis_id = str(uuid4())

    def service(*_, analysis_id: str, **__) -> AnalysisResult:
        return make_result(analysis_id)

    app.dependency_overrides[get_analysis_service] = lambda: service
    return analysis_id
