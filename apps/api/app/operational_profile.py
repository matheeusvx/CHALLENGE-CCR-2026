"""Perfil operacional e janela temporal usados pela API web."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from src.satellite_monitoring.config import (
    DEFAULT_DAILY_AGGREGATION,
    DEFAULT_DECISION_MIN_OBSERVATIONS,
    DEFAULT_HIGH_VEGETATION_PERCENTILE,
    DEFAULT_MAX_GAP_DAYS,
    DEFAULT_MAX_SCENES,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_MIN_VALID_PIXEL_PERCENTAGE,
    DEFAULT_RECENT_INTERVENTION_DAYS,
    DEFAULT_SCENE_ORDER,
    DEFAULT_SIGNIFICANT_DROP_ABSOLUTE,
    DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE,
    DEFAULT_TREND_WINDOW,
)

DEFAULT_ANALYSIS_TIMEZONE = "America/Sao_Paulo"


@dataclass(frozen=True)
class OperationalAnalysisProfile:
    """Parametros experimentais do cenario de faixa lateral gramada."""

    profile_id: str = "roadside_grass_default"
    max_cloud_cover: float = 30.0
    max_scenes: int = DEFAULT_MAX_SCENES
    scene_order: str = DEFAULT_SCENE_ORDER
    min_valid_pixel_percentage: float = DEFAULT_MIN_VALID_PIXEL_PERCENTAGE
    min_observations: int = DEFAULT_MIN_OBSERVATIONS
    daily_aggregation: str = DEFAULT_DAILY_AGGREGATION
    decision_min_observations: int = DEFAULT_DECISION_MIN_OBSERVATIONS
    high_vegetation_percentile: float = DEFAULT_HIGH_VEGETATION_PERCENTILE
    significant_drop_absolute: float = DEFAULT_SIGNIFICANT_DROP_ABSOLUTE
    significant_drop_relative_percentage: float = (
        DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE
    )
    trend_window: int = DEFAULT_TREND_WINDOW
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS
    recent_intervention_days: int = DEFAULT_RECENT_INTERVENTION_DAYS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_OPERATIONAL_ANALYSIS_PROFILE = OperationalAnalysisProfile()


@dataclass(frozen=True)
class AnalysisPeriod:
    start_date: date
    end_date: date
    timezone: str
    strategy: Literal["previous_calendar_month", "explicit"]

    def to_dict(self) -> dict[str, str]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "timezone": self.timezone,
            "strategy": self.strategy,
        }


def _one_calendar_month_before(value: date) -> date:
    previous_month = 12 if value.month == 1 else value.month - 1
    previous_year = value.year - 1 if value.month == 1 else value.year
    last_day = calendar.monthrange(previous_year, previous_month)[1]
    return date(previous_year, previous_month, min(value.day, last_day))


def get_analysis_date_range(
    now: datetime | None = None,
    *,
    timezone_name: str = DEFAULT_ANALYSIS_TIMEZONE,
) -> AnalysisPeriod:
    """Retorna hoje e o mesmo dia do mes anterior no timezone operacional."""

    analysis_timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(analysis_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=analysis_timezone)
    else:
        current = current.astimezone(analysis_timezone)
    end_date = current.date()
    return AnalysisPeriod(
        start_date=_one_calendar_month_before(end_date),
        end_date=end_date,
        timezone=timezone_name,
        strategy="previous_calendar_month",
    )


def resolve_analysis_period(
    start_date: date | None,
    end_date: date | None,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_ANALYSIS_TIMEZONE,
) -> AnalysisPeriod:
    """Preserva datas explicitas antigas ou aplica a janela operacional."""

    if start_date is None and end_date is None:
        return get_analysis_date_range(now, timezone_name=timezone_name)
    if start_date is None or end_date is None:
        raise ValueError("As datas inicial e final devem ser informadas em conjunto.")
    if start_date > end_date:
        raise ValueError("A data inicial nao pode ser posterior a data final.")
    return AnalysisPeriod(
        start_date=start_date,
        end_date=end_date,
        timezone=timezone_name,
        strategy="explicit",
    )
