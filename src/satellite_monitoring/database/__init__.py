"""Camada de persistencia do historico operacional da plataforma Motiva."""

from __future__ import annotations

from .models import (
    DEFAULT_HEIGHT_LIMIT_CM,
    HEIGHT_CLASS_RANGES,
    HEIGHT_THRESHOLD_CM,
    STRICT_HEIGHT_LIMIT_CM,
    Analysis,
    AnalysisObservation,
    Base,
    CrossSectionPosition,
    FieldConditionObservation,
    Highway,
    KmMarker,
    MowingPolygon,
    NdviFieldSample,
    Segment,
    exceeds_height_limit,
)
from .repository import (
    count_analyses,
    delete_analysis,
    get_analysis,
    list_analyses,
    nearest_km,
    save_analysis,
)
from .session import (
    database_url,
    get_engine,
    get_session_factory,
    init_database,
    reset_engine,
    session_scope,
)

__all__ = [
    "Analysis",
    "AnalysisObservation",
    "Base",
    "CrossSectionPosition",
    "DEFAULT_HEIGHT_LIMIT_CM",
    "FieldConditionObservation",
    "HEIGHT_CLASS_RANGES",
    "HEIGHT_THRESHOLD_CM",
    "Highway",
    "KmMarker",
    "MowingPolygon",
    "NdviFieldSample",
    "STRICT_HEIGHT_LIMIT_CM",
    "Segment",
    "count_analyses",
    "database_url",
    "delete_analysis",
    "exceeds_height_limit",
    "get_analysis",
    "get_engine",
    "get_session_factory",
    "init_database",
    "list_analyses",
    "nearest_km",
    "reset_engine",
    "save_analysis",
    "session_scope",
]
