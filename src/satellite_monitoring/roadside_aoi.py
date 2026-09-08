"""Build conservative roadside polygons around a canonical road section."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from urllib.parse import quote

from pyproj import Transformer
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union
from shapely.validation import make_valid

from .road_geometry import RoadGeometryProvider, RoadSectionResolution, _metric_crs


ROADSIDE_AOI_ALGORITHM_VERSION = "1.2"


@dataclass(frozen=True)
class RoadsideAoiConfig:
    roadway_exclusion_m: float = 25.0
    lateral_width_m: float = 40.0
    min_area_m2: float = 5_000.0
    min_width_m: float = 20.0

    def __post_init__(self) -> None:
        if self.roadway_exclusion_m <= 0:
            raise ValueError("roadway_exclusion_m_must_be_positive")
        if self.lateral_width_m <= 0:
            raise ValueError("lateral_width_m_must_be_positive")
        if self.min_area_m2 <= 0:
            raise ValueError("min_area_m2_must_be_positive")
        if self.min_width_m <= 0:
            raise ValueError("min_width_m_must_be_positive")


@dataclass(frozen=True)
class RoadsideAoiResult:
    status: str
    reason: str | None
    spatial_key: str | None
    road: dict[str, Any] | None
    spatial_strategy: dict[str, Any] | None
    centerline_geometry: dict[str, Any] | None
    side_a_geometry: dict[str, Any] | None
    side_b_geometry: dict[str, Any] | None
    analyzed_geometry: dict[str, Any] | None
    canonical_bounds: dict[str, float] | None
    roadway_exclusion_m: float
    lateral_width_m: float
    area_m2: float | None = None
    side_a_area_m2: float | None = None
    side_b_area_m2: float | None = None
    side_a_effective_width_m: float | None = None
    side_b_effective_width_m: float | None = None
    additional_axes_excluded: tuple[str, ...] = ()
    roadway_exclusion_geometry: dict[str, Any] | None = None
    minimum_centerline_distance_m: float | None = None
    roadway_exclusion_overlap_m2: float | None = None


def _polygonal(geometry: Any) -> Polygon | MultiPolygon:
    repaired = make_valid(geometry)
    if repaired.geom_type in {"Polygon", "MultiPolygon"}:
        return repaired
    if isinstance(repaired, GeometryCollection):
        polygons = [
            part
            for part in repaired.geoms
            if part.geom_type in {"Polygon", "MultiPolygon"} and not part.is_empty
        ]
        if polygons:
            merged = unary_union(polygons)
            if merged.geom_type in {"Polygon", "MultiPolygon"}:
                return merged
    return Polygon()


def _strategy_profile(
    resolution: RoadSectionResolution, config: RoadsideAoiConfig
) -> tuple[str, str]:
    assert resolution.road is not None
    profile_document = {
        "spatial_strategy": "roadside_v1",
        "spatial_strategy_version": "1.0",
        "aoi_algorithm_version": ROADSIDE_AOI_ALGORITHM_VERSION,
        "segment_length_m": resolution.road["configured_segment_length_m"],
        "roadway_exclusion_m": config.roadway_exclusion_m,
        "lateral_width_m": config.lateral_width_m,
        "min_area_m2": config.min_area_m2,
        "min_width_m": config.min_width_m,
    }
    fingerprint = sha256(
        json.dumps(
            profile_document, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return fingerprint[:12], fingerprint


def build_roadside_aoi(
    resolution: RoadSectionResolution,
    provider: RoadGeometryProvider,
    *,
    config: RoadsideAoiConfig | None = None,
) -> RoadsideAoiResult:
    """Create two axis-relative road margins and their combined scientific AOI."""
    selected = config or RoadsideAoiConfig()
    if (
        resolution.status != "road_section_resolved"
        or resolution.road is None
        or resolution.canonical_segment_geometry is None
        or resolution.spatial_strategy is None
    ):
        return RoadsideAoiResult(
            "roadside_geometry_invalid",
            "road_section_not_resolved",
            None,
            resolution.road,
            resolution.spatial_strategy,
            resolution.canonical_segment_geometry,
            None,
            None,
            None,
            None,
            selected.roadway_exclusion_m,
            selected.lateral_width_m,
        )

    segment_wgs84 = shape(resolution.canonical_segment_geometry)
    if segment_wgs84.is_empty or segment_wgs84.geom_type != "LineString":
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "canonical_centerline_invalid", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
        )
    centroid = segment_wgs84.centroid
    metric_crs = _metric_crs(centroid.x, centroid.y)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    reverse = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    segment_metric = transform(forward.transform, segment_wgs84)
    segment_length_m = float(segment_metric.length)
    if segment_length_m <= 0:
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "canonical_centerline_has_no_length", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
        )

    outer_distance = selected.roadway_exclusion_m + selected.lateral_width_m
    # A flat inner cap shares the exact centerline endpoint with GEOS' outer
    # single-sided buffer. On some real diagonals that overlay leaves a
    # near-zero-area spike reaching the endpoint even though overlap area is
    # zero. Round caps extend the exclusion beyond both endpoints and remove
    # that numerical contact without changing the configured lateral width.
    roadway_exclusion = _polygonal(
        segment_metric.buffer(selected.roadway_exclusion_m, cap_style=1)
    )
    side_a = segment_metric.buffer(
        outer_distance, single_sided=True, cap_style=2
    ).difference(roadway_exclusion)
    side_b = segment_metric.buffer(
        -outer_distance, single_sided=True, cap_style=2
    ).difference(roadway_exclusion)
    side_a = _polygonal(side_a)
    side_b = _polygonal(side_b)
    if side_a.is_empty or side_b.is_empty:
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "roadside_side_empty", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
        )

    tentative = _polygonal(unary_union([side_a, side_b]))
    additional_axes: list[str] = []
    try:
        intersecting_axes = provider.find_axes_intersecting(
            mapping(transform(reverse.transform, tentative))
        )
    except Exception:
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "additional_road_check_failed", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
        )
    selected_axis_id = str(resolution.road["axis_id"])
    for axis in intersecting_axes:
        if axis.axis_id == selected_axis_id:
            continue
        metric_axis = transform(forward.transform, axis.geometry)
        exclusion = metric_axis.buffer(selected.roadway_exclusion_m)
        if not exclusion.intersects(tentative):
            continue
        if not axis.eligible:
            return RoadsideAoiResult(
                "roadside_geometry_invalid", "additional_road_axis_unreliable", None,
                resolution.road, resolution.spatial_strategy,
                resolution.canonical_segment_geometry, None, None, None, None,
                selected.roadway_exclusion_m, selected.lateral_width_m,
            )
        side_a = _polygonal(side_a.difference(exclusion))
        side_b = _polygonal(side_b.difference(exclusion))
        additional_axes.append(axis.axis_id)

    both = _polygonal(unary_union([side_a, side_b]))
    if (
        side_a.is_empty
        or side_b.is_empty
        or both.is_empty
        or not side_a.is_valid
        or not side_b.is_valid
        or not both.is_valid
    ):
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "roadside_polygon_invalid", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
            additional_axes_excluded=tuple(sorted(additional_axes)),
        )

    exclusion_overlap_m2 = float(both.intersection(roadway_exclusion).area)
    minimum_centerline_distance_m = float(both.distance(segment_metric))
    overlap_tolerance_m2 = max(1e-6, float(both.area) * 1e-9)
    if exclusion_overlap_m2 > overlap_tolerance_m2:
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "roadway_exclusion_overlap", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
            additional_axes_excluded=tuple(sorted(additional_axes)),
            minimum_centerline_distance_m=minimum_centerline_distance_m,
            roadway_exclusion_overlap_m2=exclusion_overlap_m2,
        )

    side_a_area = float(side_a.area)
    side_b_area = float(side_b.area)
    area = float(both.area)
    side_a_width = side_a_area / segment_length_m
    side_b_width = side_b_area / segment_length_m
    if (
        area + 1e-6 < selected.min_area_m2
        or side_a_width + 1e-6 < selected.min_width_m
        or side_b_width + 1e-6 < selected.min_width_m
    ):
        return RoadsideAoiResult(
            "roadside_too_small", "roadside_quality_gates_not_met", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
            area_m2=area, side_a_area_m2=side_a_area, side_b_area_m2=side_b_area,
            side_a_effective_width_m=side_a_width,
            side_b_effective_width_m=side_b_width,
            additional_axes_excluded=tuple(sorted(additional_axes)),
        )

    # Projection round-trips can introduce sub-nanometre ring intersections on
    # curves. Repair each output in its published CRS, then derive ``both`` from
    # those repaired sides so the API never receives an invalid GeoJSON.
    side_a_wgs84 = _polygonal(transform(reverse.transform, side_a))
    side_b_wgs84 = _polygonal(transform(reverse.transform, side_b))
    roadway_exclusion_wgs84 = _polygonal(
        transform(reverse.transform, roadway_exclusion)
    )
    both_wgs84 = _polygonal(unary_union([side_a_wgs84, side_b_wgs84]))
    if (
        side_a_wgs84.is_empty
        or side_b_wgs84.is_empty
        or both_wgs84.is_empty
        or roadway_exclusion_wgs84.is_empty
    ):
        return RoadsideAoiResult(
            "roadside_geometry_invalid", "roadside_projection_invalid", None,
            resolution.road, resolution.spatial_strategy,
            resolution.canonical_segment_geometry, None, None, None, None,
            selected.roadway_exclusion_m, selected.lateral_width_m,
            additional_axes_excluded=tuple(sorted(additional_axes)),
        )
    west, south, east, north = map(float, both_wgs84.bounds)
    short_profile, full_profile = _strategy_profile(resolution, selected)
    dataset = resolution.spatial_strategy["dataset"]
    encoded_axis = quote(selected_axis_id, safe="-._~")
    key = (
        f"roadside:v1:{dataset['provider']}:{dataset['dataset_version']}:"
        f"{encoded_axis}:section:{int(resolution.road['section_index']):06d}:"
        f"side:both:{short_profile}"
    )
    strategy = {
        **resolution.spatial_strategy,
        "profile": "roadside-both",
        "aoi_algorithm_version": ROADSIDE_AOI_ALGORITHM_VERSION,
        "profile_fingerprint": full_profile,
    }
    return RoadsideAoiResult(
        "roadside_ready", None, key, resolution.road, strategy,
        mapping(segment_wgs84), mapping(side_a_wgs84), mapping(side_b_wgs84),
        mapping(both_wgs84),
        {"west": west, "south": south, "east": east, "north": north},
        selected.roadway_exclusion_m, selected.lateral_width_m,
        area_m2=area, side_a_area_m2=side_a_area, side_b_area_m2=side_b_area,
        side_a_effective_width_m=side_a_width,
        side_b_effective_width_m=side_b_width,
        additional_axes_excluded=tuple(sorted(additional_axes)),
        roadway_exclusion_geometry=mapping(roadway_exclusion_wgs84),
        minimum_centerline_distance_m=minimum_centerline_distance_m,
        roadway_exclusion_overlap_m2=exclusion_overlap_m2,
    )
