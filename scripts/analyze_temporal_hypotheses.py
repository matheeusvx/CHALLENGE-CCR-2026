"""Ferramenta offline/exploratoria para calibracao temporal com Sentinel-2.

O script orquestra os componentes existentes de consulta, RED/NIR, NDVI, SCL e
qualidade, mas deliberadamente nao chama a logica operacional de recommendation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from xml.etree import ElementTree

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon, mapping
from shapely.ops import transform, unary_union

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.geometry import validate_polygon_geometry
from src.satellite_monitoring.indices import (
    InsufficientValidPixelsError,
    analyze_ndvi,
)
from src.satellite_monitoring.quality import assess_scene_quality
from src.satellite_monitoring.raster_processing import read_scene_bands
from src.satellite_monitoring.stac_client import NoScenesError, Scene, search_scenes

RANDOM_SEED = 20260328
WINDOW_DAYS = 15
MAX_CANDIDATE_SCENES = 100
HYPOTHESES: dict[str, date] = {
    "H_A": date(2025, 3, 28),
    "H_B1": date(2026, 3, 13),
    "H_B2": date(2026, 3, 20),
}
HEIGHT_LABELS = {
    1: "<10 cm",
    2: "10-30 cm",
    3: ">30 cm",
}
OUTPUT_FIELDS = (
    "sample_id",
    "km",
    "height_class",
    "hypothesis",
    "target_date",
    "sentinel_scene_date",
    "temporal_delta_days",
    "temporal_abs_delta_days",
    "item_id",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "ndvi_min",
    "ndvi_max",
    "valid_pixel_percentage",
    "valid_pixel_count",
    "aoi_coverage_percentage",
    "cloud_cover",
    "scene_quality_score",
    "quality_status",
    "scene_selection_status",
    "spatial_match_status",
    "geometry_source",
    "geometry_feature_id",
    "geometry_match_method",
    "geometry_match_distance_m",
    "spatial_match_reason",
)
SUMMARY_FIELDS = (
    "hypothesis",
    "height_class",
    "n_total",
    "n_with_valid_scene",
    "valid_scene_percentage",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "ndvi_q1",
    "ndvi_q3",
    "temporal_delta_median_days",
)
SPATIAL_DIAGNOSTIC_FIELDS = (
    "sample_id",
    "height_class",
    "km",
    "sample_component",
    "km_marker_found",
    "km_marker_latitude",
    "km_marker_longitude",
    "km_marker_method",
    "nearest_polygon_1_id",
    "nearest_polygon_1_type",
    "nearest_polygon_1_distance_m",
    "nearest_polygon_2_id",
    "nearest_polygon_2_type",
    "nearest_polygon_2_distance_m",
    "nearest_polygon_3_id",
    "nearest_polygon_3_type",
    "nearest_polygon_3_distance_m",
    "candidate_polygon_count",
    "spatial_match_status",
    "spatial_match_reason",
)


@dataclass(frozen=True)
class ManagementFeature:
    feature_id: str
    name: str | None
    description: str | None
    properties: dict[str, str]
    geometry: Any


@dataclass(frozen=True)
class SpatialMatch:
    status: str
    geometry: Any | None
    geometry_source: str
    method: str | None
    reason: str
    candidate_count: int
    feature_id: str | None = None
    distance_m: float | None = None


@dataclass(frozen=True)
class KmMarker:
    km: int
    latitude: float
    longitude: float
    source_id: str


def hypothesis_window(target_date: date) -> tuple[date, date]:
    """Retorna a janela inclusiva de +/- 15 dias."""
    delta = timedelta(days=WINDOW_DAYS)
    return target_date - delta, target_date + delta


def _parse_scene_date(record: Mapping[str, Any]) -> date | None:
    raw = record.get("datetime") or record.get("sentinel_scene_date")
    if isinstance(raw, datetime):
        return raw.date()
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _finite_rank_value(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def select_best_scene(
    records: Sequence[Mapping[str, Any]],
    target_date: date,
) -> dict[str, Any] | None:
    """Seleciona apenas cenas aceitas usando a ordem deterministica solicitada."""
    start_date, end_date = hypothesis_window(target_date)
    eligible: list[tuple[Mapping[str, Any], date]] = []
    for record in records:
        scene_date = _parse_scene_date(record)
        if (
            record.get("accepted_for_timeseries") is True
            and scene_date is not None
            and start_date <= scene_date <= end_date
        ):
            eligible.append((record, scene_date))
    if not eligible:
        return None

    def rank(entry: tuple[Mapping[str, Any], date]) -> tuple[Any, ...]:
        record, scene_date = entry
        return (
            abs((scene_date - target_date).days),
            -_finite_rank_value(record.get("scene_quality_score"), -math.inf),
            -_finite_rank_value(record.get("valid_pixel_percentage"), -math.inf),
            _finite_rank_value(record.get("cloud_cover"), math.inf),
            str(record.get("item_id") or ""),
        )

    selected, scene_date = min(eligible, key=rank)
    result = dict(selected)
    result["sentinel_scene_date"] = scene_date.isoformat()
    result["temporal_delta_days"] = (scene_date - target_date).days
    result["temporal_abs_delta_days"] = abs((scene_date - target_date).days)
    return result


def _height_level(row: Mapping[str, str]) -> int | None:
    try:
        value = int(float(str(row.get("height_level") or "")))
    except ValueError:
        return None
    return value if value in HEIGHT_LABELS else None


def load_calibration_rows(path: str | Path) -> list[dict[str, str]]:
    dataset_path = Path(path)
    with dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"sample_id", "km", "height_level", "asset_component"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Calibration dataset is missing columns: " + ", ".join(sorted(missing))
            )
        return [dict(row) for row in reader]


def _spatial_group_key(row: Mapping[str, str]) -> tuple[int, float | str]:
    level = _height_level(row)
    assert level is not None
    try:
        km_value: float | str = round(float(row.get("km") or ""), 6)
    except ValueError:
        km_value = str(row.get("sample_id") or "")
    return level, km_value


def stratified_sample(
    rows: Sequence[Mapping[str, str]],
    sample_size: int,
    *,
    seed: int = RANDOM_SEED,
) -> list[dict[str, str]]:
    """Amostra classes equilibradas e percorre KMs distintos antes de repeti-los."""
    if sample_size <= 0:
        raise ValueError("sample_size must be greater than zero.")
    eligible = [
        dict(row)
        for row in rows
        if _height_level(row) is not None
        and str(row.get("sample_id") or "").strip()
        and str(row.get("usable_for_height_classification") or "YES").upper() == "YES"
    ]
    if sample_size > len(eligible):
        raise ValueError("sample_size exceeds eligible calibration records.")

    base, remainder = divmod(sample_size, len(HEIGHT_LABELS))
    quotas = {
        level: base + (1 if index < remainder else 0)
        for index, level in enumerate(sorted(HEIGHT_LABELS))
    }
    selected: list[dict[str, str]] = []
    for level in sorted(HEIGHT_LABELS):
        candidates = [row for row in eligible if _height_level(row) == level]
        if len(candidates) < quotas[level]:
            raise ValueError(f"Height class {level} has fewer rows than requested quota.")
        groups: dict[float | str, list[dict[str, str]]] = {}
        for row in sorted(candidates, key=lambda item: str(item["sample_id"])):
            groups.setdefault(_spatial_group_key(row)[1], []).append(row)
        rng = random.Random(seed + level)
        keys = sorted(groups, key=str)
        rng.shuffle(keys)
        for values in groups.values():
            rng.shuffle(values)
        class_selection: list[dict[str, str]] = []
        while len(class_selection) < quotas[level]:
            progressed = False
            for key in keys:
                if groups[key] and len(class_selection) < quotas[level]:
                    class_selection.append(groups[key].pop())
                    progressed = True
            if not progressed:
                break
        selected.extend(class_selection)
    for position, row in enumerate(selected, start=1):
        row["selection_order"] = str(position)
    return selected


def _load_kml_text(path: str | Path) -> str:
    source = Path(path)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = sorted(name for name in archive.namelist() if name.lower().endswith(".kml"))
            if not names:
                raise ValueError(f"KMZ does not contain a KML document: {source}")
            return archive.read(names[0]).decode("utf-8-sig")
    return source.read_text(encoding="utf-8-sig")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_descendant(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
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
        boundary_name = _local_name(boundary.tag)
        coordinates_node = _first_descendant(boundary, "coordinates")
        coordinates = _parse_coordinates(
            coordinates_node.text if coordinates_node is not None else None
        )
        if len(coordinates) < 4:
            continue
        if boundary_name == "outerBoundaryIs":
            outer = coordinates
        elif boundary_name == "innerBoundaryIs":
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
    for index, placemark in enumerate(
        (node for node in root.iter() if _local_name(node.tag) == "Placemark"),
        start=1,
    ):
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
            (node for node in placemark if _local_name(node.tag) == "description"), None
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


def load_km_marker_count(path: str | Path) -> int:
    root = ElementTree.fromstring(_load_kml_text(path))
    return sum(1 for node in root.iter() if _local_name(node.tag) == "Point")


def load_km_markers(path: str | Path) -> dict[int, KmMarker]:
    root = ElementTree.fromstring(_load_kml_text(path))
    markers: dict[int, KmMarker] = {}
    placemarks = [node for node in root.iter() if _local_name(node.tag) == "Placemark"]
    for index, placemark in enumerate(placemarks, start=1):
        description_node = next(
            (node for node in placemark if _local_name(node.tag) == "description"), None
        )
        description = (
            (description_node.text or "") if description_node is not None else ""
        )
        match = re.search(r"\bkm\s*(\d+)\b", description, flags=re.IGNORECASE)
        coordinates_node = next(
            (
                node
                for node in placemark.iter()
                if _local_name(node.tag) == "coordinates"
            ),
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
    row: Mapping[str, str],
    markers: Mapping[int, KmMarker],
) -> tuple[bool, float | None, float | None, str]:
    method = str(row.get("coordinate_method") or "UNKNOWN")
    try:
        km = float(str(row.get("km") or ""))
    except ValueError:
        return False, None, None, method
    rounded = round(km)
    if abs(km - rounded) < 1e-9 and rounded in markers:
        marker = markers[rounded]
        return True, marker.latitude, marker.longitude, "KM_MARKER_EXACT"
    if "LINEAR_INTERPOLATION_BETWEEN_KM_MARKERS" in method.upper():
        lower = math.floor(km)
        upper = math.ceil(km)
        if lower in markers and upper in markers:
            try:
                return (
                    True,
                    float(str(row.get("latitude") or "")),
                    float(str(row.get("longitude") or "")),
                    "LINEAR_INTERPOLATION_BETWEEN_KM_MARKERS",
                )
            except ValueError:
                pass
    if "NEAREST_LOWER_KM_MARKER" in method.upper() and math.floor(km) in markers:
        marker = markers[math.floor(km)]
        return True, marker.latitude, marker.longitude, "NEAREST_LOWER_KM_MARKER"
    return False, None, None, method


def _management_km(feature: ManagementFeature) -> int | None:
    raw = str(feature.description or "").strip()
    try:
        numeric = float(raw)
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
    sample: Mapping[str, str],
    feature: ManagementFeature,
) -> dict[str, Any]:
    """Compara apenas atributos realmente presentes, sem inferir lado pela geometria."""
    sample_bucket = _sample_km_bucket(sample)
    polygon_km = _management_km(feature)
    if sample_bucket is None or polygon_km != sample_bucket:
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
    component_dimensions = (
        {"lateral", "central", "dispositivo", "marginal"},
        {"interno", "externo"},
    )
    component_evidence = False
    for dimension in component_dimensions:
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
        "reason": (
            "km_method_and_component_compatible"
            if component_evidence
            else "km_and_optional_mowing_method_only"
        ),
    }


def _distance_distribution(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if not array.size:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}
    return {
        "n": int(array.size),
        "min": round(float(np.min(array)), 3),
        "q1": round(float(np.quantile(array, 0.25)), 3),
        "median": round(float(np.median(array)), 3),
        "q3": round(float(np.quantile(array, 0.75)), 3),
        "max": round(float(np.max(array)), 3),
    }


def build_spatial_diagnostics(
    samples: Sequence[Mapping[str, str]],
    features: Sequence[ManagementFeature],
    markers: Mapping[int, KmMarker],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not markers:
        raise ValueError("No valid KM markers were found.")
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
    rows: list[dict[str, Any]] = []
    nearest_distances: list[float] = []
    candidate_distances: list[float] = []
    candidate_counts: list[int] = []
    for sample in samples:
        found, latitude, longitude, marker_method = _sample_reference_point(sample, markers)
        distances: list[tuple[float, ManagementFeature]] = []
        if found and latitude is not None and longitude is not None:
            x_value, y_value = transformer.transform(longitude, latitude)
            point = Point(x_value, y_value)
            distances = sorted(
                [
                    (
                    float(projected_features[feature.feature_id].distance(point)),
                    feature,
                    )
                    for feature in features
                ],
                key=lambda item: (item[0], item[1].feature_id),
            )
        nearest = distances[:3]
        if nearest:
            nearest_distances.append(nearest[0][0])
        plausible: list[tuple[float, ManagementFeature, dict[str, Any]]] = []
        for distance, feature in distances:
            compatibility = semantic_polygon_compatibility(sample, feature)
            if compatibility["compatible"]:
                plausible.append((distance, feature, compatibility))
                candidate_distances.append(distance)
        candidate_counts.append(len(plausible))
        component_supported = [
            item for item in plausible if item[2]["component_evidence"]
        ]
        if len(component_supported) == 1:
            status = "SPATIAL_MATCH_UNRESOLVED"
            reason = (
                "One semantic candidate has component evidence, but no distance "
                "limit has been justified yet."
            )
        elif len(component_supported) > 1:
            status = "SPATIAL_MATCH_AMBIGUOUS"
            reason = "Multiple polygons remain compatible with explicit component attributes."
        elif len(plausible) > 1:
            status = "SPATIAL_MATCH_AMBIGUOUS"
            reason = (
                "Multiple polygons match KM and optional mowing method, but the KML "
                "does not identify lateral/central/device or internal/external."
            )
        elif len(plausible) == 1:
            status = "SPATIAL_MATCH_UNRESOLVED"
            reason = (
                "The only KM/mowing-method candidate has no component or side metadata."
            )
        else:
            status = "SPATIAL_MATCH_UNRESOLVED"
            reason = "No polygon matches the available KM and mowing-method attributes."
        row: dict[str, Any] = {
            "sample_id": sample.get("sample_id"),
            "height_class": _height_level(sample),
            "km": sample.get("km"),
            "sample_component": sample.get("asset_component"),
            "km_marker_found": found,
            "km_marker_latitude": latitude,
            "km_marker_longitude": longitude,
            "km_marker_method": marker_method,
            "candidate_polygon_count": len(plausible),
            "spatial_match_status": status,
            "spatial_match_reason": reason,
        }
        for position in range(3):
            prefix = f"nearest_polygon_{position + 1}"
            if position < len(nearest):
                distance, feature = nearest[position]
                row[f"{prefix}_id"] = feature.feature_id
                row[f"{prefix}_type"] = feature.name
                row[f"{prefix}_distance_m"] = round(distance, 3)
            else:
                row[f"{prefix}_id"] = None
                row[f"{prefix}_type"] = None
                row[f"{prefix}_distance_m"] = None
        rows.append(row)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "projected_crs": projected_crs.to_string(),
        "status_counts": {
            status: sum(row["spatial_match_status"] == status for row in rows)
            for status in (
                "SPATIAL_MATCH_RESOLVED",
                "SPATIAL_MATCH_AMBIGUOUS",
                "SPATIAL_MATCH_UNRESOLVED",
            )
        },
        "nearest_polygon_1_distance_m": _distance_distribution(nearest_distances),
        "all_semantic_candidate_distances_m": _distance_distribution(candidate_distances),
        "candidate_polygon_count": _distance_distribution(candidate_counts),
        "semantic_evidence": {
            "management_description": "integer KM bucket",
            "management_name": "mowing method",
            "component_or_side_metadata_available": False,
        },
        "sentinel_2_queried": False,
    }
    return rows, summary


def run_spatial_diagnostics(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    samples = stratified_sample(load_calibration_rows(dataset), sample_size)
    features = load_management_features(management_kmz)
    markers = load_km_markers(km_markers_kmz)
    rows, summary = build_spatial_diagnostics(samples, features, markers)
    diagnostics_path = output / "spatial_match_diagnostics.csv"
    summary_path = output / "spatial_match_diagnostics_summary.json"
    _write_csv(diagnostics_path, rows, SPATIAL_DIAGNOSTIC_FIELDS)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "diagnostics_csv": str(diagnostics_path),
        "summary_json": str(summary_path),
        **summary,
    }


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _property_values(feature: ManagementFeature, aliases: set[str]) -> list[str]:
    normalized_aliases = {_normalized_key(alias) for alias in aliases}
    return [
        value
        for key, value in feature.properties.items()
        if _normalized_key(key) in normalized_aliases
    ]


def _float_equal(left: str, right: str, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _geometry_distance_meters(
    row: Mapping[str, str], geometry: Any
) -> float | None:
    try:
        latitude = float(str(row.get("latitude") or ""))
        longitude = float(str(row.get("longitude") or ""))
    except ValueError:
        return None
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} "
        "+datum=WGS84 +units=m +no_defs"
    )
    transformer = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    projected = transform(transformer.transform, geometry)
    distance = float(projected.distance(Point(0.0, 0.0)))
    return round(distance, 3)


def resolve_spatial_match(
    row: Mapping[str, str],
    features: Sequence[ManagementFeature],
    management_source: str | Path,
) -> SpatialMatch:
    """Resolve apenas chaves explicitas ou ponto nao derivado do eixo e unico."""
    source = str(management_source)
    sample_id = str(row.get("sample_id") or "").strip()
    explicit = [
        feature
        for feature in features
        if sample_id
        and sample_id
        in _property_values(feature, {"sample_id", "sampleid", "id_amostra"})
    ]
    if len(explicit) == 1:
        feature = explicit[0]
        return SpatialMatch(
            "SPATIAL_MATCH_RESOLVED",
            feature.geometry,
            f"{source}#{feature.feature_id}",
            "explicit_sample_id",
            "Unique management polygon linked by sample_id.",
            1,
            feature.feature_id,
            _geometry_distance_meters(row, feature.geometry),
        )
    if len(explicit) > 1:
        return SpatialMatch(
            "SPATIAL_MATCH_AMBIGUOUS",
            None,
            source,
            None,
            "Multiple management polygons share the sample_id.",
            len(explicit),
        )

    component = str(row.get("asset_component") or "").strip().casefold()
    km = str(row.get("km") or "").strip()
    composite: list[ManagementFeature] = []
    for feature in features:
        components = _property_values(
            feature, {"asset_component", "componente", "faixa", "segmento"}
        )
        kms = _property_values(feature, {"km", "quilometro", "quilometragem"})
        if (
            component
            and km
            and any(value.strip().casefold() == component for value in components)
            and any(_float_equal(value, km) for value in kms)
        ):
            composite.append(feature)
    if len(composite) == 1:
        feature = composite[0]
        return SpatialMatch(
            "SPATIAL_MATCH_RESOLVED",
            feature.geometry,
            f"{source}#{feature.feature_id}",
            "explicit_component_and_km",
            "Unique management polygon linked by component and KM fields.",
            1,
            feature.feature_id,
            _geometry_distance_meters(row, feature.geometry),
        )
    if len(composite) > 1:
        return SpatialMatch(
            "SPATIAL_MATCH_AMBIGUOUS",
            None,
            source,
            None,
            "Multiple management polygons match component and KM.",
            len(composite),
        )

    try:
        latitude = float(str(row.get("latitude") or ""))
        longitude = float(str(row.get("longitude") or ""))
        point = Point(longitude, latitude)
        containing = [feature for feature in features if feature.geometry.covers(point)]
    except ValueError:
        containing = []
    coordinate_method = str(row.get("coordinate_method") or "").upper()
    axis_derived = any(
        marker in coordinate_method
        for marker in ("KM_MARKER", "INTERPOLATION_BETWEEN_KM", "NEAREST_LOWER_KM")
    )
    if axis_derived:
        plausible = [
            feature
            for feature in features
            if semantic_polygon_compatibility(row, feature)["compatible"]
        ]
        component_supported = [
            feature
            for feature in plausible
            if semantic_polygon_compatibility(row, feature)["component_evidence"]
        ]
        if len(component_supported) > 1:
            return SpatialMatch(
                "SPATIAL_MATCH_AMBIGUOUS",
                None,
                source,
                None,
                "Multiple polygons remain compatible with explicit component attributes.",
                len(component_supported),
            )
        if len(component_supported) == 1:
            return SpatialMatch(
                "SPATIAL_MATCH_UNRESOLVED",
                None,
                source,
                None,
                "A component-compatible polygon exists, but no technical distance limit is justified.",
                1,
            )
        if len(plausible) > 1:
            return SpatialMatch(
                "SPATIAL_MATCH_AMBIGUOUS",
                None,
                source,
                None,
                "Multiple polygons match KM and optional mowing method, but component/side metadata is absent.",
                len(plausible),
            )
        if len(plausible) == 1:
            return SpatialMatch(
                "SPATIAL_MATCH_UNRESOLVED",
                None,
                source,
                None,
                "The only KM/mowing-method candidate has no component or side metadata.",
                1,
            )
        return SpatialMatch(
            "SPATIAL_MATCH_UNRESOLVED",
            None,
            source,
            None,
            "No polygon matches the available KM and mowing-method attributes.",
            0,
        )
    if len(containing) == 1:
        feature = containing[0]
        return SpatialMatch(
            "SPATIAL_MATCH_RESOLVED",
            feature.geometry,
            f"{source}#{feature.feature_id}",
            "unique_non_axis_point_containment",
            "Unique polygon contains a coordinate not identified as road-axis-derived.",
            1,
            feature.feature_id,
            0.0,
        )
    reason = (
        "No management polygon contains the non-axis coordinate."
        if not containing
        else "Multiple management polygons contain the coordinate."
    )
    return SpatialMatch(
        "SPATIAL_MATCH_AMBIGUOUS" if len(containing) > 1 else "SPATIAL_MATCH_UNRESOLVED",
        None,
        source,
        None,
        reason,
        len(containing),
    )


def _process_scene(scene: Scene, aoi_geojson: dict[str, Any], config: MonitoringConfig) -> dict[str, Any]:
    record = scene.to_record()
    record.update(
        {
            "ndvi_mean": None,
            "ndvi_median": None,
            "ndvi_std": None,
            "ndvi_min": None,
            "ndvi_max": None,
            "valid_pixel_percentage": 0.0,
            "valid_pixel_count": 0,
            "aoi_coverage_percentage": None,
            "scene_quality_score": None,
            "quality_status": "unknown",
            "accepted_for_timeseries": False,
            "processing_status": "processing",
        }
    )
    try:
        raster_data = read_scene_bands(scene.item, aoi_geojson)
        statistics = None
        try:
            _, statistics = analyze_ndvi(
                raster_data.red,
                raster_data.nir,
                raster_data.valid_mask,
                raster_data.total_pixel_count,
            )
        except InsufficientValidPixelsError:
            pass
        valid_count = statistics.valid_pixel_count if statistics else 0
        valid_percentage = statistics.valid_pixel_percentage if statistics else 0.0
        assessment = assess_scene_quality(
            valid_pixel_percentage=valid_percentage,
            valid_pixel_count=valid_count,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
            min_valid_pixel_count=config.min_valid_pixel_count,
            medium_threshold=config.medium_quality_threshold,
            high_threshold=config.high_quality_threshold,
            has_scl=raster_data.scl_asset is not None,
            scl_class_percentages=raster_data.scl_class_percentages,
            cloud_cover=record.get("cloud_cover"),
            max_cloud_cover=config.max_cloud_cover,
            aoi_coverage_percentage=raster_data.aoi_coverage_percentage,
            min_aoi_coverage_percentage=config.min_aoi_coverage_percentage,
            partial_raster_coverage=raster_data.partial_raster_coverage,
            has_valid_ndvi_pixels=statistics is not None,
            include_low_quality_scenes=config.include_low_quality_scenes,
        )
        record.update(
            {
                "ndvi_mean": statistics.mean if statistics else None,
                "ndvi_median": statistics.median if statistics else None,
                "ndvi_std": statistics.std if statistics else None,
                "ndvi_min": statistics.minimum if statistics else None,
                "ndvi_max": statistics.maximum if statistics else None,
                "valid_pixel_percentage": valid_percentage,
                "valid_pixel_count": valid_count,
                "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                "scene_quality_score": assessment.scene_quality_score,
                "quality_status": assessment.quality_status,
                "quality_reasons": list(assessment.quality_reasons),
                "accepted_for_timeseries": assessment.accepted_for_timeseries,
                "processing_status": "processed",
            }
        )
    except Exception as exc:
        record.update(
            {
                "processing_status": "failed",
                "quality_reasons": ["processing_error"],
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    return record


def _query_and_process_scenes(
    geometry: Any,
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], str | None]:
    aoi_geojson = mapping(geometry)
    config = MonitoringConfig(
        geometry=aoi_geojson,
        start_date=start_date,
        end_date=end_date,
        max_scenes=MAX_CANDIDATE_SCENES,
        max_candidate_scenes=MAX_CANDIDATE_SCENES,
        scene_order="oldest",
    )
    try:
        result = search_scenes(config, aoi_geojson)
    except NoScenesError:
        return [], None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    return [_process_scene(scene, aoi_geojson, config) for scene in result.scenes], None


def _base_output_row(
    sample: Mapping[str, str],
    hypothesis: str,
    target_date: date,
    spatial_match: SpatialMatch,
) -> dict[str, Any]:
    return {
        "sample_id": sample.get("sample_id"),
        "km": sample.get("km"),
        "height_class": _height_level(sample),
        "hypothesis": hypothesis,
        "target_date": target_date.isoformat(),
        "sentinel_scene_date": None,
        "temporal_delta_days": None,
        "temporal_abs_delta_days": None,
        "item_id": None,
        "ndvi_mean": None,
        "ndvi_median": None,
        "ndvi_std": None,
        "ndvi_min": None,
        "ndvi_max": None,
        "valid_pixel_percentage": None,
        "valid_pixel_count": None,
        "aoi_coverage_percentage": None,
        "cloud_cover": None,
        "scene_quality_score": None,
        "quality_status": None,
        "scene_selection_status": (
            "NOT_EVALUATED_SPATIAL_UNRESOLVED"
            if spatial_match.status == "SPATIAL_MATCH_UNRESOLVED"
            else "NOT_EVALUATED_SPATIAL_AMBIGUOUS"
            if spatial_match.status == "SPATIAL_MATCH_AMBIGUOUS"
            else "NO_VALID_SCENE"
        ),
        "spatial_match_status": spatial_match.status,
        "geometry_source": spatial_match.geometry_source,
        "geometry_feature_id": spatial_match.feature_id,
        "geometry_match_method": spatial_match.method,
        "geometry_match_distance_m": spatial_match.distance_m,
        "spatial_match_reason": spatial_match.reason,
    }


def make_output_row(
    sample: Mapping[str, str],
    hypothesis: str,
    target_date: date,
    spatial_match: SpatialMatch,
    scene: Mapping[str, Any] | None,
) -> dict[str, Any]:
    row = _base_output_row(sample, hypothesis, target_date, spatial_match)
    if scene is None:
        return row
    row.update(
        {
            key: scene.get(key)
            for key in (
                "sentinel_scene_date",
                "temporal_delta_days",
                "temporal_abs_delta_days",
                "item_id",
                "ndvi_mean",
                "ndvi_median",
                "ndvi_std",
                "ndvi_min",
                "ndvi_max",
                "valid_pixel_percentage",
                "valid_pixel_count",
                "aoi_coverage_percentage",
                "cloud_cover",
                "scene_quality_score",
                "quality_status",
            )
        }
    )
    row["scene_selection_status"] = "VALID_SCENE_SELECTED"
    return row


def analyze_sample(
    sample: Mapping[str, str],
    spatial_match: SpatialMatch,
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]] = (
        _query_and_process_scenes
    ),
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if spatial_match.status != "SPATIAL_MATCH_RESOLVED" or spatial_match.geometry is None:
        return (
            [
                make_output_row(sample, name, target, spatial_match, None)
                for name, target in HYPOTHESES.items()
            ],
            [],
        )

    errors: list[dict[str, str]] = []
    a_start, a_end = hypothesis_window(HYPOTHESES["H_A"])
    b1_start, _ = hypothesis_window(HYPOTHESES["H_B1"])
    _, b2_end = hypothesis_window(HYPOTHESES["H_B2"])
    a_records, a_error = query_scenes(spatial_match.geometry, a_start, a_end)
    b_records, b_error = query_scenes(spatial_match.geometry, b1_start, b2_end)
    if a_error:
        errors.append({"sample_id": str(sample["sample_id"]), "window": "H_A", "error": a_error})
    if b_error:
        errors.append({"sample_id": str(sample["sample_id"]), "window": "H_B_SHARED", "error": b_error})
    rows = []
    for name, target in HYPOTHESES.items():
        records = a_records if name == "H_A" else b_records
        rows.append(
            make_output_row(
                sample,
                name,
                target,
                spatial_match,
                select_best_scene(records, target),
            )
        )
    return rows, errors


def summarize_by_class(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hypothesis in HYPOTHESES:
        for height_class in sorted(HEIGHT_LABELS):
            group = [
                row
                for row in rows
                if row.get("hypothesis") == hypothesis
                and row.get("height_class") == height_class
            ]
            valid = [
                row
                for row in group
                if row.get("scene_selection_status") == "VALID_SCENE_SELECTED"
                and row.get("ndvi_median") is not None
            ]
            ndvi = np.asarray([float(row["ndvi_median"]) for row in valid], dtype=float)
            deltas = np.asarray(
                [abs(float(row["temporal_delta_days"])) for row in valid], dtype=float
            )
            summaries.append(
                {
                    "hypothesis": hypothesis,
                    "height_class": height_class,
                    "n_total": len(group),
                    "n_with_valid_scene": len(valid),
                    "valid_scene_percentage": (
                        len(valid) / len(group) * 100.0 if group else 0.0
                    ),
                    "ndvi_mean": float(np.mean(ndvi)) if ndvi.size else None,
                    "ndvi_median": float(np.median(ndvi)) if ndvi.size else None,
                    "ndvi_std": (
                        float(np.std(ndvi, ddof=1)) if ndvi.size >= 2 else None
                    ),
                    "ndvi_q1": float(np.quantile(ndvi, 0.25)) if ndvi.size else None,
                    "ndvi_q3": float(np.quantile(ndvi, 0.75)) if ndvi.size else None,
                    "temporal_delta_median_days": (
                        float(np.median(deltas)) if deltas.size else None
                    ),
                }
            )
    return summaries


def _spearman(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("ndvi_median") is not None]
    if len(valid) < 3 or len({row.get("height_class") for row in valid}) < 2:
        return {"status": "INSUFFICIENT_SAMPLE", "n": len(valid), "correlation": None, "p_value": None}
    try:
        from scipy.stats import spearmanr

        result = spearmanr(
            [int(row["height_class"]) for row in valid],
            [float(row["ndvi_median"]) for row in valid],
        )
        correlation = float(result.statistic)
        p_value = float(result.pvalue)
        if not math.isfinite(correlation):
            raise ValueError("Spearman correlation is not finite.")
        return {
            "status": "CALCULATED",
            "n": len(valid),
            "correlation": correlation,
            "p_value": p_value if math.isfinite(p_value) else None,
        }
    except (ImportError, ValueError):
        return {"status": "INSUFFICIENT_SAMPLE", "n": len(valid), "correlation": None, "p_value": None}


def _auc(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("ndvi_median") is not None]
    labels = [1 if int(row["height_class"]) == 3 else 0 for row in valid]
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives < 2 or negatives < 2:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "n": len(valid),
            "positive_n": positives,
            "negative_n": negatives,
            "auc": None,
        }
    try:
        from sklearn.metrics import roc_auc_score

        auc = float(
            roc_auc_score(labels, [float(row["ndvi_median"]) for row in valid])
        )
        return {
            "status": "CALCULATED",
            "n": len(valid),
            "positive_n": positives,
            "negative_n": negatives,
            "auc": auc,
        }
    except (ImportError, ValueError):
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "n": len(valid),
            "positive_n": positives,
            "negative_n": negatives,
            "auc": None,
        }


def build_comparison(
    rows: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    selected_samples: Sequence[Mapping[str, str]],
    spatial_matches: Mapping[str, SpatialMatch],
    *,
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    management_feature_count: int,
    km_marker_count: int,
    errors: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    hypotheses: dict[str, Any] = {}
    for name, target in HYPOTHESES.items():
        hypothesis_rows = [row for row in rows if row.get("hypothesis") == name]
        valid_count = sum(
            row.get("scene_selection_status") == "VALID_SCENE_SELECTED"
            for row in hypothesis_rows
        )
        resolved = sum(
            spatial_matches[str(sample["sample_id"])].status
            == "SPATIAL_MATCH_RESOLVED"
            for sample in selected_samples
        )
        ambiguous = sum(
            spatial_matches[str(sample["sample_id"])].status
            == "SPATIAL_MATCH_AMBIGUOUS"
            for sample in selected_samples
        )
        hypotheses[name] = {
            "target_date": target.isoformat(),
            "window_start": hypothesis_window(target)[0].isoformat(),
            "window_end": hypothesis_window(target)[1].isoformat(),
            "valid_scene_coverage": {
                "n_total": len(hypothesis_rows),
                "n_with_valid_scene": valid_count,
                "percentage": (
                    valid_count / len(hypothesis_rows) * 100.0
                    if hypothesis_rows
                    else 0.0
                ),
            },
            "statistics_by_height_class": [
                dict(summary)
                for summary in summaries
                if summary.get("hypothesis") == name
            ],
            "spearman_height_class_vs_ndvi_median": _spearman(hypothesis_rows),
            "auc_height_gt_30cm_vs_ndvi_median": _auc(hypothesis_rows),
            "spatial_matches": {
                "resolved": resolved,
                "ambiguous": ambiguous,
                "unresolved": len(selected_samples) - resolved - ambiguous,
            },
            "exploratory_support_status": "NOT_RANKED_INSUFFICIENT_VALID_DATA",
        }

    scores: dict[str, float] = {}
    for name, data in hypotheses.items():
        spearman = data["spearman_height_class_vs_ndvi_median"]["correlation"]
        auc = data["auc_height_gt_30cm_vs_ndvi_median"]["auc"]
        if spearman is not None and auc is not None:
            scores[name] = (float(spearman) + (2.0 * float(auc) - 1.0)) / 2.0
    if scores:
        best_score = max(scores.values())
        winners = [name for name, score in scores.items() if score == best_score]
        for name in scores:
            hypotheses[name]["exploratory_support_score"] = scores[name]
            hypotheses[name]["exploratory_support_status"] = (
                "STRONGER_EXPLORATORY_SUPPORT"
                if len(winners) == 1 and name == winners[0]
                else "COMPARATIVE_EXPLORATORY_SUPPORT"
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "offline_exploratory_temporal_hypothesis_calibration",
        "documented_date_confirmation": False,
        "date_confirmation_note": (
            "Hypotheses are exploratory and no result confirms a field observation date."
        ),
        "selection_rule": (
            "accepted_for_timeseries, minimum absolute temporal delta, maximum "
            "scene_quality_score, maximum valid_pixel_percentage, minimum "
            "cloud_cover, then item_id"
        ),
        "temporal_delta_definition": (
            "Signed calendar days: sentinel_scene_date - target_date; ranking uses absolute value."
        ),
        "random_seed": RANDOM_SEED,
        "inputs": {
            "dataset": str(dataset),
            "management_kmz": str(management_kmz),
            "km_markers_kmz": str(km_markers_kmz),
            "management_feature_count": management_feature_count,
            "km_marker_count": km_marker_count,
        },
        "sample": {
            "requested_size": len(selected_samples),
            "selected_by_class": {
                str(level): [
                    str(row["sample_id"])
                    for row in selected_samples
                    if _height_level(row) == level
                ]
                for level in sorted(HEIGHT_LABELS)
            },
        },
        "hypotheses": hypotheses,
        "execution_errors": list(errors),
        "limitations": [
            "The management source is used only when record-to-polygon association is unequivocal.",
            "Road-axis KM marker points are never converted into vegetation AOIs.",
            "Spearman and AUC are exploratory association metrics, not system accuracy.",
        ],
    }


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_boxplots(rows: Sequence[Mapping[str, Any]], output_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: list[Path] = []
    for hypothesis, target in HYPOTHESES.items():
        groups = [
            [
                float(row["ndvi_median"])
                for row in rows
                if row.get("hypothesis") == hypothesis
                and row.get("height_class") == level
                and row.get("ndvi_median") is not None
            ]
            for level in sorted(HEIGHT_LABELS)
        ]
        figure, axis = plt.subplots(figsize=(8, 5))
        if any(groups):
            axis.boxplot(groups, tick_labels=[HEIGHT_LABELS[level] for level in sorted(HEIGHT_LABELS)])
            axis.set_ylabel("NDVI median")
        else:
            axis.text(
                0.5,
                0.5,
                "No valid Sentinel-2 scenes for resolved vegetation polygons",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_xticks([])
            axis.set_yticks([])
        axis.set_title(f"{hypothesis} — target {target.isoformat()}")
        axis.set_xlabel("Field vegetation height class")
        figure.tight_layout()
        path = output_dir / f"ndvi_boxplot_{hypothesis}.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)
    return paths


def run_analysis(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    calibration_rows = load_calibration_rows(dataset)
    selected = stratified_sample(calibration_rows, sample_size)
    management_features = load_management_features(management_kmz)
    marker_count = load_km_marker_count(km_markers_kmz)
    matches = {
        str(row["sample_id"]): resolve_spatial_match(
            row, management_features, management_kmz
        )
        for row in selected
    }
    result_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for sample in selected:
        rows, sample_errors = analyze_sample(
            sample, matches[str(sample["sample_id"])]
        )
        result_rows.extend(rows)
        errors.extend(sample_errors)
    summaries = summarize_by_class(result_rows)
    comparison = build_comparison(
        result_rows,
        summaries,
        selected,
        matches,
        dataset=dataset,
        management_kmz=management_kmz,
        km_markers_kmz=km_markers_kmz,
        management_feature_count=len(management_features),
        km_marker_count=marker_count,
        errors=errors,
    )

    results_path = output / "temporal_hypotheses_results.csv"
    summary_path = output / "temporal_hypotheses_summary.csv"
    samples_path = output / "selected_samples.csv"
    comparison_path = output / "temporal_hypotheses_comparison.json"
    _write_csv(results_path, result_rows, OUTPUT_FIELDS)
    _write_csv(summary_path, summaries, SUMMARY_FIELDS)
    sample_fields = list(selected[0]) if selected else ["sample_id"]
    _write_csv(samples_path, selected, sample_fields)
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    chart_paths = _write_boxplots(result_rows, output)
    return {
        "results_csv": str(results_path),
        "summary_csv": str(summary_path),
        "selected_samples_csv": str(samples_path),
        "comparison_json": str(comparison_path),
        "charts": [str(path) for path in chart_paths],
        "selected_by_class": comparison["sample"]["selected_by_class"],
        "spatial_matches_resolved": sum(
            match.status == "SPATIAL_MATCH_RESOLVED" for match in matches.values()
        ),
        "spatial_matches_ambiguous": sum(
            match.status == "SPATIAL_MATCH_AMBIGUOUS" for match in matches.values()
        ),
        "spatial_matches_unresolved": sum(
            match.status == "SPATIAL_MATCH_UNRESOLVED" for match in matches.values()
        ),
        "valid_sentinel_scenes": sum(
            row["scene_selection_status"] == "VALID_SCENE_SELECTED"
            for row in result_rows
        ),
        "output_rows": len(result_rows),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare unconfirmed field-date hypotheses against Sentinel-2."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--management-kmz", required=True, type=Path)
    parser.add_argument("--km-markers-kmz", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--spatial-diagnostics-only",
        action="store_true",
        help="Generate spatial diagnostics without querying Sentinel-2.",
    )
    arguments = parser.parse_args()
    if arguments.sample_size <= 0:
        parser.error("--sample-size must be greater than zero")
    return arguments


def main() -> int:
    arguments = _arguments()
    runner = run_spatial_diagnostics if arguments.spatial_diagnostics_only else run_analysis
    result = runner(
        arguments.dataset,
        arguments.management_kmz,
        arguments.km_markers_kmz,
        arguments.sample_size,
        arguments.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
