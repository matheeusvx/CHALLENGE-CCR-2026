"""Deterministic viewport AOIs, persistent spatial cache, and local deduplication."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from src.satellite_monitoring.road_geometry import (
    RoadGeometryProvider,
    resolve_canonical_road_section,
)
from src.satellite_monitoring.roadside_aoi import (
    RoadsideAoiConfig,
    build_roadside_aoi,
)


logger = logging.getLogger(__name__)
WEB_MERCATOR_MAX_LATITUDE = 85.05112878
EARTH_RADIUS_METERS = 6_371_008.8


@dataclass(frozen=True)
class AutomaticAnalysisPolicy:
    enabled: bool
    min_zoom: float
    max_zoom: float
    canonical_tile_zoom: int
    max_area_km2: float
    min_dimension_meters: float
    max_dimension_meters: float
    cache_ttl_seconds: int
    in_progress_ttl_seconds: int
    failure_ttl_seconds: int
    force_refresh_cooldown_seconds: int
    max_concurrent: int
    spatial_strategy: str = "tile_v1"
    roadside_min_zoom: float = 13.0
    road_snap_max_distance_m: float = 50.0
    road_ambiguity_tolerance_m: float = 10.0
    road_segment_length_m: float = 300.0
    roadway_exclusion_m: float = 25.0
    lateral_width_m: float = 40.0
    roadside_min_area_m2: float = 5_000.0
    roadside_min_width_m: float = 20.0


@dataclass(frozen=True)
class CanonicalViewport:
    spatial_key: str
    geometry: dict[str, Any]
    bounds: dict[str, float]
    viewport_area_km2: float
    viewport_width_meters: float
    viewport_height_meters: float


class InvalidViewportError(ValueError):
    pass


class AutomaticPipelineFailure(RuntimeError):
    """Sanitized failure raised by the shared analysis adapter."""

    def __init__(self, category: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category


def _sanitized_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, AutomaticPipelineFailure):
        category = exc.category
    elif isinstance(exc, (TimeoutError, ConnectionError)):
        category = "remote_stac_failure"
    elif isinstance(exc, sqlite3.Error):
        category = "cache_persistence_failure"
    elif type(exc).__name__ in {"ApiError", "InvalidAnalysisGeometryError"}:
        category = "gis_failure"
    elif type(exc).__name__ == "ValidationError":
        category = "api_response_validation_failure"
    else:
        category = "unexpected_runtime_failure"
    detail = " ".join(str(exc).split())[:500] or type(exc).__name__
    return category, detail


def _validated_viewport_values(
    *,
    bounds: Mapping[str, Any],
    center: Mapping[str, Any],
    zoom: float,
    max_zoom: float,
) -> tuple[dict[str, float], float, float, float]:
    try:
        numeric_bounds = {
            name: float(bounds[name]) for name in ("west", "south", "east", "north")
        }
        longitude = float(center["lng"])
        latitude = float(center["lat"])
        zoom_value = float(zoom)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidViewportError("viewport_coordinates_invalid") from exc
    values = [*numeric_bounds.values(), longitude, latitude, zoom_value]
    if not all(math.isfinite(value) for value in values):
        raise InvalidViewportError("viewport_values_must_be_finite")
    if not (-180 <= numeric_bounds["west"] < numeric_bounds["east"] <= 180):
        raise InvalidViewportError("viewport_longitude_bounds_invalid")
    if not (
        -WEB_MERCATOR_MAX_LATITUDE
        <= numeric_bounds["south"]
        < numeric_bounds["north"]
        <= WEB_MERCATOR_MAX_LATITUDE
    ):
        raise InvalidViewportError("viewport_latitude_bounds_invalid")
    if not (
        numeric_bounds["west"] <= longitude <= numeric_bounds["east"]
        and numeric_bounds["south"] <= latitude <= numeric_bounds["north"]
    ):
        raise InvalidViewportError("viewport_center_outside_bounds")
    if not (0 <= zoom_value <= max_zoom):
        raise InvalidViewportError("viewport_zoom_invalid")
    return numeric_bounds, longitude, latitude, zoom_value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("automatic analysis clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _viewport_dimensions(bounds: Mapping[str, float], latitude: float) -> tuple[float, float]:
    width = EARTH_RADIUS_METERS * math.cos(math.radians(latitude)) * math.radians(
        bounds["east"] - bounds["west"]
    )
    height = EARTH_RADIUS_METERS * math.radians(bounds["north"] - bounds["south"])
    return width, height


def canonicalize_viewport(
    *,
    bounds: Mapping[str, Any],
    center: Mapping[str, Any],
    zoom: float,
    policy: AutomaticAnalysisPolicy,
) -> CanonicalViewport:
    """Validate a viewport and map its center to a stable Web Mercator tile."""
    numeric_bounds, longitude, latitude, _ = _validated_viewport_values(
        bounds=bounds, center=center, zoom=zoom, max_zoom=policy.max_zoom
    )

    width, height = _viewport_dimensions(numeric_bounds, latitude)
    area_km2 = width * height / 1_000_000
    if width < policy.min_dimension_meters or height < policy.min_dimension_meters:
        raise InvalidViewportError("viewport_too_small")
    if width > policy.max_dimension_meters or height > policy.max_dimension_meters:
        raise InvalidViewportError("viewport_dimensions_too_large")
    if area_km2 > policy.max_area_km2:
        raise InvalidViewportError("viewport_too_large")

    tile_zoom = policy.canonical_tile_zoom
    scale = 1 << tile_zoom
    x = min(scale - 1, max(0, int((longitude + 180.0) / 360.0 * scale)))
    latitude_radians = math.radians(latitude)
    y = min(
        scale - 1,
        max(
            0,
            int(
                (1 - math.asinh(math.tan(latitude_radians)) / math.pi)
                / 2
                * scale
            ),
        ),
    )

    def tile_longitude(tile_x: int) -> float:
        return tile_x / scale * 360.0 - 180.0

    def tile_latitude(tile_y: int) -> float:
        mercator = math.pi * (1 - 2 * tile_y / scale)
        return math.degrees(math.atan(math.sinh(mercator)))

    west, east = tile_longitude(x), tile_longitude(x + 1)
    north, south = tile_latitude(y), tile_latitude(y + 1)
    canonical_bounds = {"west": west, "south": south, "east": east, "north": north}
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [east, south], [east, north], [west, north], [west, south]
        ]],
    }
    return CanonicalViewport(
        spatial_key=f"tile:{tile_zoom}/{x}/{y}",
        geometry=geometry,
        bounds=canonical_bounds,
        viewport_area_km2=area_km2,
        viewport_width_meters=width,
        viewport_height_meters=height,
    )


class AutomaticAnalysisRepository:
    """SQLite metadata cache with transactional claim semantics."""

    def __init__(self, path: str | Path, *, timeout: float = 10.0) -> None:
        self.path = Path(path).expanduser().resolve()
        self.timeout = timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS automatic_analysis_runs (
                    analysis_id TEXT PRIMARY KEY,
                    spatial_key TEXT NOT NULL,
                    analysis_period TEXT NOT NULL,
                    analysis_mode TEXT NOT NULL CHECK (analysis_mode = 'automatic_viewport'),
                    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    canonical_bounds_json TEXT NOT NULL,
                    aoi_fingerprint TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_automatic_analysis_lookup
                    ON automatic_analysis_runs(spatial_key, analysis_period, status, expires_at);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_automatic_analysis_active
                    ON automatic_analysis_runs(spatial_key, analysis_period)
                    WHERE status = 'in_progress';
                """
            )

    def claim(
        self,
        *,
        canonical: CanonicalViewport,
        analysis_period: str,
        analysis_id: str,
        now: datetime,
        policy: AutomaticAnalysisPolicy,
        force_refresh: bool,
        aoi_fingerprint: str,
    ) -> dict[str, Any]:
        current = _utc(now)
        timestamp = current.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                """SELECT analysis_id, spatial_key FROM automatic_analysis_runs
                   WHERE status='in_progress' AND expires_at <= ?""",
                (timestamp,),
            ).fetchall()
            connection.execute(
                """UPDATE automatic_analysis_runs
                   SET status='failed', updated_at=?, error_code='in_progress_lease_expired'
                   WHERE status='in_progress' AND expires_at <= ?""",
                (timestamp, timestamp),
            )
            for expired_row in expired:
                logger.warning(
                    "automatic_analysis_lease_expired",
                    extra={
                        "analysis_id": expired_row["analysis_id"],
                        "spatial_key": expired_row["spatial_key"],
                        "status": "failed",
                        "failure_category": "in_progress_lease_expired",
                        "failure_detail": "worker_did_not_finish_before_persisted_lease_expiry",
                    },
                )
            active = connection.execute(
                """SELECT * FROM automatic_analysis_runs
                   WHERE spatial_key=? AND analysis_period=? AND status='in_progress'
                   ORDER BY created_at DESC LIMIT 1""",
                (canonical.spatial_key, analysis_period),
            ).fetchone()
            if active is not None:
                return {"outcome": "in_progress", "row": dict(active)}
            if not force_refresh:
                cached = connection.execute(
                    """SELECT * FROM automatic_analysis_runs
                       WHERE spatial_key=? AND analysis_period=?
                         AND status IN ('completed', 'failed') AND expires_at > ?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (canonical.spatial_key, analysis_period, timestamp),
                ).fetchone()
                if cached is not None:
                    outcome = "cache_hit" if cached["status"] == "completed" else "failed"
                    return {"outcome": outcome, "row": dict(cached)}
            else:
                cooldown_after = (
                    current - timedelta(seconds=policy.force_refresh_cooldown_seconds)
                ).isoformat()
                recent = connection.execute(
                    """SELECT * FROM automatic_analysis_runs
                       WHERE spatial_key=? AND analysis_period=? AND updated_at > ?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (canonical.spatial_key, analysis_period, cooldown_after),
                ).fetchone()
                if recent is not None:
                    return {"outcome": "refresh_rate_limited", "row": dict(recent)}
            in_progress_count = connection.execute(
                "SELECT count(*) FROM automatic_analysis_runs WHERE status='in_progress'"
            ).fetchone()[0]
            if in_progress_count >= policy.max_concurrent:
                return {"outcome": "capacity_reached", "row": None}
            expires_at = (
                current + timedelta(seconds=policy.in_progress_ttl_seconds)
            ).isoformat()
            connection.execute(
                """INSERT INTO automatic_analysis_runs (
                       analysis_id, spatial_key, analysis_period, analysis_mode, status,
                       created_at, updated_at, expires_at, canonical_bounds_json,
                       aoi_fingerprint, result_json, error_code
                   ) VALUES (?, ?, ?, 'automatic_viewport', 'in_progress', ?, ?, ?, ?, ?, NULL, NULL)""",
                (
                    analysis_id,
                    canonical.spatial_key,
                    analysis_period,
                    timestamp,
                    timestamp,
                    expires_at,
                    json.dumps(canonical.bounds, sort_keys=True, separators=(",", ":")),
                    aoi_fingerprint,
                ),
            )
            return {"outcome": "claimed", "row": None}

    def complete(
        self, analysis_id: str, result: Mapping[str, Any], *, now: datetime, ttl_seconds: int
    ) -> None:
        current = _utc(now)
        with self._connect() as connection:
            connection.execute(
                """UPDATE automatic_analysis_runs
                   SET status='completed', updated_at=?, expires_at=?, result_json=?, error_code=NULL
                   WHERE analysis_id=? AND status='in_progress'""",
                (
                    current.isoformat(),
                    (current + timedelta(seconds=ttl_seconds)).isoformat(),
                    json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False),
                    analysis_id,
                ),
            )

    def fail(
        self, analysis_id: str, error_code: str, *, now: datetime, ttl_seconds: int
    ) -> None:
        current = _utc(now)
        with self._connect() as connection:
            connection.execute(
                """UPDATE automatic_analysis_runs
                   SET status='failed', updated_at=?, expires_at=?, result_json=NULL, error_code=?
                   WHERE analysis_id=? AND status='in_progress'""",
                (
                    current.isoformat(),
                    (current + timedelta(seconds=ttl_seconds)).isoformat(),
                    error_code,
                    analysis_id,
                ),
            )

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM automatic_analysis_runs")


class AutomaticAnalysisCoordinator:
    def __init__(
        self,
        repository: AutomaticAnalysisRepository,
        policy: AutomaticAnalysisPolicy,
        *,
        executor: Executor | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        road_geometry_provider: RoadGeometryProvider | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.executor = executor or ThreadPoolExecutor(
            max_workers=policy.max_concurrent,
            thread_name_prefix="automatic-analysis",
        )
        self.clock = clock
        self.road_geometry_provider = road_geometry_provider
        self._submit_lock = RLock()

    def request(
        self,
        *,
        bounds: Mapping[str, Any],
        center: Mapping[str, Any],
        zoom: float,
        force_refresh: bool,
        analysis_period: str,
        execute: Callable[[dict[str, Any], str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        started = self.clock()
        logger.info(
            "automatic_analysis_requested",
            extra={
                "spatial_key": None,
                "spatial_strategy": self.policy.spatial_strategy,
                "zoom": zoom,
                "status": "requested",
                "cache_hit": False,
                "force_refresh": force_refresh,
            },
        )
        if not self.policy.enabled:
            logger.info(
                "automatic_analysis_rejected",
                extra={"spatial_key": None, "zoom": zoom, "status": "disabled", "cache_hit": False},
            )
            return self._response("disabled", reason="feature_disabled")
        if self.policy.spatial_strategy == "roadside_v1":
            return self._resolve_roadside(
                bounds=bounds,
                center=center,
                zoom=zoom,
                force_refresh=force_refresh,
                analysis_period=analysis_period,
                execute=execute,
                started=started,
            )
        if zoom < self.policy.min_zoom:
            logger.info(
                "automatic_analysis_rejected",
                extra={"spatial_key": None, "zoom": zoom, "status": "zoom_required", "cache_hit": False},
            )
            return self._response("zoom_required", reason="zoom_below_minimum")
        try:
            canonical = canonicalize_viewport(
                bounds=bounds, center=center, zoom=zoom, policy=self.policy
            )
        except InvalidViewportError as exc:
            reason = str(exc)
            status = (
                "zoom_required"
                if reason in {"viewport_too_large", "viewport_dimensions_too_large"}
                else "invalid_viewport"
            )
            logger.info(
                "automatic_analysis_rejected",
                extra={"spatial_key": None, "zoom": zoom, "status": status, "cache_hit": False},
            )
            return self._response(status, reason=reason)

        return self._submit_canonical(
            canonical,
            force_refresh=force_refresh,
            analysis_period=analysis_period,
            execute=execute,
            started=started,
            zoom=zoom,
        )

    def _submit_canonical(
        self,
        canonical: CanonicalViewport,
        *,
        force_refresh: bool,
        analysis_period: str,
        execute: Callable[[dict[str, Any], str], Mapping[str, Any]],
        started: datetime,
        zoom: float,
        response_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .validation.identity import geometry_fingerprint

        analysis_id = str(uuid4())
        with self._submit_lock:
            claim = self.repository.claim(
                canonical=canonical,
                analysis_period=analysis_period,
                analysis_id=analysis_id,
                now=started,
                policy=self.policy,
                force_refresh=force_refresh,
                aoi_fingerprint=geometry_fingerprint(canonical.geometry),
            )
            outcome, row = claim["outcome"], claim["row"]
            if outcome == "cache_hit":
                result = json.loads(row["result_json"])
                logger.info(
                    "automatic_analysis_cache_hit",
                    extra={"spatial_key": canonical.spatial_key, "zoom": zoom, "status": "cache_hit", "cache_hit": True, "analysis_id": row["analysis_id"]},
                )
                return self._response(
                    "cache_hit", canonical=canonical, analysis_id=row["analysis_id"],
                    cache_hit=True, result=result, cache_state="fresh",
                    expires_at=row["expires_at"],
                    extra=response_metadata,
                )
            if outcome == "in_progress":
                logger.info(
                    "automatic_analysis_deduplicated",
                    extra={"spatial_key": canonical.spatial_key, "zoom": zoom, "status": "in_progress", "cache_hit": False, "analysis_id": row["analysis_id"]},
                )
                return self._response(
                    "in_progress", canonical=canonical, analysis_id=row["analysis_id"],
                    reason="matching_analysis_in_progress", cache_state="in_progress",
                    expires_at=row["expires_at"],
                    extra=response_metadata,
                )
            if outcome == "failed":
                return self._response(
                    "failed", canonical=canonical, analysis_id=row["analysis_id"],
                    reason=str(row["error_code"] or "pipeline_failed"),
                    cache_state="failed", expires_at=row["expires_at"],
                    extra=response_metadata,
                )
            if outcome == "refresh_rate_limited":
                result = (
                    json.loads(row["result_json"])
                    if row["status"] == "completed" and row["result_json"]
                    else None
                )
                response = self._response(
                    "cache_hit" if result is not None else "failed",
                    canonical=canonical,
                    analysis_id=row["analysis_id"],
                    cache_hit=result is not None,
                    reason="force_refresh_cooldown_active",
                    result=result,
                    cache_state="fresh" if result is not None else "failed",
                    expires_at=row["expires_at"],
                    extra=response_metadata,
                )
                logger.info(
                    "automatic_analysis_cache_hit",
                    extra={
                        "spatial_key": canonical.spatial_key,
                        "zoom": zoom,
                        "status": response["status"],
                        "cache_hit": response["cache_hit"],
                        "analysis_id": row["analysis_id"],
                    },
                )
                return response
            if outcome == "capacity_reached":
                logger.info(
                    "automatic_analysis_deduplicated",
                    extra={
                        "spatial_key": canonical.spatial_key,
                        "zoom": zoom,
                        "status": "in_progress",
                        "cache_hit": False,
                        "analysis_id": None,
                    },
                )
                return self._response(
                    "in_progress", canonical=canonical,
                    reason="maximum_concurrent_analyses_reached",
                    extra=response_metadata,
                )
            try:
                self.executor.submit(
                    self._execute,
                    canonical,
                    analysis_id,
                    execute,
                    started,
                    zoom,
                )
            except Exception:
                self.repository.fail(
                    analysis_id,
                    "executor_unavailable",
                    now=self.clock(),
                    ttl_seconds=self.policy.failure_ttl_seconds,
                )
                logger.error(
                    "automatic_analysis_failed",
                    extra={
                        "spatial_key": canonical.spatial_key,
                        "zoom": zoom,
                        "status": "failed",
                        "cache_hit": False,
                        "analysis_id": analysis_id,
                        "duration_ms": 0.0,
                    },
                )
                return self._response(
                    "failed",
                    canonical=canonical,
                    analysis_id=analysis_id,
                    reason="executor_unavailable",
                    cache_state="failed",
                    extra=response_metadata,
                )
        logger.info(
            "automatic_analysis_started",
            extra={"spatial_key": canonical.spatial_key, "zoom": zoom, "status": "analysis_started", "cache_hit": False, "analysis_id": analysis_id},
        )
        return self._response(
            "analysis_started", canonical=canonical, analysis_id=analysis_id,
            analysis_started=True, cache_state="in_progress",
            expires_at=(
                _utc(started) + timedelta(seconds=self.policy.in_progress_ttl_seconds)
            ).isoformat(),
            extra=response_metadata,
        )

    def _resolve_roadside(
        self,
        *,
        bounds: Mapping[str, Any],
        center: Mapping[str, Any],
        zoom: float,
        force_refresh: bool,
        analysis_period: str,
        execute: Callable[[dict[str, Any], str], Mapping[str, Any]],
        started: datetime,
    ) -> dict[str, Any]:
        if zoom < self.policy.roadside_min_zoom:
            return self._roadside_response(
                "road_context_required", reason="zoom_below_roadside_minimum"
            )
        try:
            _, longitude, latitude, _ = _validated_viewport_values(
                bounds=bounds,
                center=center,
                zoom=zoom,
                max_zoom=self.policy.max_zoom,
            )
        except InvalidViewportError as exc:
            return self._roadside_response("invalid_viewport", reason=str(exc))
        if self.road_geometry_provider is None:
            return self._roadside_response(
                "road_geometry_unavailable", reason="road_provider_not_configured"
            )
        try:
            resolution = resolve_canonical_road_section(
                self.road_geometry_provider,
                longitude=longitude,
                latitude=latitude,
                max_distance_m=self.policy.road_snap_max_distance_m,
                ambiguity_tolerance_m=self.policy.road_ambiguity_tolerance_m,
                segment_length_m=self.policy.road_segment_length_m,
            )
        except Exception as exc:
            _, failure_detail = _sanitized_failure(exc)
            logger.error(
                "automatic_road_resolution_failed",
                extra={
                    "spatial_strategy": "roadside_v1",
                    "zoom": zoom,
                    "status": "road_geometry_unavailable",
                    "failure_category": "gis_failure",
                    "failure_exception_type": type(exc).__name__,
                    "failure_detail": failure_detail,
                },
                exc_info=True,
            )
            return self._roadside_response(
                "road_geometry_unavailable", reason="road_resolution_failed"
            )
        logger.info(
            "automatic_road_resolution_completed",
            extra={
                "spatial_strategy": "roadside_v1",
                "spatial_key": resolution.road_section_key,
                "zoom": zoom,
                "status": resolution.status,
            },
        )
        if resolution.status != "road_section_resolved":
            return self._roadside_response(
                resolution.status,
                reason=resolution.reason,
                spatial_key=resolution.road_section_key,
                canonical_bounds=resolution.canonical_bounds,
                road=resolution.road,
                spatial_strategy=resolution.spatial_strategy,
                canonical_segment_geometry=resolution.canonical_segment_geometry,
                candidate_audit=list(resolution.candidate_audit),
            )
        try:
            roadside = build_roadside_aoi(
                resolution,
                self.road_geometry_provider,
                config=RoadsideAoiConfig(
                    roadway_exclusion_m=self.policy.roadway_exclusion_m,
                    lateral_width_m=self.policy.lateral_width_m,
                    min_area_m2=self.policy.roadside_min_area_m2,
                    min_width_m=self.policy.roadside_min_width_m,
                ),
            )
        except Exception as exc:
            _, failure_detail = _sanitized_failure(exc)
            logger.error(
                "automatic_roadside_aoi_failed",
                extra={
                    "spatial_strategy": "roadside_v1",
                    "spatial_key": resolution.road_section_key,
                    "zoom": zoom,
                    "status": "roadside_geometry_invalid",
                    "failure_category": "gis_failure",
                    "failure_exception_type": type(exc).__name__,
                    "failure_detail": failure_detail,
                },
                exc_info=True,
            )
            return self._roadside_response(
                "roadside_geometry_invalid",
                reason="roadside_aoi_build_failed",
                spatial_key=resolution.road_section_key,
                canonical_bounds=resolution.canonical_bounds,
                road=resolution.road,
                spatial_strategy=resolution.spatial_strategy,
                canonical_segment_geometry=resolution.canonical_segment_geometry,
                candidate_audit=list(resolution.candidate_audit),
            )
        metadata = {
            "road": roadside.road,
            "spatial_strategy": roadside.spatial_strategy,
            "canonical_segment_geometry": roadside.centerline_geometry,
            "roadway_exclusion_m": roadside.roadway_exclusion_m,
            "lateral_width_m": roadside.lateral_width_m,
            "centerline": roadside.centerline_geometry,
            "roadway_exclusion_geometry": roadside.roadway_exclusion_geometry,
            "side_a_geometry": roadside.side_a_geometry,
            "side_b_geometry": roadside.side_b_geometry,
            "analyzed_geometry": roadside.analyzed_geometry,
            "roadside_metrics": {
                "area_m2": roadside.area_m2,
                "side_a_area_m2": roadside.side_a_area_m2,
                "side_b_area_m2": roadside.side_b_area_m2,
                "side_a_effective_width_m": roadside.side_a_effective_width_m,
                "side_b_effective_width_m": roadside.side_b_effective_width_m,
                "additional_axes_excluded": list(roadside.additional_axes_excluded),
                "minimum_centerline_distance_m": (
                    roadside.minimum_centerline_distance_m
                ),
                "roadway_exclusion_overlap_m2": (
                    roadside.roadway_exclusion_overlap_m2
                ),
            },
            "candidate_audit": list(resolution.candidate_audit),
        }
        if roadside.status != "roadside_ready" or roadside.analyzed_geometry is None:
            logger.info(
                "automatic_roadside_aoi_rejected",
                extra={
                    "spatial_strategy": "roadside_v1",
                    "spatial_key": roadside.spatial_key,
                    "zoom": zoom,
                    "status": roadside.status,
                    "failure_category": "gis_failure",
                    "failure_detail": roadside.reason,
                    "roadside_area_m2": roadside.area_m2,
                    "minimum_centerline_distance_m": (
                        roadside.minimum_centerline_distance_m
                    ),
                    "roadway_exclusion_overlap_m2": (
                        roadside.roadway_exclusion_overlap_m2
                    ),
                },
            )
            return self._roadside_response(
                roadside.status,
                reason=roadside.reason,
                spatial_key=roadside.spatial_key,
                canonical_bounds=roadside.canonical_bounds,
                **metadata,
            )
        logger.info(
            "automatic_roadside_aoi_ready",
            extra={
                "spatial_key": roadside.spatial_key,
                "road_id": roadside.road.get("id") if roadside.road else None,
                "axis_id": roadside.road.get("axis_id") if roadside.road else None,
                "section_id": roadside.road.get("section_id") if roadside.road else None,
                "section_length_m": (
                    roadside.road.get("section_length_m") if roadside.road else None
                ),
                "roadside_area_m2": roadside.area_m2,
                "roadway_exclusion_m": roadside.roadway_exclusion_m,
                "lateral_width_m": roadside.lateral_width_m,
                "minimum_centerline_distance_m": (
                    roadside.minimum_centerline_distance_m
                ),
                "roadway_exclusion_overlap_m2": (
                    roadside.roadway_exclusion_overlap_m2
                ),
                "additional_axes_excluded": len(
                    roadside.additional_axes_excluded
                ),
                "status": roadside.status,
            },
        )
        assert roadside.spatial_key is not None
        assert roadside.canonical_bounds is not None
        canonical = CanonicalViewport(
            spatial_key=roadside.spatial_key,
            geometry=roadside.analyzed_geometry,
            bounds=roadside.canonical_bounds,
            viewport_area_km2=float(roadside.area_m2 or 0.0) / 1_000_000.0,
            viewport_width_meters=float(roadside.road["section_length_m"]),
            viewport_height_meters=2.0 * roadside.lateral_width_m,
        )
        return self._submit_canonical(
            canonical,
            force_refresh=force_refresh,
            analysis_period=analysis_period,
            execute=execute,
            started=started,
            zoom=zoom,
            response_metadata=metadata,
        )

    def _execute(
        self,
        canonical: CanonicalViewport,
        analysis_id: str,
        execute: Callable[[dict[str, Any], str], Mapping[str, Any]],
        started: datetime,
        zoom: float,
    ) -> None:
        processing_stage = "pipeline_and_response_adapter"
        try:
            result = dict(execute(canonical.geometry, analysis_id))
            pipeline_result_status = str(result.get("status") or "unknown")
            finished = self.clock()
            processing_stage = "cache_persistence"
            self.repository.complete(
                analysis_id, result, now=finished, ttl_seconds=self.policy.cache_ttl_seconds
            )
            logger.info(
                "automatic_analysis_completed",
                extra={
                    "spatial_key": canonical.spatial_key, "zoom": zoom,
                    "status": "completed", "cache_hit": False, "analysis_id": analysis_id,
                    "duration_ms": (_utc(finished) - _utc(started)).total_seconds() * 1000,
                    "pipeline_result_status": pipeline_result_status,
                    "processing_stage": "completed",
                    "failure_category": None,
                },
            )
        except Exception as exc:
            finished = self.clock()
            failure_category, failure_detail = _sanitized_failure(exc)
            self.repository.fail(
                analysis_id,
                failure_category,
                now=finished,
                ttl_seconds=self.policy.failure_ttl_seconds,
            )
            logger.error(
                "automatic_analysis_failed",
                extra={
                    "spatial_key": canonical.spatial_key, "zoom": zoom,
                    "status": "failed", "cache_hit": False, "analysis_id": analysis_id,
                    "duration_ms": (_utc(finished) - _utc(started)).total_seconds() * 1000,
                    "failure_category": failure_category,
                    "failure_exception_type": type(exc).__name__,
                    "failure_detail": failure_detail,
                    "processing_stage": processing_stage,
                },
                exc_info=True,
            )

    @staticmethod
    def _response(
        status: str,
        *,
        canonical: CanonicalViewport | None = None,
        analysis_id: str | None = None,
        analysis_started: bool = False,
        cache_hit: bool = False,
        reason: str | None = None,
        result: Mapping[str, Any] | None = None,
        cache_state: str | None = None,
        expires_at: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = {
            "status": status,
            "spatial_key": canonical.spatial_key if canonical else None,
            "analysis_id": analysis_id,
            "analysis_started": analysis_started,
            "cache_hit": cache_hit,
            "automatic": True,
            "reason": reason,
            "canonical_bounds": canonical.bounds if canonical else None,
            "cache_state": cache_state,
            "expires_at": expires_at,
            "result": dict(result) if result is not None else None,
        }
        if extra:
            response.update(extra)
        return response

    @staticmethod
    def _roadside_response(
        status: str,
        *,
        reason: str | None = None,
        spatial_key: str | None = None,
        canonical_bounds: Mapping[str, float] | None = None,
        road: Mapping[str, Any] | None = None,
        spatial_strategy: Mapping[str, Any] | None = None,
        canonical_segment_geometry: Mapping[str, Any] | None = None,
        candidate_audit: list[dict[str, Any]] | None = None,
        roadway_exclusion_m: float | None = None,
        lateral_width_m: float | None = None,
        centerline: Mapping[str, Any] | None = None,
        roadway_exclusion_geometry: Mapping[str, Any] | None = None,
        side_a_geometry: Mapping[str, Any] | None = None,
        side_b_geometry: Mapping[str, Any] | None = None,
        analyzed_geometry: Mapping[str, Any] | None = None,
        roadside_metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "spatial_key": spatial_key,
            "analysis_id": None,
            "analysis_started": False,
            "cache_hit": False,
            "automatic": True,
            "reason": reason,
            "canonical_bounds": dict(canonical_bounds) if canonical_bounds else None,
            "cache_state": None,
            "expires_at": None,
            "result": None,
            "road": dict(road) if road else None,
            "spatial_strategy": dict(spatial_strategy) if spatial_strategy else None,
            "canonical_segment_geometry": (
                dict(canonical_segment_geometry)
                if canonical_segment_geometry
                else None
            ),
            "candidate_audit": candidate_audit or [],
            "roadway_exclusion_m": roadway_exclusion_m,
            "lateral_width_m": lateral_width_m,
            "centerline": dict(centerline) if centerline else None,
            "roadway_exclusion_geometry": (
                dict(roadway_exclusion_geometry)
                if roadway_exclusion_geometry
                else None
            ),
            "side_a_geometry": dict(side_a_geometry) if side_a_geometry else None,
            "side_b_geometry": dict(side_b_geometry) if side_b_geometry else None,
            "analyzed_geometry": dict(analyzed_geometry) if analyzed_geometry else None,
            "roadside_metrics": dict(roadside_metrics) if roadside_metrics else None,
        }
