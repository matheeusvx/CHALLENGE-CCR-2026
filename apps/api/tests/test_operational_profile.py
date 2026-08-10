from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.api.app.config import ApiSettings
from apps.api.app.operational_profile import (
    DEFAULT_ANALYSIS_TIMEZONE,
    DEFAULT_OPERATIONAL_ANALYSIS_PROFILE,
    get_analysis_date_range,
)


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
