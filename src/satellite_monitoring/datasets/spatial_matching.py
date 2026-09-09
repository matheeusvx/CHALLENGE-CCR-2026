"""Leitura e candidate sets D50 reutilizaveis para calibracao de campo."""

from __future__ import annotations

import csv
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform, unary_union

from ..geometry import validate_polygon_geometry

FILENAME_TARGET_DATES = {date(2026, 3, 13), date(2026, 3, 20)}
DEFAULT_DISTANCE_SCENARIOS = {"D50": 50.0}


@dataclass(frozen=True)
class ManagementFeature:
    feature_id: str
    name: str | None
    description: str | None
    properties: dict[str, str]
    geometry: Any


@dataclass(frozen=True)
class KmMarker:
    km: int
    latitude: float
    longitude: float
    source_id: str


@dataclass(frozen=True)
class CandidatePolygon:
    """Poligono candidato; nunca implica associacao ground truth."""

    feature: ManagementFeature
    distance_m: float
    semantic_match_reason: str


def load_calibration_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"sample_id", "km", "height_level", "asset_component"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Calibration dataset is missing columns: " + ", ".join(sorted(missing))
            )
        return [dict(row) for row in reader]


def _load_kml_text(path: str | Path) -> str:
    source = Path(path)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = sorted(
                name for name in archive.namelist() if name.lower().endswith(".kml")
            )
            if not names:
                raise ValueError(f"KMZ does not contain a KML document: {source}")
            return archive.read(names[0]).decode("utf-8-sig")
    return source.read_text(encoding="utf-8-sig")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_descendant(
    element: ElementTree.Element, name: str
) -> ElementTree.Element | None:
    return next((node for node in element.iter() if _local_name(node.tag) == name), None)


def _parse_coordinates(text: str | None) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []
    for token in (text or "").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            longitude, latitude = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if math.isfinite(longitude) and math.isfinite(latitude):
            coordinates.append((longitude, latitude))
    return coordinates


def _polygon_from_kml(element: ElementTree.Element) -> Polygon | None:
    outer = None
    holes: list[list[tuple[float, float]]] = []
    for boundary in element:
        coordinates_node = _first_descendant(boundary, "coordinates")
        coordinates = _parse_coordinates(
            coordinates_node.text if coordinates_node is not None else None
        )
        if len(coordinates) < 4:
            continue
        if _local_name(boundary.tag) == "outerBoundaryIs":
            outer = coordinates
        elif _local_name(boundary.tag) == "innerBoundaryIs":
            holes.append(coordinates)
    if outer is None:
        coordinates_node = _first_descendant(element, "coordinates")
        coordinates = _parse_coordinates(
            coordinates_node.text if coordinates_node is not None else None
        )
        outer = coordinates if len(coordinates) >= 4 else None
    if outer is None:
        return None
    polygon = Polygon(outer, holes)
    return polygon if not polygon.is_empty and polygon.is_valid else None


def load_management_features(path: str | Path) -> list[ManagementFeature]:
    root = ElementTree.fromstring(_load_kml_text(path))
    features: list[ManagementFeature] = []
    placemarks = (node for node in root.iter() if _local_name(node.tag) == "Placemark")
    for index, placemark in enumerate(placemarks, start=1):
        properties: dict[str, str] = {}
        for node in placemark.iter():
            node_name = _local_name(node.tag)
            if node_name == "SimpleData" and node.get("name"):
                properties[str(node.get("name"))] = (node.text or "").strip()
            elif node_name == "Data" and node.get("name"):
                value_node = _first_descendant(node, "value")
                properties[str(node.get("name"))] = (
                    (value_node.text or "").strip() if value_node is not None else ""
                )
        polygons = [
            polygon
            for node in placemark.iter()
            if _local_name(node.tag) == "Polygon"
            and (polygon := _polygon_from_kml(node)) is not None
        ]
        if not polygons:
            continue
        try:
            geometry = validate_polygon_geometry(unary_union(polygons))
        except ValueError:
            continue
        name_node = next(
            (node for node in placemark if _local_name(node.tag) == "name"), None
        )
        description_node = next(
            (node for node in placemark if _local_name(node.tag) == "description"),
            None,
        )
        features.append(
            ManagementFeature(
                feature_id=f"management_{index:04d}",
                name=(name_node.text or "").strip() if name_node is not None else None,
                description=(description_node.text or "").strip()
                if description_node is not None
                else None,
                properties=properties,
                geometry=geometry,
            )
        )
    return features


def load_km_markers(path: str | Path) -> dict[int, KmMarker]:
    root = ElementTree.fromstring(_load_kml_text(path))
    markers: dict[int, KmMarker] = {}
    placemarks = [node for node in root.iter() if _local_name(node.tag) == "Placemark"]
    for index, placemark in enumerate(placemarks, start=1):
        description_node = next(
            (node for node in placemark if _local_name(node.tag) == "description"),
            None,
        )
        description = description_node.text or "" if description_node is not None else ""
        match = re.search(r"\bkm\s*(\d+)\b", description, flags=re.IGNORECASE)
        coordinates_node = next(
            (node for node in placemark.iter() if _local_name(node.tag) == "coordinates"),
            None,
        )
        coordinates = _parse_coordinates(
            coordinates_node.text if coordinates_node is not None else None
        )
        if match is None or not coordinates:
            continue
        longitude, latitude = coordinates[0]
        km = int(match.group(1))
        markers[km] = KmMarker(
            km=km,
            latitude=latitude,
            longitude=longitude,
            source_id=f"km_marker_{index:03d}",
        )
    return markers


def _sample_reference_point(
    row: Mapping[str, str], markers: Mapping[int, KmMarker]
) -> tuple[bool, float | None, float | None]:
    method = str(row.get("coordinate_method") or "UNKNOWN")
    try:
        km = float(str(row.get("km") or ""))
    except ValueError:
        return False, None, None
    rounded = round(km)
    if abs(km - rounded) < 1e-9 and rounded in markers:
        marker = markers[rounded]
        return True, marker.latitude, marker.longitude
    if "LINEAR_INTERPOLATION_BETWEEN_KM_MARKERS" in method.upper():
        if math.floor(km) in markers and math.ceil(km) in markers:
            try:
                return True, float(str(row.get("latitude") or "")), float(
                    str(row.get("longitude") or "")
                )
            except ValueError:
                pass
    if "NEAREST_LOWER_KM_MARKER" in method.upper() and math.floor(km) in markers:
        marker = markers[math.floor(km)]
        return True, marker.latitude, marker.longitude
    return False, None, None


def _management_km(feature: ManagementFeature) -> int | None:
    try:
        numeric = float(str(feature.description or "").strip())
    except ValueError:
        return None
    integer = int(numeric)
    return integer if numeric == integer and 0 <= integer <= 9999 else None


def _sample_km_bucket(row: Mapping[str, str]) -> int | None:
    for key in ("mowing_method_km_bucket", "km"):
        try:
            return math.floor(float(str(row.get(key) or "")))
        except ValueError:
            continue
    return None


def _semantic_tokens(value: str | None) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold())
    aliases = {
        "lateral": "lateral",
        "central": "central",
        "dispositivo": "dispositivo",
        "marginal": "marginal",
        "interna": "interno",
        "interno": "interno",
        "int": "interno",
        "externa": "externo",
        "externo": "externo",
        "ext": "externo",
    }
    return {aliases[token] for token in normalized.split() if token in aliases}


def semantic_polygon_compatibility(
    sample: Mapping[str, str], feature: ManagementFeature
) -> dict[str, Any]:
    sample_bucket = _sample_km_bucket(sample)
    if sample_bucket is None or _management_km(feature) != sample_bucket:
        return {"compatible": False, "component_evidence": False, "reason": "km_mismatch"}
    sample_method = str(sample.get("mowing_method_dominant") or "").strip()
    polygon_method = str(feature.name or "").strip()
    if sample_method and polygon_method.casefold() != sample_method.casefold():
        return {
            "compatible": False,
            "component_evidence": False,
            "reason": "mowing_method_mismatch",
        }
    sample_tokens = _semantic_tokens(str(sample.get("asset_component") or ""))
    polygon_text = " ".join(
        [
            str(feature.name or ""),
            str(feature.description or ""),
            *(f"{key} {value}" for key, value in feature.properties.items()),
        ]
    )
    polygon_tokens = _semantic_tokens(polygon_text)
    component_evidence = False
    for dimension in (
        {"lateral", "central", "dispositivo", "marginal"},
        {"interno", "externo"},
    ):
        sample_values = sample_tokens & dimension
        polygon_values = polygon_tokens & dimension
        if sample_values and polygon_values:
            component_evidence = True
            if sample_values.isdisjoint(polygon_values):
                return {
                    "compatible": False,
                    "component_evidence": True,
                    "reason": "component_conflict",
                }
    return {
        "compatible": True,
        "component_evidence": component_evidence,
        "reason": "km_method_and_component_compatible"
        if component_evidence
        else "km_and_optional_mowing_method_only",
    }


def build_candidate_polygon_sets(
    samples: Sequence[Mapping[str, str]],
    features: Sequence[ManagementFeature],
    markers: Mapping[int, KmMarker],
    *,
    scenarios: Mapping[str, float] = DEFAULT_DISTANCE_SCENARIOS,
) -> dict[str, dict[str, list[CandidatePolygon]]]:
    if not markers:
        raise ValueError("No valid KM markers were found.")
    if not scenarios or any(limit <= 0 for limit in scenarios.values()):
        raise ValueError("Distance scenarios must have positive limits.")
    if max(scenarios.values()) > 100.0:
        raise ValueError("Exploratory candidate polygons cannot exceed 100 metres.")
    center_latitude = float(np.mean([marker.latitude for marker in markers.values()]))
    center_longitude = float(np.mean([marker.longitude for marker in markers.values()]))
    projected_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_latitude} +lon_0={center_longitude} "
        "+datum=WGS84 +units=m +no_defs"
    )
    transformer = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    projected_features = {
        feature.feature_id: transform(transformer.transform, feature.geometry)
        for feature in features
    }
    result: dict[str, dict[str, list[CandidatePolygon]]] = {}
    maximum_distance = max(scenarios.values())
    for sample in samples:
        sample_id = str(sample.get("sample_id") or "")
        eligible: list[CandidatePolygon] = []
        found, latitude, longitude = _sample_reference_point(sample, markers)
        if found and latitude is not None and longitude is not None:
            x_value, y_value = transformer.transform(longitude, latitude)
            point = Point(x_value, y_value)
            for feature in features:
                compatibility = semantic_polygon_compatibility(sample, feature)
                if not compatibility["compatible"]:
                    continue
                distance_m = float(projected_features[feature.feature_id].distance(point))
                if distance_m <= maximum_distance:
                    eligible.append(
                        CandidatePolygon(
                            feature=feature,
                            distance_m=distance_m,
                            semantic_match_reason=str(compatibility["reason"]),
                        )
                    )
        eligible.sort(key=lambda item: (item.distance_m, item.feature.feature_id))
        result[sample_id] = {
            scenario: [
                candidate for candidate in eligible if candidate.distance_m <= limit
            ]
            for scenario, limit in scenarios.items()
        }
    return result


def resolve_filename_target_date(sample: Mapping[str, str]) -> tuple[date, str]:
    raw_snapshot = str(sample.get("source_snapshot_date") or "").strip()
    if raw_snapshot:
        try:
            parsed = date.fromisoformat(raw_snapshot)
        except ValueError as exc:
            raise ValueError(f"Invalid source_snapshot_date: {raw_snapshot}") from exc
        if parsed not in FILENAME_TARGET_DATES:
            raise ValueError(f"Unsupported source_snapshot_date: {raw_snapshot}")
        return parsed, "source_snapshot_date"
    source_file = str(sample.get("source_file") or "")
    match = re.search(r"(2026-03-(?:13|20))", source_file)
    if match:
        return date.fromisoformat(match.group(1)), "source_file"
    raise ValueError("H_FILENAME provenance date is unavailable.")
