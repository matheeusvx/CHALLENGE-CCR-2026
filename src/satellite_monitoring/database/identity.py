"""Stable operational identities for persisted analysis subjects."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from shapely import normalize, set_precision, to_wkb

from ..geometry import extract_polygon_geometry

# Seven decimal degrees is roughly one centimetre at the equator. This removes
# irrelevant serialization noise while remaining far below Sentinel pixel size.
GEOMETRY_IDENTITY_GRID_DEGREES = 1e-7


@dataclass(frozen=True)
class AnalysisIdentity:
    subject_kind: str
    subject_key: str
    spatial_key: str | None = None
    road_id: str | None = None
    road_ref: str | None = None
    road_name: str | None = None
    axis_id: str | None = None
    section_id: str | None = None
    section_index: int | None = None


def geometry_fingerprint(geometry_document: Mapping[str, Any]) -> str:
    """Hash Polygon/MultiPolygon topology, ignoring GeoJSON wrapper properties.

    Shapely extracts/unions supported geometries, snaps coordinates to a 1e-7
    degree grid, and normalizes ring orientation, ring start vertices, and
    multipart ordering before stable little-endian 2D WKB serialization.
    """

    geometry, _ = extract_polygon_geometry(dict(geometry_document))
    canonical = normalize(
        set_precision(geometry, GEOMETRY_IDENTITY_GRID_DEGREES, mode="valid_output")
    )
    digest = sha256(
        to_wkb(canonical, output_dimension=2, byte_order=1, include_srid=False)
    ).hexdigest()
    return digest


def geometry_identity(geometry_document: Mapping[str, Any]) -> AnalysisIdentity:
    return AnalysisIdentity(
        subject_kind="geometry",
        subject_key=f"geometry:v1:{geometry_fingerprint(geometry_document)}",
    )


def road_section_identity(
    spatial_key: str, road: Mapping[str, Any]
) -> AnalysisIdentity:
    key = str(spatial_key).strip()
    if not key:
        raise ValueError("roadside identity requires spatial_key")
    return AnalysisIdentity(
        subject_kind="road_section",
        subject_key=key,
        spatial_key=key,
        road_id=_optional_text(road.get("id")),
        road_ref=_optional_text(road.get("ref")),
        road_name=_optional_text(road.get("name")),
        axis_id=_optional_text(road.get("axis_id")),
        section_id=_optional_text(road.get("section_id")),
        section_index=_optional_int(road.get("section_index")),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
