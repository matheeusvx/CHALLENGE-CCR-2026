from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.api.app.config import ApiSettings
from apps.api.app.dependencies import get_analysis_service
from apps.api.app.operational_profile import (
    DEFAULT_ANALYSIS_TIMEZONE,
    DEFAULT_OPERATIONAL_ANALYSIS_PROFILE,
    get_analysis_date_range,
)
from src.satellite_monitoring.service import run_monitoring_analysis


@pytest.mark.parametrize(
    ("current", "expected_start"),
    [
        ("2026-08-10", "2026-07-10"),
        ("2026-08-11", "2026-07-11"),
        ("2026-03-31", "2026-02-28"),
        ("2028-03-31", "2028-02-29"),
        ("2026-01-31", "2025-12-31"),
    ],
)
def test_previous_calendar_month_date_range(
    current: str,
    expected_start: str,
) -> None:
    now = datetime.fromisoformat(f"{current}T12:00:00").replace(
        tzinfo=ZoneInfo(DEFAULT_ANALYSIS_TIMEZONE)
    )

    period = get_analysis_date_range(now)

    assert period.start_date.isoformat() == expected_start
    assert period.end_date.isoformat() == current
    assert period.strategy == "previous_calendar_month"


def test_analysis_timezone_default_is_operational_timezone() -> None:
    assert DEFAULT_ANALYSIS_TIMEZONE == "America/Sao_Paulo"
    assert ApiSettings().analysis_timezone == DEFAULT_ANALYSIS_TIMEZONE


def test_validation_database_relative_path_is_repository_rooted(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VALIDATION_DB_PATH", "data/custom-validation.sqlite3")

    configured = ApiSettings()

    assert configured.validation_db_path.is_absolute()
    assert configured.validation_db_path.name == "custom-validation.sqlite3"
    assert configured.validation_db_path.parent.name == "data"


def test_operational_profile_has_validated_experimental_values() -> None:
    assert DEFAULT_OPERATIONAL_ANALYSIS_PROFILE.to_dict() == {
        "profile_id": "roadside_grass_default",
        "max_cloud_cover": 30.0,
        "max_scenes": 12,
        "max_candidate_scenes": 40,
        "scene_order": "newest",
        "min_valid_pixel_percentage": 70.0,
        "min_valid_pixel_count": 30,
        "min_aoi_coverage_percentage": 95.0,
        "min_observations": 4,
        "daily_aggregation": "best",
        "decision_min_observations": 4,
        "high_vegetation_percentile": 75.0,
        "significant_drop_absolute": 0.06,
        "significant_drop_relative_percentage": 15.0,
        "trend_window": 3,
        "max_gap_days": 20,
        "recent_intervention_days": 20,
    }


def test_multisource_feature_flags_are_disabled_by_default(monkeypatch) -> None:
    for variable in (
        "MULTISOURCE_ENABLED",
        "SENTINEL1_ENABLED",
        "SENTINEL1_COLLECTION",
        "SENTINEL1_MAX_SCENES",
        "GEDI_ENABLED",
        "ICESAT2_ENABLED",
        "MULTISOURCE_FUSION_MODE",
    ):
        monkeypatch.delenv(variable, raising=False)

    configured = ApiSettings()

    assert configured.multisource_enabled is False
    assert configured.sentinel1_enabled is False
    assert configured.sentinel1_collection == "sentinel-1-grd"
    assert configured.sentinel1_max_scenes == 8
    assert configured.gedi_enabled is False
    assert configured.icesat2_enabled is False
    assert configured.multisource_fusion_mode == "disabled"


def test_multisource_feature_flags_read_explicit_environment(monkeypatch) -> None:
    monkeypatch.setenv("MULTISOURCE_ENABLED", "true")
    monkeypatch.setenv("SENTINEL1_ENABLED", "true")
    monkeypatch.setenv("SENTINEL1_COLLECTION", "sentinel-1-rtc")
    monkeypatch.setenv("SENTINEL1_MAX_SCENES", "5")
    monkeypatch.setenv("GEDI_ENABLED", "1")
    monkeypatch.setenv("ICESAT2_ENABLED", "yes")
    monkeypatch.setenv("MULTISOURCE_FUSION_MODE", "shadow")

    configured = ApiSettings()

    assert configured.multisource_enabled is True
    assert configured.sentinel1_enabled is True
    assert configured.sentinel1_collection == "sentinel-1-rtc"
    assert configured.sentinel1_max_scenes == 5
    assert configured.gedi_enabled is True
    assert configured.icesat2_enabled is True
    assert configured.multisource_fusion_mode == "shadow"


def test_operational_fusion_mode_is_an_explicit_supported_value(monkeypatch) -> None:
    monkeypatch.setenv("MULTISOURCE_FUSION_MODE", "operational")

    configured = ApiSettings()

    assert configured.multisource_fusion_mode == "operational"


def test_experimental_fusion_mode_is_an_explicit_supported_value(monkeypatch) -> None:
    monkeypatch.setenv("MULTISOURCE_FUSION_MODE", "experimental")

    configured = ApiSettings()

    assert configured.multisource_fusion_mode == "experimental"


def test_multisource_disabled_keeps_the_sentinel_service_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("MULTISOURCE_ENABLED", "false")

    assert ApiSettings().multisource_enabled is False
    assert get_analysis_service() is run_monitoring_analysis
