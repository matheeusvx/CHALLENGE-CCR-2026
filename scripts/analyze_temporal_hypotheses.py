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
import time
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
DISTANCE_SCENARIOS: dict[str, float] = {
    "D20": 20.0,
    "D50": 50.0,
    "D100": 100.0,
}
EXPANDED_SPATIAL_SCENARIOS: dict[str, float] = {
    "D50": 50.0,
    "D100": 100.0,
}
EXPANDED_HYPOTHESES = ("H_EMBEDDED", "H_FILENAME")
EMBEDDED_TARGET_DATE = date(2025, 3, 28)
FILENAME_TARGET_DATES = {date(2026, 3, 13), date(2026, 3, 20)}
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 20260404
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
CANDIDATE_NDVI_FIELDS = (
    "sample_id",
    "km",
    "height_class",
    "sample_component",
    "hypothesis",
    "target_date",
    "distance_scenario",
    "distance_limit_m",
    "candidate_polygon_id",
    "candidate_polygon_type",
    "candidate_polygon_km",
    "candidate_distance_m",
    "semantic_match_reason",
    "spatial_uncertainty_status",
    "ground_truth_assignment",
    "sentinel_scene_date",
    "temporal_delta_days",
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
)
SAMPLE_UNCERTAINTY_FIELDS = (
    "sample_id",
    "km",
    "height_class",
    "sample_component",
    "hypothesis",
    "target_date",
    "distance_scenario",
    "distance_limit_m",
    "candidate_polygon_count",
    "candidate_with_valid_scene_count",
    "ndvi_candidate_min",
    "ndvi_candidate_q1",
    "ndvi_candidate_median",
    "ndvi_candidate_q3",
    "ndvi_candidate_max",
    "ndvi_candidate_std",
    "ndvi_candidate_iqr",
    "ndvi_candidate_range",
    "spatial_uncertainty_status",
    "ground_truth_assignment",
)
HEIGHT_CLASS_CANDIDATE_FIELDS = (
    "hypothesis",
    "distance_scenario",
    "height_class",
    "n_total",
    "n_with_candidates",
    "n_with_valid_sentinel",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "ndvi_q1",
    "ndvi_q3",
)
EXPANDED_CANDIDATE_FIELDS = (
    "sample_id",
    "source_file",
    "source_snapshot_date",
    "height_class",
    "km",
    "sample_component",
    "hypothesis",
    "target_date",
    "target_date_source",
    "distance_scenario",
    "distance_limit_m",
    "candidate_geometry_id",
    "candidate_polygon_type",
    "candidate_distance_m",
    "semantic_match_reason",
    "ground_truth_assignment",
    "sentinel_item_id",
    "sentinel_scene_date",
    "temporal_delta_days",
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
    "shared_geometry_count",
    "shared_scene_count",
    "shared_km_geometry_count",
)
EXPANDED_SAMPLE_FIELDS = (
    "sample_id",
    "source_file",
    "source_snapshot_date",
    "height_class",
    "km",
    "sample_component",
    "hypothesis",
    "target_date",
    "target_date_source",
    "distance_scenario",
    "distance_limit_m",
    "candidate_polygon_count",
    "candidate_with_valid_scene_count",
    "candidate_geometry_id",
    "sentinel_item_id",
    "shared_geometry_count",
    "shared_scene_count",
    "shared_km_geometry_count",
    "ndvi_candidate_min",
    "ndvi_candidate_q1",
    "ndvi_candidate_median",
    "ndvi_candidate_q3",
    "ndvi_candidate_max",
    "ndvi_candidate_std",
    "ndvi_candidate_iqr",
    "ndvi_candidate_range",
    "spatial_uncertainty_status",
    "ground_truth_assignment",
)
EXPANDED_HEIGHT_CLASS_FIELDS = (
    "hypothesis",
    "distance_scenario",
    "height_class",
    "n_selected",
    "n_with_candidates",
    "n_with_valid_sentinel",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "ndvi_q1",
    "ndvi_q3",
)
BINARY_HEIGHT_FIELDS = (
    "hypothesis",
    "distance_scenario",
    "binary_height_group",
    "n_selected",
    "n_with_candidates",
    "n_with_valid_sentinel",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "ndvi_q1",
    "ndvi_q3",
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


@dataclass(frozen=True)
class CandidatePolygon:
    """Poligono elegivel em um cenario, sem implicar associacao ground truth."""

    feature: ManagementFeature
    distance_m: float
    semantic_match_reason: str


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


def build_candidate_polygon_sets(
    samples: Sequence[Mapping[str, str]],
    features: Sequence[ManagementFeature],
    markers: Mapping[int, KmMarker],
    *,
    scenarios: Mapping[str, float] = DISTANCE_SCENARIOS,
) -> dict[str, dict[str, list[CandidatePolygon]]]:
    """Monta conjuntos cumulativos por distancia sem escolher um poligono unico."""
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
        found, latitude, longitude, _ = _sample_reference_point(sample, markers)
        if found and latitude is not None and longitude is not None:
            x_value, y_value = transformer.transform(longitude, latitude)
            point = Point(x_value, y_value)
            for feature in features:
                compatibility = semantic_polygon_compatibility(sample, feature)
                if not compatibility["compatible"]:
                    continue
                distance_m = float(
                    projected_features[feature.feature_id].distance(point)
                )
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
                candidate
                for candidate in eligible
                if candidate.distance_m <= distance_limit
            ]
            for scenario, distance_limit in scenarios.items()
        }
    return result


def resolve_filename_target_date(
    sample: Mapping[str, str],
) -> tuple[date, str]:
    """Resolve H_FILENAME pela proveniencia mais explicita disponivel."""
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


def expanded_target_date(sample: Mapping[str, str], hypothesis: str) -> date:
    if hypothesis == "H_EMBEDDED":
        return EMBEDDED_TARGET_DATE
    if hypothesis == "H_FILENAME":
        return resolve_filename_target_date(sample)[0]
    raise ValueError(f"Unknown expanded hypothesis: {hypothesis}")


def _expanded_eligible_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    eligible: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for source_row in rows:
        row = dict(source_row)
        if (
            _height_level(row) is None
            or not str(row.get("sample_id") or "").strip()
            or str(row.get("usable_for_height_classification") or "YES").upper()
            != "YES"
        ):
            continue
        try:
            resolve_filename_target_date(row)
        except ValueError as exc:
            excluded.append(
                {"sample_id": str(row.get("sample_id") or ""), "reason": str(exc)}
            )
            continue
        eligible.append(row)
    return eligible, excluded


def build_expanded_preflight(
    rows: Sequence[Mapping[str, str]],
    features: Sequence[ManagementFeature],
    markers: Mapping[int, KmMarker],
) -> tuple[
    dict[str, Any],
    list[dict[str, str]],
    dict[str, dict[str, list[CandidatePolygon]]],
]:
    eligible, excluded = _expanded_eligible_rows(rows)
    candidate_sets = build_candidate_polygon_sets(
        eligible, features, markers, scenarios=EXPANDED_SPATIAL_SCENARIOS
    )
    by_class: dict[str, Any] = {}
    for height_class in sorted(HEIGHT_LABELS):
        class_rows = [row for row in eligible if _height_level(row) == height_class]
        snapshots = sorted(
            {str(row.get("source_snapshot_date") or "UNKNOWN") for row in class_rows}
        )
        by_class[str(height_class)] = {
            "height_label": HEIGHT_LABELS[height_class],
            "total_records_available": len(class_rows),
            "scenarios": {
                scenario: {
                    "records_with_candidate_polygon": sum(
                        bool(candidate_sets[str(row["sample_id"])][scenario])
                        for row in class_rows
                    ),
                    "snapshot_distribution": {
                        snapshot: {
                            "total_records": sum(
                                str(row.get("source_snapshot_date") or "UNKNOWN")
                                == snapshot
                                for row in class_rows
                            ),
                            "records_with_candidate_polygon": sum(
                                str(row.get("source_snapshot_date") or "UNKNOWN")
                                == snapshot
                                and bool(
                                    candidate_sets[str(row["sample_id"])][scenario]
                                )
                                for row in class_rows
                            ),
                        }
                        for snapshot in snapshots
                    },
                }
                for scenario in EXPANDED_SPATIAL_SCENARIOS
            },
        }
    balanced_per_class = {
        scenario: min(
            by_class[str(level)]["scenarios"][scenario][
                "records_with_candidate_polygon"
            ]
            for level in HEIGHT_LABELS
        )
        for scenario in EXPANDED_SPATIAL_SCENARIOS
    }
    selected_per_class = min(30, balanced_per_class["D50"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "expanded_temporal_calibration_preflight",
        "sentinel_2_queried": False,
        "documented_date_confirmation": False,
        "filename_hypothesis_provenance_field": "source_snapshot_date",
        "spatial_scenarios_m": dict(EXPANDED_SPATIAL_SCENARIOS),
        "spatial_scenarios_are_operational_thresholds": False,
        "selection_basis": "D50 primary exploratory scenario",
        "eligible_record_count": len(eligible),
        "excluded_for_missing_or_invalid_provenance": excluded,
        "availability_by_height_class": by_class,
        "maximum_balanced_per_class_by_scenario": balanced_per_class,
        "recommended_balanced_sample": {
            "records_per_class": selected_per_class,
            "total_records": selected_per_class * len(HEIGHT_LABELS),
            "maximum_total_cap": 90,
            "duplicates_allowed": False,
        },
    }
    return report, eligible, candidate_sets


def run_expanded_preflight(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    del sample_size  # O tamanho e derivado conservadoramente da disponibilidade D50.
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report, _, _ = build_expanded_preflight(
        load_calibration_rows(dataset),
        load_management_features(management_kmz),
        load_km_markers(km_markers_kmz),
    )
    path = output / "preflight_availability.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"preflight_availability_json": str(path), **report}


def _select_spatially_distributed(
    rows: Sequence[Mapping[str, str]], count: int, *, seed: int
) -> list[dict[str, str]]:
    groups: dict[float | str, list[dict[str, str]]] = {}
    for source_row in sorted(rows, key=lambda row: str(row.get("sample_id") or "")):
        row = dict(source_row)
        try:
            key: float | str = round(float(str(row.get("km") or "")), 6)
        except ValueError:
            key = str(row.get("sample_id") or "")
        groups.setdefault(key, []).append(row)
    rng = random.Random(seed)
    keys = sorted(groups, key=str)
    rng.shuffle(keys)
    for values in groups.values():
        rng.shuffle(values)
    selected: list[dict[str, str]] = []
    while len(selected) < count:
        progressed = False
        for key in keys:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop())
                progressed = True
        if not progressed:
            break
    return selected


def select_expanded_balanced_sample(
    eligible: Sequence[Mapping[str, str]],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    *,
    maximum_total: int = 90,
    seed: int = RANDOM_SEED,
) -> list[dict[str, str]]:
    """Equilibra classes e snapshots usando apenas registros elegiveis em D50."""
    available = [
        dict(row)
        for row in eligible
        if candidate_sets.get(str(row.get("sample_id") or ""), {}).get("D50")
    ]
    per_class_cap = maximum_total // len(HEIGHT_LABELS)
    quota = min(
        per_class_cap,
        *(sum(_height_level(row) == level for row in available) for level in HEIGHT_LABELS),
    )
    if quota <= 0:
        return []

    selected: list[dict[str, str]] = []
    for height_class in sorted(HEIGHT_LABELS):
        class_rows = [row for row in available if _height_level(row) == height_class]
        snapshot_groups: dict[str, list[dict[str, str]]] = {}
        for row in class_rows:
            snapshot_groups.setdefault(str(row["source_snapshot_date"]), []).append(row)
        snapshot_quota = {snapshot: 0 for snapshot in snapshot_groups}
        while sum(snapshot_quota.values()) < quota:
            options = [
                snapshot
                for snapshot, rows in snapshot_groups.items()
                if snapshot_quota[snapshot] < len(rows)
            ]
            if not options:
                break
            chosen = min(options, key=lambda value: (snapshot_quota[value], value))
            snapshot_quota[chosen] += 1
        class_selection: list[dict[str, str]] = []
        for snapshot in sorted(snapshot_groups):
            class_selection.extend(
                _select_spatially_distributed(
                    snapshot_groups[snapshot],
                    snapshot_quota[snapshot],
                    seed=seed + height_class * 1000 + sum(map(ord, snapshot)),
                )
            )
        class_selection.sort(
            key=lambda row: (
                str(row.get("source_snapshot_date") or ""),
                float(str(row.get("km") or "inf")),
                str(row.get("sample_id") or ""),
            )
        )
        selected.extend(class_selection)
    if len({str(row["sample_id"]) for row in selected}) != len(selected):
        raise ValueError("Expanded sample selection produced duplicate sample IDs.")
    for position, row in enumerate(selected, start=1):
        row["selection_order"] = str(position)
    return selected


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


def _analyze_candidate_feature(
    feature: ManagementFeature,
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]],
) -> tuple[dict[str, dict[str, Any] | None], list[dict[str, str]]]:
    """Processa as duas janelas necessarias uma vez para um poligono."""
    a_start, a_end = hypothesis_window(HYPOTHESES["H_A"])
    b_start, _ = hypothesis_window(HYPOTHESES["H_B1"])
    _, b_end = hypothesis_window(HYPOTHESES["H_B2"])
    a_records, a_error = query_scenes(feature.geometry, a_start, a_end)
    b_records, b_error = query_scenes(feature.geometry, b_start, b_end)
    errors: list[dict[str, str]] = []
    if a_error:
        errors.append(
            {"feature_id": feature.feature_id, "window": "H_A", "error": a_error}
        )
    if b_error:
        errors.append(
            {
                "feature_id": feature.feature_id,
                "window": "H_B_SHARED",
                "error": b_error,
            }
        )
    return (
        {
            name: select_best_scene(a_records if name == "H_A" else b_records, target)
            for name, target in HYPOTHESES.items()
        },
        errors,
    )


def build_candidate_polygon_ndvi_rows(
    samples: Sequence[Mapping[str, str]],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]] = (
        _query_and_process_scenes
    ),
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """Avalia candidatos com cache por poligono e preserva a ambiguidade."""
    feature_cache: dict[str, dict[str, dict[str, Any] | None]] = {}
    errors: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample.get("sample_id") or "")
        for scenario, distance_limit in DISTANCE_SCENARIOS.items():
            for candidate in candidate_sets.get(sample_id, {}).get(scenario, []):
                feature = candidate.feature
                if feature.feature_id not in feature_cache:
                    selected, feature_errors = _analyze_candidate_feature(
                        feature, query_scenes=query_scenes
                    )
                    feature_cache[feature.feature_id] = selected
                    errors.extend(feature_errors)
                for hypothesis, target in HYPOTHESES.items():
                    scene = feature_cache[feature.feature_id][hypothesis]
                    row: dict[str, Any] = {
                        "sample_id": sample_id,
                        "km": sample.get("km"),
                        "height_class": _height_level(sample),
                        "sample_component": sample.get("asset_component"),
                        "hypothesis": hypothesis,
                        "target_date": target.isoformat(),
                        "distance_scenario": scenario,
                        "distance_limit_m": distance_limit,
                        "candidate_polygon_id": feature.feature_id,
                        "candidate_polygon_type": feature.name,
                        "candidate_polygon_km": _management_km(feature),
                        "candidate_distance_m": round(candidate.distance_m, 3),
                        "semantic_match_reason": candidate.semantic_match_reason,
                        "spatial_uncertainty_status": "AMBIGUOUS_CANDIDATE_SET",
                        "ground_truth_assignment": False,
                        "sentinel_scene_date": None,
                        "temporal_delta_days": None,
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
                        "scene_selection_status": "NO_VALID_SCENE",
                    }
                    if scene is not None:
                        for key in (
                            "sentinel_scene_date",
                            "temporal_delta_days",
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
                        ):
                            row[key] = scene.get(key)
                        row["scene_selection_status"] = "VALID_SCENE_SELECTED"
                    rows.append(row)
    return rows, errors, len(feature_cache)


def _analyze_expanded_candidate_feature(
    feature: ManagementFeature,
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]],
) -> tuple[dict[date, dict[str, Any] | None], list[dict[str, str]]]:
    embedded_start, embedded_end = hypothesis_window(EMBEDDED_TARGET_DATE)
    filename_start = hypothesis_window(min(FILENAME_TARGET_DATES))[0]
    filename_end = hypothesis_window(max(FILENAME_TARGET_DATES))[1]
    embedded_records, embedded_error = query_scenes(
        feature.geometry, embedded_start, embedded_end
    )
    filename_records, filename_error = query_scenes(
        feature.geometry, filename_start, filename_end
    )
    errors: list[dict[str, str]] = []
    if embedded_error:
        errors.append(
            {
                "feature_id": feature.feature_id,
                "window": "H_EMBEDDED",
                "error": embedded_error,
            }
        )
    if filename_error:
        errors.append(
            {
                "feature_id": feature.feature_id,
                "window": "H_FILENAME_SHARED",
                "error": filename_error,
            }
        )
    return (
        {
            EMBEDDED_TARGET_DATE: select_best_scene(
                embedded_records, EMBEDDED_TARGET_DATE
            ),
            **{
                target: select_best_scene(filename_records, target)
                for target in FILENAME_TARGET_DATES
            },
        },
        errors,
    )


def build_expanded_candidate_rows(
    samples: Sequence[Mapping[str, str]],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]] = (
        _query_and_process_scenes
    ),
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int, int]:
    feature_cache: dict[str, dict[date, dict[str, Any] | None]] = {}
    errors: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        filename_date, filename_source = resolve_filename_target_date(sample)
        for scenario, distance_limit in EXPANDED_SPATIAL_SCENARIOS.items():
            for candidate in candidate_sets[sample_id][scenario]:
                feature = candidate.feature
                if feature.feature_id not in feature_cache:
                    selected, feature_errors = _analyze_expanded_candidate_feature(
                        feature, query_scenes=query_scenes
                    )
                    feature_cache[feature.feature_id] = selected
                    errors.extend(feature_errors)
                for hypothesis in EXPANDED_HYPOTHESES:
                    target = (
                        EMBEDDED_TARGET_DATE
                        if hypothesis == "H_EMBEDDED"
                        else filename_date
                    )
                    scene = feature_cache[feature.feature_id][target]
                    row: dict[str, Any] = {
                        "sample_id": sample_id,
                        "source_file": sample.get("source_file"),
                        "source_snapshot_date": sample.get("source_snapshot_date"),
                        "height_class": _height_level(sample),
                        "km": sample.get("km"),
                        "sample_component": sample.get("asset_component"),
                        "hypothesis": hypothesis,
                        "target_date": target.isoformat(),
                        "target_date_source": (
                            "embedded_constant_2025-03-28"
                            if hypothesis == "H_EMBEDDED"
                            else filename_source
                        ),
                        "distance_scenario": scenario,
                        "distance_limit_m": distance_limit,
                        "candidate_geometry_id": feature.feature_id,
                        "candidate_polygon_type": feature.name,
                        "candidate_distance_m": round(candidate.distance_m, 3),
                        "semantic_match_reason": candidate.semantic_match_reason,
                        "ground_truth_assignment": False,
                        "sentinel_item_id": None,
                        "sentinel_scene_date": None,
                        "temporal_delta_days": None,
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
                        "scene_selection_status": "NO_VALID_SCENE",
                    }
                    if scene is not None:
                        row["sentinel_item_id"] = scene.get("item_id")
                        for key in (
                            "sentinel_scene_date",
                            "temporal_delta_days",
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
                        ):
                            row[key] = scene.get(key)
                        row["scene_selection_status"] = "VALID_SCENE_SELECTED"
                    rows.append(row)

    geometry_users: dict[str, set[str]] = {}
    scene_users: dict[str, set[str]] = {}
    km_geometry_users: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        sample_id = str(row["sample_id"])
        geometry_id = str(row["candidate_geometry_id"])
        geometry_users.setdefault(geometry_id, set()).add(sample_id)
        km_geometry_users.setdefault((str(row.get("km")), geometry_id), set()).add(
            sample_id
        )
        if row.get("sentinel_item_id"):
            scene_users.setdefault(str(row["sentinel_item_id"]), set()).add(sample_id)
    for row in rows:
        geometry_id = str(row["candidate_geometry_id"])
        scene_id = row.get("sentinel_item_id")
        row["shared_geometry_count"] = len(geometry_users[geometry_id])
        row["shared_scene_count"] = (
            len(scene_users[str(scene_id)]) if scene_id else 0
        )
        row["shared_km_geometry_count"] = len(
            km_geometry_users[(str(row.get("km")), geometry_id)]
        )
    return rows, errors, len(feature_cache), len(scene_users)


def _finite_values(values: Iterable[Any]) -> np.ndarray:
    numbers: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numbers.append(number)
    return np.asarray(numbers, dtype=float)


def aggregate_expanded_candidate_ndvi(
    samples: Sequence[Mapping[str, str]],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in candidate_rows:
        key = (
            str(row["sample_id"]),
            str(row["hypothesis"]),
            str(row["distance_scenario"]),
        )
        grouped.setdefault(key, []).append(row)
    aggregates: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        _, filename_source = resolve_filename_target_date(sample)
        for hypothesis in EXPANDED_HYPOTHESES:
            target = expanded_target_date(sample, hypothesis)
            for scenario, distance_limit in EXPANDED_SPATIAL_SCENARIOS.items():
                candidates = list(candidate_sets[sample_id][scenario])
                rows = grouped.get((sample_id, hypothesis, scenario), [])
                valid = [
                    row
                    for row in rows
                    if row.get("scene_selection_status") == "VALID_SCENE_SELECTED"
                    and _finite_values([row.get("ndvi_median")]).size == 1
                ]
                values = _finite_values(row.get("ndvi_median") for row in valid)
                q1 = float(np.quantile(values, 0.25)) if values.size else None
                q3 = float(np.quantile(values, 0.75)) if values.size else None
                minimum = float(np.min(values)) if values.size else None
                maximum = float(np.max(values)) if values.size else None
                geometry_ids = sorted(
                    {str(candidate.feature.feature_id) for candidate in candidates}
                )
                scene_ids = sorted(
                    {
                        str(row["sentinel_item_id"])
                        for row in valid
                        if row.get("sentinel_item_id")
                    }
                )
                aggregates.append(
                    {
                        "sample_id": sample_id,
                        "source_file": sample.get("source_file"),
                        "source_snapshot_date": sample.get("source_snapshot_date"),
                        "height_class": _height_level(sample),
                        "km": sample.get("km"),
                        "sample_component": sample.get("asset_component"),
                        "hypothesis": hypothesis,
                        "target_date": target.isoformat(),
                        "target_date_source": (
                            "embedded_constant_2025-03-28"
                            if hypothesis == "H_EMBEDDED"
                            else filename_source
                        ),
                        "distance_scenario": scenario,
                        "distance_limit_m": distance_limit,
                        "candidate_polygon_count": len(candidates),
                        "candidate_with_valid_scene_count": len(valid),
                        "candidate_geometry_id": ";".join(geometry_ids),
                        "sentinel_item_id": ";".join(scene_ids),
                        "shared_geometry_count": max(
                            (int(row["shared_geometry_count"]) for row in rows),
                            default=0,
                        ),
                        "shared_scene_count": max(
                            (int(row["shared_scene_count"]) for row in valid),
                            default=0,
                        ),
                        "shared_km_geometry_count": max(
                            (int(row["shared_km_geometry_count"]) for row in rows),
                            default=0,
                        ),
                        "ndvi_candidate_min": minimum,
                        "ndvi_candidate_q1": q1,
                        "ndvi_candidate_median": (
                            float(np.median(values)) if values.size else None
                        ),
                        "ndvi_candidate_q3": q3,
                        "ndvi_candidate_max": maximum,
                        "ndvi_candidate_std": (
                            float(np.std(values, ddof=0)) if values.size else None
                        ),
                        "ndvi_candidate_iqr": (
                            q3 - q1 if q1 is not None and q3 is not None else None
                        ),
                        "ndvi_candidate_range": (
                            maximum - minimum
                            if minimum is not None and maximum is not None
                            else None
                        ),
                        "spatial_uncertainty_status": "AMBIGUOUS_CANDIDATE_SET",
                        "ground_truth_assignment": False,
                    }
                )
    return aggregates


def summarize_expanded_height_classes(
    aggregates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hypothesis in EXPANDED_HYPOTHESES:
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            for height_class in sorted(HEIGHT_LABELS):
                group = [
                    row
                    for row in aggregates
                    if row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                    and row.get("height_class") == height_class
                ]
                values = _finite_values(
                    row.get("ndvi_candidate_median") for row in group
                )
                summaries.append(
                    {
                        "hypothesis": hypothesis,
                        "distance_scenario": scenario,
                        "height_class": height_class,
                        "n_selected": len(group),
                        "n_with_candidates": sum(
                            int(row.get("candidate_polygon_count") or 0) > 0
                            for row in group
                        ),
                        "n_with_valid_sentinel": int(values.size),
                        "ndvi_mean": float(np.mean(values)) if values.size else None,
                        "ndvi_median": (
                            float(np.median(values)) if values.size else None
                        ),
                        "ndvi_std": (
                            float(np.std(values, ddof=1)) if values.size >= 2 else None
                        ),
                        "ndvi_q1": (
                            float(np.quantile(values, 0.25)) if values.size else None
                        ),
                        "ndvi_q3": (
                            float(np.quantile(values, 0.75)) if values.size else None
                        ),
                    }
                )
    return summaries


def summarize_binary_height_groups(
    aggregates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hypothesis in EXPANDED_HYPOTHESES:
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            scenario_rows = [
                row
                for row in aggregates
                if row.get("hypothesis") == hypothesis
                and row.get("distance_scenario") == scenario
            ]
            for label, predicate in (
                ("LOW_OR_ACCEPTABLE", lambda value: int(value) <= 2),
                ("HIGH", lambda value: int(value) == 3),
            ):
                group = [row for row in scenario_rows if predicate(row["height_class"])]
                values = _finite_values(
                    row.get("ndvi_candidate_median") for row in group
                )
                summaries.append(
                    {
                        "hypothesis": hypothesis,
                        "distance_scenario": scenario,
                        "binary_height_group": label,
                        "n_selected": len(group),
                        "n_with_candidates": sum(
                            int(row.get("candidate_polygon_count") or 0) > 0
                            for row in group
                        ),
                        "n_with_valid_sentinel": int(values.size),
                        "ndvi_mean": float(np.mean(values)) if values.size else None,
                        "ndvi_median": (
                            float(np.median(values)) if values.size else None
                        ),
                        "ndvi_std": (
                            float(np.std(values, ddof=1)) if values.size >= 2 else None
                        ),
                        "ndvi_q1": (
                            float(np.quantile(values, 0.25)) if values.size else None
                        ),
                        "ndvi_q3": (
                            float(np.quantile(values, 0.75)) if values.size else None
                        ),
                    }
                )
    return summaries


def aggregate_candidate_ndvi(
    samples: Sequence[Mapping[str, str]],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resume a incerteza entre candidatos por amostra, hipotese e cenario."""
    grouped_rows: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in candidate_rows:
        key = (
            str(row.get("sample_id") or ""),
            str(row.get("hypothesis") or ""),
            str(row.get("distance_scenario") or ""),
        )
        grouped_rows.setdefault(key, []).append(row)

    aggregates: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample.get("sample_id") or "")
        for hypothesis, target in HYPOTHESES.items():
            for scenario, distance_limit in DISTANCE_SCENARIOS.items():
                candidates = list(candidate_sets.get(sample_id, {}).get(scenario, []))
                group = grouped_rows.get((sample_id, hypothesis, scenario), [])
                valid = [
                    row
                    for row in group
                    if row.get("scene_selection_status") == "VALID_SCENE_SELECTED"
                    and _finite_values([row.get("ndvi_median")]).size == 1
                ]
                values = _finite_values(row.get("ndvi_median") for row in valid)
                q1 = float(np.quantile(values, 0.25)) if values.size else None
                q3 = float(np.quantile(values, 0.75)) if values.size else None
                minimum = float(np.min(values)) if values.size else None
                maximum = float(np.max(values)) if values.size else None
                aggregates.append(
                    {
                        "sample_id": sample_id,
                        "km": sample.get("km"),
                        "height_class": _height_level(sample),
                        "sample_component": sample.get("asset_component"),
                        "hypothesis": hypothesis,
                        "target_date": target.isoformat(),
                        "distance_scenario": scenario,
                        "distance_limit_m": distance_limit,
                        "candidate_polygon_count": len(candidates),
                        "candidate_with_valid_scene_count": len(valid),
                        "ndvi_candidate_min": minimum,
                        "ndvi_candidate_q1": q1,
                        "ndvi_candidate_median": (
                            float(np.median(values)) if values.size else None
                        ),
                        "ndvi_candidate_q3": q3,
                        "ndvi_candidate_max": maximum,
                        "ndvi_candidate_std": (
                            float(np.std(values, ddof=0)) if values.size else None
                        ),
                        "ndvi_candidate_iqr": (
                            q3 - q1 if q1 is not None and q3 is not None else None
                        ),
                        "ndvi_candidate_range": (
                            maximum - minimum
                            if minimum is not None and maximum is not None
                            else None
                        ),
                        "spatial_uncertainty_status": (
                            "AMBIGUOUS_CANDIDATE_SET"
                            if candidates
                            else "NO_ELIGIBLE_CANDIDATES"
                        ),
                        "ground_truth_assignment": False,
                    }
                )
    return aggregates


def summarize_candidate_ndvi_by_class(
    aggregates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hypothesis in HYPOTHESES:
        for scenario in DISTANCE_SCENARIOS:
            for height_class in sorted(HEIGHT_LABELS):
                group = [
                    row
                    for row in aggregates
                    if row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                    and row.get("height_class") == height_class
                ]
                valid = _finite_values(
                    row.get("ndvi_candidate_median") for row in group
                )
                summaries.append(
                    {
                        "hypothesis": hypothesis,
                        "distance_scenario": scenario,
                        "height_class": height_class,
                        "n_total": len(group),
                        "n_with_candidates": sum(
                            int(row.get("candidate_polygon_count") or 0) > 0
                            for row in group
                        ),
                        "n_with_valid_sentinel": int(valid.size),
                        "ndvi_mean": float(np.mean(valid)) if valid.size else None,
                        "ndvi_median": float(np.median(valid)) if valid.size else None,
                        "ndvi_std": (
                            float(np.std(valid, ddof=1)) if valid.size >= 2 else None
                        ),
                        "ndvi_q1": (
                            float(np.quantile(valid, 0.25)) if valid.size else None
                        ),
                        "ndvi_q3": (
                            float(np.quantile(valid, 0.75)) if valid.size else None
                        ),
                    }
                )
    return summaries


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


def _auc_from_groups(low: np.ndarray, high: np.ndarray, *, negate: bool = False) -> float:
    scores = np.concatenate((low, high))
    if negate:
        scores = -scores
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(scores.size, dtype=float)
    position = 0
    while position < scores.size:
        end = position + 1
        while end < scores.size and scores[order[end]] == scores[order[position]]:
            end += 1
        average_rank = (position + 1 + end) / 2.0
        ranks[order[position:end]] = average_rank
        position = end
    positive_ranks = float(np.sum(ranks[low.size :]))
    return (
        positive_ranks - high.size * (high.size + 1) / 2.0
    ) / (high.size * low.size)


def bootstrap_binary_metrics(
    low: Sequence[float],
    high: Sequence[float],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    low_values = _finite_values(low)
    high_values = _finite_values(high)
    if low_values.size < 2 or high_values.size < 2 or iterations <= 0:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "iterations": 0,
            "seed": seed,
            "median_difference_high_minus_low_ci95": None,
            "auc_ndvi_ci95": None,
            "auc_negative_ndvi_ci95": None,
        }
    rng = np.random.default_rng(seed)
    differences = np.empty(iterations, dtype=float)
    auc_ndvi = np.empty(iterations, dtype=float)
    auc_negative = np.empty(iterations, dtype=float)
    for index in range(iterations):
        low_sample = rng.choice(low_values, size=low_values.size, replace=True)
        high_sample = rng.choice(high_values, size=high_values.size, replace=True)
        differences[index] = float(np.median(high_sample) - np.median(low_sample))
        auc_ndvi[index] = _auc_from_groups(low_sample, high_sample)
        auc_negative[index] = _auc_from_groups(low_sample, high_sample, negate=True)

    def interval(values: np.ndarray) -> dict[str, float]:
        return {
            "lower": float(np.quantile(values, 0.025)),
            "upper": float(np.quantile(values, 0.975)),
        }

    return {
        "status": "CALCULATED",
        "iterations": iterations,
        "seed": seed,
        "median_difference_high_minus_low_ci95": interval(differences),
        "auc_ndvi_ci95": interval(auc_ndvi),
        "auc_negative_ndvi_ci95": interval(auc_negative),
    }


def calculate_binary_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    low = _finite_values(
        row.get("ndvi_candidate_median")
        for row in rows
        if int(row["height_class"]) <= 2
    )
    high = _finite_values(
        row.get("ndvi_candidate_median")
        for row in rows
        if int(row["height_class"]) == 3
    )

    def distribution(values: np.ndarray) -> dict[str, Any]:
        return {
            "n": int(values.size),
            "mean": float(np.mean(values)) if values.size else None,
            "median": float(np.median(values)) if values.size else None,
            "std": float(np.std(values, ddof=1)) if values.size >= 2 else None,
            "q1": float(np.quantile(values, 0.25)) if values.size else None,
            "q3": float(np.quantile(values, 0.75)) if values.size else None,
        }

    sufficient = low.size >= 2 and high.size >= 2
    median_difference = (
        float(np.median(high) - np.median(low)) if low.size and high.size else None
    )
    mann_whitney: dict[str, Any]
    if sufficient:
        try:
            from scipy.stats import mannwhitneyu

            result = mannwhitneyu(high, low, alternative="two-sided")
            mann_whitney = {
                "status": "CALCULATED",
                "u_statistic": float(result.statistic),
                "p_value": float(result.pvalue),
            }
        except (ImportError, ValueError):
            mann_whitney = {
                "status": "UNAVAILABLE",
                "u_statistic": None,
                "p_value": None,
            }
    else:
        mann_whitney = {
            "status": "INSUFFICIENT_SAMPLE",
            "u_statistic": None,
            "p_value": None,
        }
    return {
        "low_or_acceptable_le_30cm": distribution(low),
        "high_gt_30cm": distribution(high),
        "median_difference_high_minus_low": median_difference,
        "mann_whitney_u": mann_whitney,
        "auc_ndvi": {
            "status": "CALCULATED" if sufficient else "INSUFFICIENT_SAMPLE",
            "value": _auc_from_groups(low, high) if sufficient else None,
        },
        "auc_negative_ndvi": {
            "status": "CALCULATED" if sufficient else "INSUFFICIENT_SAMPLE",
            "value": (
                _auc_from_groups(low, high, negate=True) if sufficient else None
            ),
            "interpretation": "Direction-only exploratory analysis; not a selected model.",
        },
        "bootstrap_95": bootstrap_binary_metrics(
            low, high, seed=bootstrap_seed
        ),
    }
def _candidate_metric_rows(
    aggregates: Sequence[Mapping[str, Any]], hypothesis: str, scenario: str
) -> list[dict[str, Any]]:
    return [
        {
            "height_class": row.get("height_class"),
            "ndvi_median": row.get("ndvi_candidate_median"),
        }
        for row in aggregates
        if row.get("hypothesis") == hypothesis
        and row.get("distance_scenario") == scenario
    ]


def _sign(value: Any) -> str | None:
    if value is None:
        return None
    number = float(value)
    if number > 0:
        return "POSITIVE"
    if number < 0:
        return "NEGATIVE"
    return "ZERO"


def build_candidate_sensitivity_report(
    samples: Sequence[Mapping[str, str]],
    aggregates: Sequence[Mapping[str, Any]],
    class_summaries: Sequence[Mapping[str, Any]],
    *,
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    unique_polygons_processed: int,
    errors: Sequence[Mapping[str, str]],
    elapsed_seconds: float,
) -> dict[str, Any]:
    hypotheses: dict[str, Any] = {}
    for hypothesis, target in HYPOTHESES.items():
        scenario_results: dict[str, Any] = {}
        orders: dict[str, list[int] | None] = {}
        correlations: list[float] = []
        auc_values: list[float] = []
        class_values: dict[int, list[float]] = {
            height_class: [] for height_class in HEIGHT_LABELS
        }
        for scenario, distance_limit in DISTANCE_SCENARIOS.items():
            group = [
                row
                for row in aggregates
                if row.get("hypothesis") == hypothesis
                and row.get("distance_scenario") == scenario
            ]
            metrics_rows = _candidate_metric_rows(aggregates, hypothesis, scenario)
            spearman = _spearman(metrics_rows)
            auc = _auc(metrics_rows)
            if spearman["correlation"] is not None:
                correlations.append(float(spearman["correlation"]))
            if auc["auc"] is not None:
                auc_values.append(float(auc["auc"]))
            dispersion = {
                field: _finite_values(row.get(field) for row in group)
                for field in (
                    "ndvi_candidate_std",
                    "ndvi_candidate_iqr",
                    "ndvi_candidate_range",
                )
            }
            summaries = [
                dict(summary)
                for summary in class_summaries
                if summary.get("hypothesis") == hypothesis
                and summary.get("distance_scenario") == scenario
            ]
            medians = {
                int(summary["height_class"]): summary.get("ndvi_median")
                for summary in summaries
            }
            if all(medians.get(level) is not None for level in HEIGHT_LABELS):
                orders[scenario] = sorted(
                    HEIGHT_LABELS,
                    key=lambda level: (float(medians[level]), level),
                )
            else:
                orders[scenario] = None
            for level, value in medians.items():
                if value is not None:
                    class_values[level].append(float(value))
            scenario_results[scenario] = {
                "distance_limit_m": distance_limit,
                "samples_with_candidates": sum(
                    int(row.get("candidate_polygon_count") or 0) > 0 for row in group
                ),
                "samples_with_valid_sentinel": sum(
                    int(row.get("candidate_with_valid_scene_count") or 0) > 0
                    for row in group
                ),
                "statistics_by_height_class": summaries,
                "spearman_height_class_vs_ndvi_candidate_median": spearman,
                "auc_height_gt_30cm_vs_ndvi_candidate_median": auc,
                "typical_intra_sample_dispersion": {
                    "median_candidate_std": (
                        float(np.median(dispersion["ndvi_candidate_std"]))
                        if dispersion["ndvi_candidate_std"].size
                        else None
                    ),
                    "median_candidate_iqr": (
                        float(np.median(dispersion["ndvi_candidate_iqr"]))
                        if dispersion["ndvi_candidate_iqr"].size
                        else None
                    ),
                    "median_candidate_range": (
                        float(np.median(dispersion["ndvi_candidate_range"]))
                        if dispersion["ndvi_candidate_range"].size
                        else None
                    ),
                },
            }
        available_orders = [tuple(order) for order in orders.values() if order is not None]
        signs = [_sign(value) for value in correlations]
        hypotheses[hypothesis] = {
            "target_date": target.isoformat(),
            "window_start": hypothesis_window(target)[0].isoformat(),
            "window_end": hypothesis_window(target)[1].isoformat(),
            "scenarios": scenario_results,
            "spatial_sensitivity": {
                "class_median_span_across_scenarios": {
                    str(level): (
                        max(values) - min(values) if len(values) >= 2 else None
                    )
                    for level, values in class_values.items()
                },
                "class_order_low_to_high_ndvi": orders,
                "class_order_is_stable_when_calculable": (
                    len(set(available_orders)) == 1 if available_orders else None
                ),
                "spearman_sign_by_scenario": {
                    scenario: _sign(
                        scenario_results[scenario][
                            "spearman_height_class_vs_ndvi_candidate_median"
                        ]["correlation"]
                    )
                    for scenario in DISTANCE_SCENARIOS
                },
                "spearman_sign_is_stable_when_calculable": (
                    len(set(signs)) == 1 if signs else None
                ),
                "auc_range_across_scenarios": (
                    max(auc_values) - min(auc_values)
                    if len(auc_values) >= 2
                    else None
                ),
                "coverage_by_scenario": {
                    scenario: {
                        "with_candidates": scenario_results[scenario][
                            "samples_with_candidates"
                        ],
                        "with_valid_sentinel": scenario_results[scenario][
                            "samples_with_valid_sentinel"
                        ],
                    }
                    for scenario in DISTANCE_SCENARIOS
                },
                "typical_dispersion_by_scenario": {
                    scenario: scenario_results[scenario][
                        "typical_intra_sample_dispersion"
                    ]
                    for scenario in DISTANCE_SCENARIOS
                },
            },
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "offline_exploratory_candidate_polygon_sensitivity",
        "documented_date_confirmation": False,
        "ground_truth_polygon_assignment": False,
        "automatic_scenario_winner": None,
        "distance_scenarios_are_operational_thresholds": False,
        "distance_scenarios_m": dict(DISTANCE_SCENARIOS),
        "representative_sample_value": "median of candidate polygon NDVI medians",
        "dispersion_definition": (
            "Population standard deviation (ddof=0), interquartile range and range "
            "across valid candidate-polygon NDVI medians."
        ),
        "sample_size": len(samples),
        "unique_candidate_polygons_processed": unique_polygons_processed,
        "inputs": {
            "dataset": str(dataset),
            "management_kmz": str(management_kmz),
            "km_markers_kmz": str(km_markers_kmz),
        },
        "hypotheses": hypotheses,
        "execution_errors": list(errors),
        "elapsed_seconds": elapsed_seconds,
        "limitations": [
            "Every polygon remains an ambiguous candidate and is not ground truth.",
            "D20, D50 and D100 are exploratory sensitivity distances, not operational thresholds.",
            "No vegetation height in centimetres is inferred from NDVI.",
            "Spearman and AUC are exploratory associations, not system accuracy.",
        ],
    }
def _expanded_dependency_diagnostics(
    candidate_rows: Sequence[Mapping[str, Any]], hypothesis: str, scenario: str
) -> dict[str, Any]:
    rows = [
        row
        for row in candidate_rows
        if row.get("hypothesis") == hypothesis
        and row.get("distance_scenario") == scenario
        and row.get("scene_selection_status") == "VALID_SCENE_SELECTED"
    ]
    geometry_users: dict[str, set[str]] = {}
    scene_users: dict[str, set[str]] = {}
    km_geometry_users: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        sample_id = str(row["sample_id"])
        geometry_id = str(row["candidate_geometry_id"])
        scene_id = str(row["sentinel_item_id"])
        geometry_users.setdefault(geometry_id, set()).add(sample_id)
        scene_users.setdefault(scene_id, set()).add(sample_id)
        km_geometry_users.setdefault((str(row.get("km")), geometry_id), set()).add(
            sample_id
        )
    return {
        "unique_candidate_geometries_with_valid_scene": len(geometry_users),
        "unique_sentinel_items": len(scene_users),
        "shared_geometry_groups": sum(
            len(users) > 1 for users in geometry_users.values()
        ),
        "shared_scene_groups": sum(len(users) > 1 for users in scene_users.values()),
        "shared_km_geometry_groups": sum(
            len(users) > 1 for users in km_geometry_users.values()
        ),
        "maximum_samples_sharing_geometry": max(
            (len(users) for users in geometry_users.values()), default=0
        ),
        "maximum_samples_sharing_scene": max(
            (len(users) for users in scene_users.values()), default=0
        ),
        "maximum_samples_sharing_km_and_geometry": max(
            (len(users) for users in km_geometry_users.values()), default=0
        ),
        "independence_note": (
            "Repeated geometry or Sentinel item use is reported dependency, not an "
            "additional independent observation."
        ),
    }


def _stable_binary_direction(scenarios: Mapping[str, Mapping[str, Any]]) -> bool:
    differences = [
        scenarios[scenario]["binary_analysis"][
            "median_difference_high_minus_low"
        ]
        for scenario in EXPANDED_SPATIAL_SCENARIOS
    ]
    return all(value is not None for value in differences) and len(
        {_sign(value) for value in differences}
    ) == 1


def _expanded_temporal_support(hypotheses: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    for hypothesis in EXPANDED_HYPOTHESES:
        scenarios = hypotheses[hypothesis]["scenarios"]
        directional_auc = [
            max(
                float(scenarios[scenario]["binary_analysis"]["auc_ndvi"]["value"]),
                float(
                    scenarios[scenario]["binary_analysis"]["auc_negative_ndvi"][
                        "value"
                    ]
                ),
            )
            if scenarios[scenario]["binary_analysis"]["auc_ndvi"]["value"]
            is not None
            else None
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        ]
        diagnostics[hypothesis] = {
            "stable_binary_direction": _stable_binary_direction(scenarios),
            "directional_auc_by_scenario": dict(
                zip(EXPANDED_SPATIAL_SCENARIOS, directional_auc)
            ),
            "valid_sample_coverage_by_scenario": {
                scenario: scenarios[scenario]["coverage"]["n_with_valid_sentinel"]
                for scenario in EXPANDED_SPATIAL_SCENARIOS
            },
        }

    embedded = diagnostics["H_EMBEDDED"]
    filename = diagnostics["H_FILENAME"]

    def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return (
            left["stable_binary_direction"]
            and all(
                left["directional_auc_by_scenario"][scenario] is not None
                and right["directional_auc_by_scenario"][scenario] is not None
                and left["directional_auc_by_scenario"][scenario]
                >= right["directional_auc_by_scenario"][scenario] + 0.05
                and left["valid_sample_coverage_by_scenario"][scenario]
                >= right["valid_sample_coverage_by_scenario"][scenario]
                for scenario in EXPANDED_SPATIAL_SCENARIOS
            )
        )

    if dominates(embedded, filename):
        conclusion = "STRONGER_EXPLORATORY_SUPPORT_FOR_H_EMBEDDED"
    elif dominates(filename, embedded):
        conclusion = "STRONGER_EXPLORATORY_SUPPORT_FOR_H_FILENAME"
    else:
        conclusion = "NO_CLEAR_TEMPORAL_HYPOTHESIS_ADVANTAGE"
    return {
        "conclusion": conclusion,
        "rule": (
            "A hypothesis is stronger only if binary direction is stable across D50/D100, "
            "directional AUC exceeds the other hypothesis by at least 0.05 in both "
            "scenarios, and valid-sample coverage is no lower in either scenario."
        ),
        "diagnostics": diagnostics,
    }


def _model_experiment_decision(hypotheses: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    qualifying: list[str] = []
    for hypothesis in EXPANDED_HYPOTHESES:
        scenarios = hypotheses[hypothesis]["scenarios"]
        if not _stable_binary_direction(scenarios):
            continue
        spearman_signs = {
            _sign(scenarios[scenario]["spearman"]["correlation"])
            for scenario in EXPANDED_SPATIAL_SCENARIOS
            if scenarios[scenario]["spearman"]["correlation"] is not None
        }
        checks = []
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            binary = scenarios[scenario]["binary_analysis"]
            interval = binary["bootstrap_95"][
                "median_difference_high_minus_low_ci95"
            ]
            auc_values = (
                binary["auc_ndvi"]["value"],
                binary["auc_negative_ndvi"]["value"],
            )
            directional_auc = (
                max(float(value) for value in auc_values if value is not None)
                if any(value is not None for value in auc_values)
                else None
            )
            checks.append(
                binary["low_or_acceptable_le_30cm"]["n"] >= 10
                and binary["high_gt_30cm"]["n"] >= 8
                and directional_auc is not None
                and directional_auc >= 0.60
                and interval is not None
                and not (interval["lower"] <= 0 <= interval["upper"])
            )
        if len(spearman_signs) == 1 and all(checks):
            qualifying.append(hypothesis)
    return {
        "decision": (
            "GO_TO_MODEL_EXPERIMENT"
            if qualifying
            else "INSUFFICIENT_OR_UNSTABLE_SIGNAL"
        ),
        "qualifying_hypotheses": qualifying,
        "rule": (
            "GO requires one hypothesis to retain the same binary and Spearman direction "
            "in D50/D100, at least 10 valid <=30 cm and 8 valid >30 cm samples per "
            "scenario, directional AUC >=0.60, and a bootstrap median-difference CI "
            "excluding zero in both scenarios."
        ),
        "scope": "Decision only on whether a later model experiment is warranted.",
    }


def build_expanded_comparison(
    selected: Sequence[Mapping[str, str]],
    aggregates: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    height_summaries: Sequence[Mapping[str, Any]],
    binary_summaries: Sequence[Mapping[str, Any]],
    preflight: Mapping[str, Any],
    *,
    unique_polygons_processed: int,
    unique_sentinel_items: int,
    elapsed_seconds: float,
    errors: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    hypotheses: dict[str, Any] = {}
    for hypothesis in EXPANDED_HYPOTHESES:
        scenarios: dict[str, Any] = {}
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            group = [
                row
                for row in aggregates
                if row.get("hypothesis") == hypothesis
                and row.get("distance_scenario") == scenario
            ]
            valid_count = sum(
                row.get("ndvi_candidate_median") is not None for row in group
            )
            metric_rows = [
                {
                    "height_class": row["height_class"],
                    "ndvi_median": row.get("ndvi_candidate_median"),
                }
                for row in group
            ]
            scenario_seed = (
                BOOTSTRAP_SEED
                + sum(map(ord, hypothesis))
                + sum(map(ord, scenario))
            )
            scenarios[scenario] = {
                "distance_limit_m": EXPANDED_SPATIAL_SCENARIOS[scenario],
                "coverage": {
                    "n_selected": len(group),
                    "n_with_candidates": sum(
                        int(row.get("candidate_polygon_count") or 0) > 0
                        for row in group
                    ),
                    "n_with_valid_sentinel": valid_count,
                    "percentage_with_valid_sentinel": (
                        valid_count / len(group) * 100 if group else 0.0
                    ),
                },
                "height_class_summary": [
                    dict(row)
                    for row in height_summaries
                    if row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                ],
                "binary_height_summary": [
                    dict(row)
                    for row in binary_summaries
                    if row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                ],
                "binary_analysis": calculate_binary_metrics(
                    group, bootstrap_seed=scenario_seed
                ),
                "spearman": _spearman(metric_rows),
                "dependency_diagnostics": _expanded_dependency_diagnostics(
                    candidate_rows, hypothesis, scenario
                ),
            }
        hypotheses[hypothesis] = {
            "target_date_rule": (
                "2025-03-28 for every selected record"
                if hypothesis == "H_EMBEDDED"
                else "source_snapshot_date of each selected record"
            ),
            "scenarios": scenarios,
            "robustness_D50_vs_D100": {
                "binary_direction_stable": _stable_binary_direction(scenarios),
                "spearman_sign_stable": len(
                    {
                        _sign(scenarios[scenario]["spearman"]["correlation"])
                        for scenario in EXPANDED_SPATIAL_SCENARIOS
                        if scenarios[scenario]["spearman"]["correlation"] is not None
                    }
                )
                == 1,
                "valid_coverage_change_D100_minus_D50": (
                    scenarios["D100"]["coverage"]["n_with_valid_sentinel"]
                    - scenarios["D50"]["coverage"]["n_with_valid_sentinel"]
                ),
                "auc_ndvi_change_D100_minus_D50": (
                    scenarios["D100"]["binary_analysis"]["auc_ndvi"]["value"]
                    - scenarios["D50"]["binary_analysis"]["auc_ndvi"]["value"]
                    if scenarios["D100"]["binary_analysis"]["auc_ndvi"]["value"]
                    is not None
                    and scenarios["D50"]["binary_analysis"]["auc_ndvi"]["value"]
                    is not None
                    else None
                ),
            },
        }
    temporal_support = _expanded_temporal_support(hypotheses)
    model_decision = _model_experiment_decision(hypotheses)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "expanded_temporal_calibration_candidate_sets",
        "documented_date_confirmation": False,
        "ground_truth_polygon_assignment": False,
        "hypotheses": hypotheses,
        "temporal_hypothesis_comparison": temporal_support,
        "model_experiment_decision": model_decision,
        "preflight": dict(preflight),
        "selection": {
            "sample_count": len(selected),
            "random_seed": RANDOM_SEED,
            "selected_by_class": {
                str(level): sum(_height_level(row) == level for row in selected)
                for level in HEIGHT_LABELS
            },
            "selected_by_snapshot_and_class": {
                snapshot: {
                    str(level): sum(
                        str(row.get("source_snapshot_date")) == snapshot
                        and _height_level(row) == level
                        for row in selected
                    )
                    for level in HEIGHT_LABELS
                }
                for snapshot in sorted(
                    {str(row.get("source_snapshot_date")) for row in selected}
                )
            },
            "duplicate_sample_ids": len(selected)
            - len({str(row["sample_id"]) for row in selected}),
        },
        "performance": {
            "unique_polygons_processed": unique_polygons_processed,
            "unique_sentinel_items_used": unique_sentinel_items,
            "elapsed_seconds": elapsed_seconds,
        },
        "execution_errors": list(errors),
        "interpretation_constraints": [
            "Neither temporal hypothesis is a documented date confirmation.",
            "D50 and D100 are exploratory sensitivity distances, not operational thresholds.",
            "Candidate polygons are ambiguous and never ground truth.",
            "AUC and AUC(-NDVI) are exploratory discrimination metrics, not accuracy.",
            "AUC(-NDVI) is direction analysis only and is not an automatically selected model.",
            "A p-value alone is not scientific proof.",
            "NDVI is not converted to vegetation height in centimetres.",
        ],
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


def _write_candidate_boxplots(
    aggregates: Sequence[Mapping[str, Any]], output_dir: Path
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: list[Path] = []
    for hypothesis, target in HYPOTHESES.items():
        for scenario in DISTANCE_SCENARIOS:
            groups = [
                [
                    float(row["ndvi_candidate_median"])
                    for row in aggregates
                    if row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                    and row.get("height_class") == level
                    and row.get("ndvi_candidate_median") is not None
                ]
                for level in sorted(HEIGHT_LABELS)
            ]
            figure, axis = plt.subplots(figsize=(8, 5))
            if any(groups):
                axis.boxplot(
                    groups,
                    tick_labels=[HEIGHT_LABELS[level] for level in sorted(HEIGHT_LABELS)],
                )
                axis.set_ylabel("Median NDVI across candidate polygons")
                axis.set_xlabel("Field vegetation height class")
            else:
                axis.text(
                    0.5,
                    0.5,
                    "Insufficient valid Sentinel-2 candidate data for this plot",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                )
                axis.set_xticks([])
                axis.set_yticks([])
            axis.set_title(
                f"{hypothesis}_{scenario} — target {target.isoformat()} "
                "(ambiguous candidate sets)"
            )
            figure.tight_layout()
            path = output_dir / f"ndvi_boxplot_{hypothesis}_{scenario}.png"
            figure.savefig(path, dpi=150)
            plt.close(figure)
            paths.append(path)
    return paths


def _write_expanded_boxplots(
    aggregates: Sequence[Mapping[str, Any]], output_dir: Path
) -> tuple[list[Path], list[str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: list[Path] = []
    skipped: list[str] = []
    for hypothesis in EXPANDED_HYPOTHESES:
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            group = [
                row
                for row in aggregates
                if row.get("hypothesis") == hypothesis
                and row.get("distance_scenario") == scenario
                and row.get("ndvi_candidate_median") is not None
            ]
            class_groups = [
                [
                    float(row["ndvi_candidate_median"])
                    for row in group
                    if int(row["height_class"]) == level
                ]
                for level in sorted(HEIGHT_LABELS)
            ]
            class_name = f"{hypothesis}_{scenario}_height_classes"
            if all(class_groups):
                figure, axis = plt.subplots(figsize=(8, 5))
                axis.boxplot(
                    class_groups,
                    tick_labels=[HEIGHT_LABELS[level] for level in sorted(HEIGHT_LABELS)],
                )
                axis.set_ylabel("Median NDVI across candidate polygons")
                axis.set_xlabel("Field vegetation height class")
                axis.set_title(f"{hypothesis}_{scenario} — ambiguous candidate sets")
                figure.tight_layout()
                path = output_dir / f"boxplot_{class_name}.png"
                figure.savefig(path, dpi=150)
                plt.close(figure)
                paths.append(path)
            else:
                skipped.append(f"{class_name}: insufficient data in one or more classes")

            binary_groups = [
                [
                    float(row["ndvi_candidate_median"])
                    for row in group
                    if int(row["height_class"]) <= 2
                ],
                [
                    float(row["ndvi_candidate_median"])
                    for row in group
                    if int(row["height_class"]) == 3
                ],
            ]
            binary_name = f"{hypothesis}_{scenario}_binary_height"
            if all(binary_groups):
                figure, axis = plt.subplots(figsize=(8, 5))
                axis.boxplot(binary_groups, tick_labels=["<=30 cm", ">30 cm"])
                axis.set_ylabel("Median NDVI across candidate polygons")
                axis.set_xlabel("Exploratory binary field-height group")
                axis.set_title(f"{hypothesis}_{scenario} — ambiguous candidate sets")
                figure.tight_layout()
                path = output_dir / f"boxplot_{binary_name}.png"
                figure.savefig(path, dpi=150)
                plt.close(figure)
                paths.append(path)
            else:
                skipped.append(f"{binary_name}: insufficient binary-group data")
    return paths, skipped


def run_expanded_calibration(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = load_calibration_rows(dataset)
    features = load_management_features(management_kmz)
    markers = load_km_markers(km_markers_kmz)
    preflight, eligible, all_candidate_sets = build_expanded_preflight(
        rows, features, markers
    )
    preflight_path = output / "preflight_availability.json"
    preflight_path.write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    selected = select_expanded_balanced_sample(
        eligible, all_candidate_sets, maximum_total=min(sample_size, 90)
    )
    if not selected:
        raise ValueError("Preflight found no balanced D50 sample.")
    selected_ids = {str(row["sample_id"]) for row in selected}
    candidate_sets = {
        sample_id: all_candidate_sets[sample_id] for sample_id in selected_ids
    }
    candidate_rows, errors, unique_polygons, unique_items = (
        build_expanded_candidate_rows(selected, candidate_sets)
    )
    aggregates = aggregate_expanded_candidate_ndvi(
        selected, candidate_sets, candidate_rows
    )
    height_summaries = summarize_expanded_height_classes(aggregates)
    binary_summaries = summarize_binary_height_groups(aggregates)

    selected_rows: list[dict[str, Any]] = []
    for sample in selected:
        sample_row: dict[str, Any] = dict(sample)
        sample_id = str(sample["sample_id"])
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            sample_row[f"candidate_polygon_count_{scenario}"] = len(
                candidate_sets[sample_id][scenario]
            )
        sample_row["H_EMBEDDED_target_date"] = EMBEDDED_TARGET_DATE.isoformat()
        sample_row["H_FILENAME_target_date"] = expanded_target_date(
            sample, "H_FILENAME"
        ).isoformat()
        sample_row["H_FILENAME_target_date_source"] = resolve_filename_target_date(
            sample
        )[1]
        selected_rows.append(sample_row)

    selected_path = output / "selected_samples_expanded.csv"
    candidate_path = output / "candidate_polygon_ndvi_expanded.csv"
    sample_path = output / "sample_calibration_expanded.csv"
    height_path = output / "height_class_summary_expanded.csv"
    binary_path = output / "binary_height_summary.csv"
    comparison_path = output / "temporal_hypothesis_comparison_expanded.json"
    selected_fields = list(selected_rows[0]) if selected_rows else ["sample_id"]
    _write_csv(selected_path, selected_rows, selected_fields)
    _write_csv(candidate_path, candidate_rows, EXPANDED_CANDIDATE_FIELDS)
    _write_csv(sample_path, aggregates, EXPANDED_SAMPLE_FIELDS)
    _write_csv(height_path, height_summaries, EXPANDED_HEIGHT_CLASS_FIELDS)
    _write_csv(binary_path, binary_summaries, BINARY_HEIGHT_FIELDS)
    chart_paths, skipped_charts = _write_expanded_boxplots(aggregates, output)
    comparison = build_expanded_comparison(
        selected,
        aggregates,
        candidate_rows,
        height_summaries,
        binary_summaries,
        preflight,
        unique_polygons_processed=unique_polygons,
        unique_sentinel_items=unique_items,
        elapsed_seconds=time.perf_counter() - started,
        errors=errors,
    )
    elapsed_seconds = time.perf_counter() - started
    comparison["performance"]["elapsed_seconds"] = elapsed_seconds
    comparison["charts"] = {
        "generated": [str(path) for path in chart_paths],
        "skipped": skipped_charts,
    }
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    coverage = {
        hypothesis: {
            scenario: comparison["hypotheses"][hypothesis]["scenarios"][scenario][
                "coverage"
            ]["n_with_valid_sentinel"]
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        }
        for hypothesis in EXPANDED_HYPOTHESES
    }
    return {
        "preflight_availability_json": str(preflight_path),
        "selected_samples_expanded_csv": str(selected_path),
        "candidate_polygon_ndvi_expanded_csv": str(candidate_path),
        "sample_calibration_expanded_csv": str(sample_path),
        "height_class_summary_expanded_csv": str(height_path),
        "binary_height_summary_csv": str(binary_path),
        "temporal_hypothesis_comparison_expanded_json": str(comparison_path),
        "sample_count": len(selected),
        "selected_by_class": comparison["selection"]["selected_by_class"],
        "selected_by_snapshot_and_class": comparison["selection"][
            "selected_by_snapshot_and_class"
        ],
        "valid_sentinel_samples": coverage,
        "unique_polygons_processed": unique_polygons,
        "unique_sentinel_items_used": unique_items,
        "elapsed_seconds": elapsed_seconds,
        "temporal_hypothesis_conclusion": comparison[
            "temporal_hypothesis_comparison"
        ]["conclusion"],
        "model_experiment_decision": comparison["model_experiment_decision"][
            "decision"
        ],
        "execution_errors": len(errors),
        "charts_generated": len(chart_paths),
        "charts_skipped": skipped_charts,
    }


def run_candidate_set_analysis(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected = stratified_sample(load_calibration_rows(dataset), sample_size)
    features = load_management_features(management_kmz)
    markers = load_km_markers(km_markers_kmz)
    candidate_sets = build_candidate_polygon_sets(selected, features, markers)
    candidate_rows, errors, unique_polygons_processed = (
        build_candidate_polygon_ndvi_rows(selected, candidate_sets)
    )
    aggregates = aggregate_candidate_ndvi(selected, candidate_sets, candidate_rows)
    class_summaries = summarize_candidate_ndvi_by_class(aggregates)

    candidate_path = output / "candidate_polygon_ndvi.csv"
    aggregates_path = output / "sample_uncertainty_aggregates.csv"
    class_summary_path = output / "height_class_summary.csv"
    sensitivity_path = output / "temporal_hypothesis_sensitivity.json"
    _write_csv(candidate_path, candidate_rows, CANDIDATE_NDVI_FIELDS)
    _write_csv(aggregates_path, aggregates, SAMPLE_UNCERTAINTY_FIELDS)
    _write_csv(class_summary_path, class_summaries, HEIGHT_CLASS_CANDIDATE_FIELDS)
    chart_paths = _write_candidate_boxplots(aggregates, output)
    elapsed_seconds = time.perf_counter() - started
    report = build_candidate_sensitivity_report(
        selected,
        aggregates,
        class_summaries,
        dataset=dataset,
        management_kmz=management_kmz,
        km_markers_kmz=km_markers_kmz,
        unique_polygons_processed=unique_polygons_processed,
        errors=errors,
        elapsed_seconds=elapsed_seconds,
    )
    sensitivity_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    scenario_counts = {
        scenario: {
            "samples_with_candidates": sum(
                bool(candidate_sets[str(sample["sample_id"])][scenario])
                for sample in selected
            ),
            "valid_ndvi_by_hypothesis": {
                hypothesis: sum(
                    row.get("hypothesis") == hypothesis
                    and row.get("distance_scenario") == scenario
                    and int(row.get("candidate_with_valid_scene_count") or 0) > 0
                    for row in aggregates
                )
                for hypothesis in HYPOTHESES
            },
        }
        for scenario in DISTANCE_SCENARIOS
    }
    return {
        "candidate_polygon_ndvi_csv": str(candidate_path),
        "sample_uncertainty_aggregates_csv": str(aggregates_path),
        "height_class_summary_csv": str(class_summary_path),
        "temporal_hypothesis_sensitivity_json": str(sensitivity_path),
        "charts": [str(path) for path in chart_paths],
        "sample_size": len(selected),
        "selected_by_class": {
            str(level): [
                str(sample["sample_id"])
                for sample in selected
                if _height_level(sample) == level
            ]
            for level in sorted(HEIGHT_LABELS)
        },
        "unique_candidate_polygons_processed": unique_polygons_processed,
        "scenario_counts": scenario_counts,
        "candidate_rows": len(candidate_rows),
        "aggregate_rows": len(aggregates),
        "execution_errors": len(errors),
        "elapsed_seconds": elapsed_seconds,
    }


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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--spatial-diagnostics-only",
        action="store_true",
        help="Generate spatial diagnostics without querying Sentinel-2.",
    )
    modes.add_argument(
        "--candidate-set-analysis",
        action="store_true",
        help=(
            "Analyze ambiguous candidate polygon sets at D20/D50/D100 without "
            "changing operational matching."
        ),
    )
    modes.add_argument(
        "--expanded-preflight-only",
        action="store_true",
        help="Compute D50/D100 expanded-analysis availability without Sentinel-2.",
    )
    modes.add_argument(
        "--expanded-calibration-analysis",
        action="store_true",
        help="Run balanced H_EMBEDDED versus per-record H_FILENAME analysis.",
    )
    arguments = parser.parse_args()
    if arguments.sample_size <= 0:
        parser.error("--sample-size must be greater than zero")
    return arguments


def main() -> int:
    arguments = _arguments()
    if arguments.spatial_diagnostics_only:
        runner = run_spatial_diagnostics
    elif arguments.expanded_preflight_only:
        runner = run_expanded_preflight
    elif arguments.expanded_calibration_analysis:
        runner = run_expanded_calibration
    elif arguments.candidate_set_analysis:
        runner = run_candidate_set_analysis
    else:
        runner = run_analysis
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
