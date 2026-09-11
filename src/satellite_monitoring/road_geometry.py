"""Offline road geometry lookup and deterministic canonical road sections."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from numbers import Integral
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import quote

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import substring, transform
from shapely.strtree import STRtree


ROAD_SPATIAL_STRATEGY_NAME = "roadside"
ROAD_SPATIAL_STRATEGY_VERSION = "1.0"


class RoadGeometryUnavailableError(RuntimeError):
    """The configured road dataset cannot be used safely."""


@dataclass(frozen=True)
class RoadDatasetIdentity:
    provider: str
    source: str
    source_version: str | None
    fingerprint: str
    feature_count: int
    valid_feature_count: int

    @property
    def dataset_version(self) -> str:
        return f"sha256-{self.fingerprint[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source,
            "source_version": self.source_version,
            "dataset_version": self.dataset_version,
            "fingerprint": self.fingerprint,
            "feature_count": self.feature_count,
            "valid_feature_count": self.valid_feature_count,
        }


@dataclass(frozen=True)
class RoadAxis:
    axis_id: str
    road_id: str | None
    road_ref: str | None
    road_name: str | None
    concession_id: str | None
    geometry: LineString
    geometry_status: str
    source: str
    dataset_fingerprint: str
    orientation_basis: str = "unavailable"
    axis_model: str = "unknown"
    eligible: bool = False
    ineligibility_reason: str | None = None


@dataclass(frozen=True)
class RoadCandidate:
    axis: RoadAxis
    distance_m: float
    chainage_m: float
    snapped_point: Point
    metric_crs: str
    metric_geometry: LineString

    def audit_dict(self) -> dict[str, Any]:
        return {
            "axis_id": self.axis.axis_id,
            "road_id": self.axis.road_id,
            "road_ref": self.axis.road_ref,
            "distance_m": self.distance_m,
            "eligible": self.axis.eligible,
            "ineligibility_reason": self.axis.ineligibility_reason,
        }


class RoadGeometryProvider(Protocol):
    @property
    def dataset_identity(self) -> RoadDatasetIdentity: ...

    def find_candidates(
        self, longitude: float, latitude: float, *, max_distance_m: float
    ) -> Sequence[RoadCandidate]: ...

    def get_axis(self, axis_id: str) -> RoadAxis | None: ...

    def find_axes_intersecting(self, geometry: Mapping[str, Any]) -> Sequence[RoadAxis]: ...


def _metric_crs(longitude: float, latitude: float) -> CRS:
    zone = max(1, min(60, int((longitude + 180.0) // 6.0) + 1))
    return CRS.from_epsg((32600 if latitude >= 0 else 32700) + zone)


class LocalGeoJsonRoadGeometryProvider:
    """Load and spatially index one local GeoJSON road dataset once."""

    provider_name = "local_geojson"

    def __init__(self, dataset_path: str | Path) -> None:
        self.dataset_path = Path(dataset_path).expanduser().resolve()
        self._lock = RLock()
        self._loaded = False
        self._load_error: str | None = None
        self._identity: RoadDatasetIdentity | None = None
        self._axes: tuple[RoadAxis, ...] = ()
        self._geometries: tuple[LineString, ...] = ()
        self._index: STRtree | None = None
        self._geometry_indexes: dict[int, int] = {}
        self._wkb_indexes: dict[bytes, list[int]] = {}
        self._index_build_count = 0

    @property
    def index_build_count(self) -> int:
        return self._index_build_count

    @property
    def dataset_identity(self) -> RoadDatasetIdentity:
        self._ensure_loaded()
        assert self._identity is not None
        return self._identity

    @property
    def axes(self) -> tuple[RoadAxis, ...]:
        self._ensure_loaded()
        return self._axes

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                if self._load_error is not None:
                    raise RoadGeometryUnavailableError(self._load_error)
                return
            self._loaded = True
            try:
                raw = self.dataset_path.read_bytes()
                document = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(document, Mapping) or document.get("type") != "FeatureCollection":
                    raise ValueError("road_dataset_not_feature_collection")
                features = document.get("features")
                if not isinstance(features, list) or not features:
                    raise ValueError("road_dataset_has_no_features")
                fingerprint = sha256(raw).hexdigest()
                axes: list[RoadAxis] = []
                sources: set[str] = set()
                axis_ids: set[str] = set()
                for index, feature in enumerate(features):
                    if not isinstance(feature, Mapping):
                        raise ValueError("road_feature_invalid")
                    properties = feature.get("properties") or {}
                    geometry = shape(feature.get("geometry"))
                    if geometry.is_empty or geometry.geom_type != "LineString":
                        raise ValueError("road_feature_geometry_invalid")
                    axis_id = str(
                        properties.get("feature_id") or feature.get("id") or f"feature-{index}"
                    )
                    if axis_id in axis_ids:
                        raise ValueError("road_axis_id_not_unique")
                    axis_ids.add(axis_id)
                    geometry_status = str(properties.get("geometry_status") or "unknown")
                    road_id_value = properties.get("road_id")
                    road_id = str(road_id_value) if road_id_value not in {None, ""} else None
                    eligible = geometry_status == "valid" and road_id is not None
                    reason = None
                    if geometry_status != "valid":
                        reason = "geometry_status_not_valid"
                    elif road_id is None:
                        reason = "road_id_missing"
                    source = str(properties.get("source") or "unknown")
                    sources.add(source)
                    axes.append(
                        RoadAxis(
                            axis_id=axis_id,
                            road_id=road_id,
                            road_ref=(
                                str(properties["road_ref"])
                                if properties.get("road_ref") not in {None, ""}
                                else None
                            ),
                            road_name=(
                                str(properties["road_name"])
                                if properties.get("road_name") not in {None, ""}
                                else None
                            ),
                            concession_id=(
                                str(properties["concession_id"])
                                if properties.get("concession_id") not in {None, ""}
                                else None
                            ),
                            geometry=geometry,
                            geometry_status=geometry_status,
                            source=source,
                            dataset_fingerprint=fingerprint,
                            orientation_basis=str(
                                properties.get("orientation_basis") or "unavailable"
                            ),
                            axis_model=str(properties.get("axis_model") or "unknown"),
                            eligible=eligible,
                            ineligibility_reason=reason,
                        )
                    )
                geometries = tuple(axis.geometry for axis in axes)
                self._axes = tuple(axes)
                self._geometries = geometries
                self._index = STRtree(geometries)
                self._geometry_indexes = {id(geometry): i for i, geometry in enumerate(geometries)}
                for i, geometry in enumerate(geometries):
                    self._wkb_indexes.setdefault(geometry.wkb, []).append(i)
                self._index_build_count += 1
                metadata = document.get("metadata") or document.get("properties") or {}
                source_version_value = (
                    metadata.get("source_version")
                    or metadata.get("generated_at")
                    or document.get("version")
                )
                self._identity = RoadDatasetIdentity(
                    provider=self.provider_name,
                    source=next(iter(sources)) if len(sources) == 1 else "mixed",
                    source_version=(
                        str(source_version_value) if source_version_value is not None else None
                    ),
                    fingerprint=fingerprint,
                    feature_count=len(axes),
                    valid_feature_count=sum(axis.eligible for axis in axes),
                )
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                raise RoadGeometryUnavailableError(self._load_error) from exc

    def _hit_indexes(self, hits: Any) -> list[int]:
        indexes: list[int] = []
        for hit in hits:
            if isinstance(hit, Integral):
                indexes.append(int(hit))
                continue
            known = self._geometry_indexes.get(id(hit))
            if known is not None:
                indexes.append(known)
                continue
            indexes.extend(self._wkb_indexes.get(hit.wkb, []))
        return sorted(set(indexes))

    def find_candidates(
        self, longitude: float, latitude: float, *, max_distance_m: float
    ) -> tuple[RoadCandidate, ...]:
        self._ensure_loaded()
        if max_distance_m <= 0:
            raise ValueError("max_distance_m_must_be_positive")
        assert self._index is not None
        latitude_delta = max_distance_m / 110_574.0
        longitude_scale = max(0.01, math.cos(math.radians(latitude)))
        longitude_delta = max_distance_m / (111_320.0 * longitude_scale)
        envelope = box(
            longitude - longitude_delta,
            latitude - latitude_delta,
            longitude + longitude_delta,
            latitude + latitude_delta,
        )
        indexes = self._hit_indexes(self._index.query(envelope))
        query_point = Point(longitude, latitude)
        candidates: list[RoadCandidate] = []
        for index in indexes:
            axis = self._axes[index]
            axis_centroid = axis.geometry.centroid
            metric_crs = _metric_crs(axis_centroid.x, axis_centroid.y)
            forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
            reverse = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
            metric_point = transform(forward.transform, query_point)
            metric_line = transform(forward.transform, axis.geometry)
            distance = float(metric_line.distance(metric_point))
            if distance > max_distance_m + 1e-6:
                continue
            chainage = float(metric_line.project(metric_point))
            snapped_metric = metric_line.interpolate(chainage)
            candidates.append(
                RoadCandidate(
                    axis=axis,
                    distance_m=distance,
                    chainage_m=chainage,
                    snapped_point=transform(reverse.transform, snapped_metric),
                    metric_crs=metric_crs.to_string(),
                    metric_geometry=metric_line,
                )
            )
        return tuple(sorted(candidates, key=lambda item: (item.distance_m, item.axis.axis_id)))

    def get_axis(self, axis_id: str) -> RoadAxis | None:
        self._ensure_loaded()
        return next((axis for axis in self._axes if axis.axis_id == axis_id), None)

    def find_axes_intersecting(
        self, geometry: Mapping[str, Any]
    ) -> tuple[RoadAxis, ...]:
        self._ensure_loaded()
        assert self._index is not None
        query_geometry = shape(geometry)
        indexes = self._hit_indexes(self._index.query(query_geometry))
        return tuple(
            self._axes[index]
            for index in indexes
            if self._axes[index].geometry.intersects(query_geometry)
        )


@dataclass(frozen=True)
class RoadSectionResolution:
    status: str
    reason: str | None
    road_section_key: str | None
    road: dict[str, Any] | None
    spatial_strategy: dict[str, Any] | None
    canonical_segment_geometry: dict[str, Any] | None
    canonical_bounds: dict[str, float] | None
    candidate_audit: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "road_section_key": self.road_section_key,
            "road": self.road,
            "spatial_strategy": self.spatial_strategy,
            "canonical_segment_geometry": self.canonical_segment_geometry,
            "canonical_bounds": self.canonical_bounds,
            "candidate_audit": list(self.candidate_audit),
        }


def resolve_canonical_road_section(
    provider: RoadGeometryProvider,
    *,
    longitude: float,
    latitude: float,
    max_distance_m: float = 50.0,
    ambiguity_tolerance_m: float = 10.0,
    segment_length_m: float = 200.0,
) -> RoadSectionResolution:
    """Snap to one trustworthy axis and return a versioned canonical section."""
    try:
        identity = provider.dataset_identity
        candidates = list(
            provider.find_candidates(
                longitude, latitude, max_distance_m=max_distance_m
            )
        )
    except RoadGeometryUnavailableError:
        return RoadSectionResolution(
            "road_geometry_unavailable",
            "road_dataset_unavailable",
            None,
            None,
            None,
            None,
            None,
        )
    audit = tuple(candidate.audit_dict() for candidate in candidates[:5])
    if not candidates:
        return RoadSectionResolution(
            "road_not_found", "no_road_within_snap_distance", None, None,
            {"name": ROAD_SPATIAL_STRATEGY_NAME, "version": ROAD_SPATIAL_STRATEGY_VERSION,
             "dataset": identity.to_dict()}, None, None, audit,
        )
    nearest = candidates[0]
    plausible = [
        candidate
        for candidate in candidates
        if candidate.distance_m - nearest.distance_m <= ambiguity_tolerance_m + 1e-6
    ]
    if not nearest.axis.eligible or any(not item.axis.eligible for item in plausible):
        return RoadSectionResolution(
            "road_geometry_unreliable", "nearest_road_identity_not_reliable", None,
            None,
            {"name": ROAD_SPATIAL_STRATEGY_NAME, "version": ROAD_SPATIAL_STRATEGY_VERSION,
             "dataset": identity.to_dict()}, None, None, audit,
        )
    if len({candidate.axis.axis_id for candidate in plausible}) > 1:
        return RoadSectionResolution(
            "road_ambiguous", "multiple_plausible_road_axes", None, None,
            {"name": ROAD_SPATIAL_STRATEGY_NAME, "version": ROAD_SPATIAL_STRATEGY_VERSION,
             "dataset": identity.to_dict()}, None, None, audit,
        )
    if segment_length_m <= 0:
        raise ValueError("segment_length_m_must_be_positive")
    line = nearest.metric_geometry
    section_count = max(1, int(math.ceil(line.length / segment_length_m)))
    section_index = min(
        section_count - 1,
        max(0, int(math.floor(nearest.chainage_m / segment_length_m))),
    )
    start_m = section_index * segment_length_m
    end_m = min(start_m + segment_length_m, float(line.length))
    segment_metric = substring(line, start_m, end_m)
    if segment_metric.geom_type != "LineString" or segment_metric.is_empty:
        return RoadSectionResolution(
            "road_geometry_unreliable", "canonical_section_invalid", None, None,
            {"name": ROAD_SPATIAL_STRATEGY_NAME, "version": ROAD_SPATIAL_STRATEGY_VERSION,
             "dataset": identity.to_dict()}, None, None, audit,
        )
    reverse = Transformer.from_crs(nearest.metric_crs, "EPSG:4326", always_xy=True)
    segment_wgs84 = transform(reverse.transform, segment_metric)
    west, south, east, north = map(float, segment_wgs84.bounds)
    encoded_axis = quote(nearest.axis.axis_id, safe="-._~")
    segment_length_value = float(segment_length_m)
    profile_value = (
        str(int(segment_length_value))
        if segment_length_value.is_integer()
        else f"{segment_length_value:g}"
    )
    profile = f"segment-{profile_value}m"
    key = (
        f"roadside:v1:{identity.provider}:{identity.dataset_version}:{encoded_axis}:"
        f"{profile}:section:{section_index:06d}"
    )
    section_length = end_m - start_m
    road = {
        "id": nearest.axis.road_id,
        "ref": nearest.axis.road_ref,
        "name": nearest.axis.road_name,
        "axis_id": nearest.axis.axis_id,
        "section_id": f"section_{section_index:06d}",
        "section_index": section_index,
        "section_start_m": start_m,
        "section_end_m": end_m,
        "section_length_m": section_length,
        "configured_segment_length_m": segment_length_m,
        "short_section": section_length + 1e-6 < segment_length_m,
        "snap_distance_m": nearest.distance_m,
        "snapped_point": mapping(nearest.snapped_point),
        "orientation_basis": nearest.axis.orientation_basis,
        "axis_model": nearest.axis.axis_model,
        "geometry_status": nearest.axis.geometry_status,
        "source": nearest.axis.source,
        "concession_id": nearest.axis.concession_id,
    }
    return RoadSectionResolution(
        "road_section_resolved",
        None,
        key,
        road,
        {
            "name": ROAD_SPATIAL_STRATEGY_NAME,
            "version": ROAD_SPATIAL_STRATEGY_VERSION,
            "profile": profile,
            "dataset": identity.to_dict(),
        },
        mapping(segment_wgs84),
        {"west": west, "south": south, "east": east, "north": north},
        audit,
    )
