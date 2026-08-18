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
from src.satellite_monitoring.raster_processing import (
    RasterProcessingError,
    SCL_EXCLUDED_CLASSES,
    find_asset_key,
    read_scene_bands,
)
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
SPECTRAL_BAND_SPECS: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "blue": ({"blue"}, ("B02", "blue")),
    "green": ({"green"}, ("B03", "green")),
    "red": ({"red"}, ("B04", "red")),
    "red_edge_1": ({"rededge"}, ("B05",)),
    "red_edge_2": ({"rededge"}, ("B06",)),
    "red_edge_3": ({"rededge"}, ("B07",)),
    "nir": ({"nir", "nir08"}, ("B08", "nir")),
    "narrow_nir": ({"nir09", "nir"}, ("B8A",)),
    "swir1": ({"swir16"}, ("B11", "swir16")),
    "swir2": ({"swir22"}, ("B12", "swir22")),
}
SPECTRAL_INDEX_NAMES = ("ndvi", "evi", "savi", "ndre")
SPECTRAL_FEATURE_NAMES = tuple(SPECTRAL_BAND_SPECS) + SPECTRAL_INDEX_NAMES
SPECTRAL_EFFECTIVE_RESOLUTION_M = 10.0
SPECTRAL_CAUSAL_LOOKBACK_DAYS = 30
METHODOLOGY_REVISION = "causal_scene_selection_and_reflectance_corrected"
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
SPECTRAL_SAMPLE_FIELDS = (
    "methodology_revision",
    "pair_id",
    "sample_id",
    "snapshot_role",
    "source_snapshot_date",
    "target_date",
    "height_class",
    "road",
    "km_m",
    "km",
    "asset_component",
    "distance_scenario",
    "candidate_geometry_id",
    "candidate_distance_m",
    "ground_truth_assignment",
    "sentinel_item_id",
    "sentinel_scene_date",
    "temporal_delta_days",
    "temporal_lag_days",
    "quality_status",
    "scene_quality_score",
    "spectral_processing_status",
    "spectral_valid_pixel_count",
    "spectral_valid_pixel_percentage",
    "effective_resolution_m",
    "resampling_strategy",
    "reflectance_scale_source",
    "reflectance_scale",
    "reflectance_offset",
    "shared_geometry_count",
    "shared_sentinel_item_count",
    "group_geometry_id",
    "group_km_id",
    "group_sentinel_acquisition_id",
    *SPECTRAL_FEATURE_NAMES,
)
PAIRED_SAMPLE_FIELDS = (
    "methodology_revision",
    "pair_id",
    "road",
    "km_m",
    "km",
    "asset_component",
    "sample_id_before",
    "sample_id_after",
    "class_before",
    "class_after",
    "class_delta",
    "class_transition",
    "binary_transition",
    "distance_scenario",
    "candidate_polygon_count",
    "candidate_with_valid_pair_count",
    "candidate_geometry_id",
    "sentinel_item_before",
    "sentinel_item_after",
    "temporal_lag_before_days",
    "temporal_lag_after_days",
    "same_acquisition_before_after",
    "shared_geometry_count",
    "shared_sentinel_item_before_count",
    "shared_sentinel_item_after_count",
    "group_geometry_id",
    "group_km_id",
    "group_sentinel_acquisition_id",
    "ground_truth_assignment",
)
SPECTRAL_DELTA_FIELDS = PAIRED_SAMPLE_FIELDS + tuple(
    field
    for feature in SPECTRAL_FEATURE_NAMES
    for field in (f"{feature}_before", f"{feature}_after", f"delta_{feature}")
)
FEATURE_CROSS_SECTIONAL_FIELDS = (
    "methodology_revision",
    "distance_scenario",
    "feature",
    "binary_height_group",
    "n",
    "mean",
    "median",
    "std",
    "q1",
    "q3",
    "auc_feature",
    "auc_directional_inverted",
    "directional_auc",
    "valid_pair_local_count",
    "unique_geometry_count",
    "unique_sentinel_acquisition_count",
)
FEATURE_DELTA_SUMMARY_FIELDS = (
    "methodology_revision",
    "distance_scenario",
    "feature",
    "grouping",
    "group",
    "n",
    "mean",
    "median",
    "std",
    "q1",
    "q3",
    "spearman_class_delta",
    "spearman_p_value",
    "spearman_n",
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


@dataclass(frozen=True)
class TemporalPair:
    pair_id: str
    road: str
    km_m: str
    km: str
    component: str
    before: dict[str, str]
    after: dict[str, str]
    class_before: int
    class_after: int
    class_delta: int
    class_transition: str
    binary_transition: str


@dataclass(frozen=True)
class SpectralSceneFeatures:
    values: dict[str, float]
    valid_pixel_count: int
    valid_pixel_percentage: float
    effective_resolution_m: float
    asset_keys: dict[str, str]
    reflectance_scale_source: str
    reflectance_scale: float
    reflectance_offset: float


@dataclass(frozen=True)
class ReflectanceScaling:
    source: str
    scale: float
    offset: float


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


def select_best_causal_scene(
    records: Sequence[Mapping[str, Any]],
    target_date: date,
    *,
    lookback_days: int = SPECTRAL_CAUSAL_LOOKBACK_DAYS,
) -> dict[str, Any] | None:
    """Seleciona cena aceita no passado, sem alterar a selecao operacional."""
    if lookback_days < 0:
        raise ValueError("lookback_days cannot be negative.")
    # Janela inclusiva de 30 datas: target e os 29 dias anteriores.
    start_date = target_date - timedelta(days=max(lookback_days - 1, 0))
    eligible: list[tuple[Mapping[str, Any], date]] = []
    for record in records:
        scene_date = _parse_scene_date(record)
        if (
            record.get("accepted_for_timeseries") is True
            and scene_date is not None
            and start_date <= scene_date <= target_date
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
    lag_days = (target_date - scene_date).days
    if lag_days < 0:
        raise ValueError("Causal scene selection produced a future scene.")
    result["sentinel_scene_date"] = scene_date.isoformat()
    result["temporal_lag_days"] = lag_days
    result["temporal_delta_days"] = (scene_date - target_date).days
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


def classify_class_transition(class_before: int, class_after: int) -> str:
    if class_after > class_before:
        return "INCREASE"
    if class_after < class_before:
        return "DECREASE"
    return "STABLE"


def classify_binary_transition(class_before: int, class_after: int) -> str:
    if class_before <= 2 and class_after == 3:
        return "CROSSED_ABOVE_30"
    if class_before == 3 and class_after <= 2:
        return "CROSSED_BELOW_OR_EQUAL_30"
    return "STAYED_SAME_SIDE"


def build_temporal_pairs(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[TemporalPair], list[dict[str, Any]]]:
    """Pareia snapshots por identificadores explicitos, sem proximidade geografica."""
    eligible, provenance_excluded = _expanded_eligible_rows(rows)
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in eligible:
        key = (
            str(row.get("road") or ""),
            str(row.get("km_m") or ""),
            str(row.get("asset_component") or ""),
        )
        grouped.setdefault(key, []).append(row)
    pairs: list[TemporalPair] = []
    excluded: list[dict[str, Any]] = list(provenance_excluded)
    for index, (key, group) in enumerate(sorted(grouped.items()), start=1):
        by_snapshot: dict[str, list[dict[str, str]]] = {}
        for row in group:
            by_snapshot.setdefault(str(row.get("source_snapshot_date") or ""), []).append(
                row
            )
        before_rows = by_snapshot.get("2026-03-13", [])
        after_rows = by_snapshot.get("2026-03-20", [])
        if len(before_rows) != 1 or len(after_rows) != 1 or len(group) != 2:
            excluded.append(
                {
                    "pair_key": list(key),
                    "reason": "Expected exactly one record for each required snapshot.",
                    "record_count": len(group),
                }
            )
            continue
        before = before_rows[0]
        after = after_rows[0]
        class_before = _height_level(before)
        class_after = _height_level(after)
        assert class_before is not None and class_after is not None
        pairs.append(
            TemporalPair(
                pair_id=f"PAIR_{index:04d}",
                road=key[0],
                km_m=key[1],
                km=str(before.get("km") or after.get("km") or ""),
                component=key[2],
                before=before,
                after=after,
                class_before=class_before,
                class_after=class_after,
                class_delta=class_after - class_before,
                class_transition=classify_class_transition(class_before, class_after),
                binary_transition=classify_binary_transition(class_before, class_after),
            )
        )
    return pairs, excluded


def pair_candidate_intersection(
    pair: TemporalPair,
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    scenario: str,
) -> list[CandidatePolygon]:
    before = {
        candidate.feature.feature_id: candidate
        for candidate in candidate_sets[str(pair.before["sample_id"])][scenario]
    }
    after = {
        candidate.feature.feature_id: candidate
        for candidate in candidate_sets[str(pair.after["sample_id"])][scenario]
    }
    return [
        CandidatePolygon(
            feature=before[feature_id].feature,
            distance_m=max(
                before[feature_id].distance_m, after[feature_id].distance_m
            ),
            semantic_match_reason="compatible_in_both_snapshots",
        )
        for feature_id in sorted(before.keys() & after.keys())
    ]


def build_spectral_temporal_preflight(
    rows: Sequence[Mapping[str, str]],
    features: Sequence[ManagementFeature],
    markers: Mapping[int, KmMarker],
) -> tuple[
    dict[str, Any],
    list[TemporalPair],
    dict[str, dict[str, list[CandidatePolygon]]],
]:
    pairs, excluded = build_temporal_pairs(rows)
    paired_rows = [row for pair in pairs for row in (pair.before, pair.after)]
    candidate_sets = build_candidate_polygon_sets(
        paired_rows,
        features,
        markers,
        scenarios=EXPANDED_SPATIAL_SCENARIOS,
    )
    transition_counts = {
        transition: sum(pair.class_transition == transition for pair in pairs)
        for transition in ("STABLE", "INCREASE", "DECREASE")
    }
    binary_counts = {
        transition: sum(pair.binary_transition == transition for pair in pairs)
        for transition in (
            "CROSSED_ABOVE_30",
            "CROSSED_BELOW_OR_EQUAL_30",
            "STAYED_SAME_SIDE",
        )
    }
    scenarios: dict[str, Any] = {}
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        eligible_pairs = [
            pair
            for pair in pairs
            if pair_candidate_intersection(pair, candidate_sets, scenario)
        ]
        scenarios[scenario] = {
            "pairs_with_shared_candidate_geometry": len(eligible_pairs),
            "transition_distribution": {
                transition: sum(
                    pair.class_transition == transition for pair in eligible_pairs
                )
                for transition in ("STABLE", "INCREASE", "DECREASE")
            },
            "binary_transition_distribution": {
                transition: sum(
                    pair.binary_transition == transition for pair in eligible_pairs
                )
                for transition in (
                    "CROSSED_ABOVE_30",
                    "CROSSED_BELOW_OR_EQUAL_30",
                    "STAYED_SAME_SIDE",
                )
            },
        }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "spectral_temporal_pair_preflight",
        "methodology_revision": METHODOLOGY_REVISION,
        "sentinel_2_queried": False,
        "documented_date_confirmation": False,
        "working_temporal_hypothesis": "H_FILENAME",
        "pairing_key": ["road", "km_m", "asset_component"],
        "pairing_uses_geographic_proximity": False,
        "pair_count": len(pairs),
        "excluded_pair_groups": excluded,
        "class_transition_distribution": transition_counts,
        "binary_transition_distribution": binary_counts,
        "scenarios": scenarios,
        "candidate_rule": (
            "Same candidate_geometry_id must be semantically compatible and within "
            "the scenario distance in both snapshots."
        ),
        "ground_truth_polygon_assignment": False,
    }
    return report, pairs, candidate_sets


def run_spectral_temporal_preflight(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    del sample_size
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report, _, _ = build_spectral_temporal_preflight(
        load_calibration_rows(dataset),
        load_management_features(management_kmz),
        load_km_markers(km_markers_kmz),
    )
    path = output / "dependency_diagnostics.json"
    path.write_text(
        json.dumps({"preflight": report}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"dependency_diagnostics_json": str(path), **report}


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


def _safe_spectral_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan)
    np.divide(
        numerator,
        denominator,
        out=result,
        where=np.isfinite(denominator) & (np.abs(denominator) > 1e-12),
    )
    return result


def calculate_spectral_indices(
    bands: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Calcula apenas os quatro indices solicitados em reflectancia alinhada."""
    blue = np.asarray(bands["blue"], dtype=float)
    red = np.asarray(bands["red"], dtype=float)
    red_edge_1 = np.asarray(bands["red_edge_1"], dtype=float)
    nir = np.asarray(bands["nir"], dtype=float)
    narrow_nir = np.asarray(bands["narrow_nir"], dtype=float)
    return {
        "ndvi": _safe_spectral_ratio(nir - red, nir + red),
        "evi": 2.5
        * _safe_spectral_ratio(nir - red, nir + 6.0 * red - 7.5 * blue + 1.0),
        "savi": 1.5 * _safe_spectral_ratio(nir - red, nir + red + 0.5),
        "ndre": _safe_spectral_ratio(
            narrow_nir - red_edge_1, narrow_nir + red_edge_1
        ),
    }


def apply_reflectance_scaling(
    values: np.ndarray, *, scale: float, offset: float
) -> np.ndarray:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Reflectance scale must be finite and positive.")
    if not math.isfinite(offset):
        raise ValueError("Reflectance offset must be finite.")
    return np.asarray(values, dtype=float) * scale + offset


_REFLECTANCE_SCALING_CACHE: dict[str, ReflectanceScaling] = {}


def resolve_reflectance_scaling(
    item: Any,
    *,
    dataset_scale: float = 1.0,
    dataset_offset: float = 0.0,
) -> ReflectanceScaling:
    """Resolve scale/offset por metadata explicita; nao presume DN/10000 cegamente."""
    item_id = str(getattr(item, "id", "") or "")
    if item_id in _REFLECTANCE_SCALING_CACHE:
        return _REFLECTANCE_SCALING_CACHE[item_id]

    red_key = _spectral_asset_key(item, {"red"}, ("B04", "red"))
    if red_key is None:
        raise RasterProcessingError("Cannot inspect reflectance scaling without B04.")
    raster_bands = item.assets[red_key].extra_fields.get("raster:bands", [])
    if isinstance(raster_bands, dict):
        raster_bands = [raster_bands]
    if raster_bands and (
        "scale" in raster_bands[0] or "offset" in raster_bands[0]
    ):
        scaling = ReflectanceScaling(
            source="asset_raster_bands",
            scale=float(raster_bands[0].get("scale", 1.0)),
            offset=float(raster_bands[0].get("offset", 0.0)),
        )
        _REFLECTANCE_SCALING_CACHE[item_id] = scaling
        return scaling
    if dataset_scale != 1.0 or dataset_offset != 0.0:
        scaling = ReflectanceScaling(
            source="geotiff_dataset_scale_offset",
            scale=float(dataset_scale),
            offset=float(dataset_offset),
        )
        _REFLECTANCE_SCALING_CACHE[item_id] = scaling
        return scaling

    metadata_asset = item.assets.get("product-metadata")
    if metadata_asset is None:
        raise RasterProcessingError(
            "Reflectance scale is absent from asset/GeoTIFF and product metadata is unavailable."
        )
    try:
        import urllib.request

        xml_data = urllib.request.urlopen(metadata_asset.href, timeout=30).read()
        root = ElementTree.fromstring(xml_data)
        quantification_values = [
            float((node.text or "").strip())
            for node in root.iter()
            if _local_name(node.tag) == "BOA_QUANTIFICATION_VALUE"
            and (node.text or "").strip()
        ]
        offsets = [
            float((node.text or "").strip())
            for node in root.iter()
            if _local_name(node.tag) == "BOA_ADD_OFFSET"
            and (node.text or "").strip()
        ]
    except Exception as exc:
        raise RasterProcessingError(
            f"Cannot read product reflectance metadata: {type(exc).__name__}: {exc}"
        ) from exc
    if len(quantification_values) != 1 or quantification_values[0] <= 0:
        raise RasterProcessingError("Invalid BOA_QUANTIFICATION_VALUE metadata.")
    if not offsets or len(set(offsets)) != 1:
        raise RasterProcessingError("BOA_ADD_OFFSET is missing or differs between bands.")
    quantification = quantification_values[0]
    scaling = ReflectanceScaling(
        source="product_metadata_BOA_QUANTIFICATION_VALUE_and_BOA_ADD_OFFSET",
        scale=1.0 / quantification,
        offset=offsets[0] / quantification,
    )
    _REFLECTANCE_SCALING_CACHE[item_id] = scaling
    return scaling


def _spectral_asset_key(
    item: Any, common_names: set[str], fallback_keys: tuple[str, ...]
) -> str | None:
    keys_by_lowercase = {key.lower(): key for key in item.assets}
    for fallback in fallback_keys:
        if fallback.lower() in keys_by_lowercase:
            return keys_by_lowercase[fallback.lower()]
    return find_asset_key(item, common_names, fallback_keys)


def read_multispectral_features(
    item: Any, aoi_geojson: dict[str, Any]
) -> SpectralSceneFeatures:
    """Le bandas na grade B04 de 10 m; reflectancia bilinear e SCL nearest."""
    from contextlib import ExitStack

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask, geometry_window
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_geom

    asset_keys = {
        name: _spectral_asset_key(item, common_names, fallback_keys)
        for name, (common_names, fallback_keys) in SPECTRAL_BAND_SPECS.items()
    }
    missing = [name for name, key in asset_keys.items() if key is None]
    if missing:
        raise RasterProcessingError(
            "Missing spectral assets: " + ", ".join(sorted(missing))
        )
    resolved_keys = {name: str(key) for name, key in asset_keys.items()}
    scl_key = _spectral_asset_key(item, {"scl"}, ("SCL", "scl"))
    red_asset = item.assets[resolved_keys["red"]]
    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    try:
        with rasterio.Env(**env_options), ExitStack() as stack:
            reference = stack.enter_context(rasterio.open(red_asset.href))
            scaling = resolve_reflectance_scaling(
                item,
                dataset_scale=float(reference.scales[0]),
                dataset_offset=float(reference.offsets[0]),
            )
            aoi = transform_geom("EPSG:4326", reference.crs, aoi_geojson, precision=15)
            window = geometry_window(reference, [aoi])
            output_transform = reference.window_transform(window)
            output_shape = (int(window.height), int(window.width))
            inside_aoi = geometry_mask(
                [aoi],
                out_shape=output_shape,
                transform=output_transform,
                invert=True,
            )
            total_pixels = int(np.count_nonzero(inside_aoi))
            if total_pixels == 0:
                raise RasterProcessingError(
                    "The AOI contains no pixels in the 10 m reference grid."
                )
            raw_band_values: dict[str, np.ndarray] = {}
            physical_band_values: dict[str, np.ndarray] = {}
            common_valid = inside_aoi.copy()
            for name, key in resolved_keys.items():
                asset = item.assets[key]
                if name == "red":
                    raw = reference.read(1, window=window, masked=True)
                else:
                    source = stack.enter_context(rasterio.open(asset.href))
                    vrt = stack.enter_context(
                        WarpedVRT(
                            source,
                            crs=reference.crs,
                            transform=reference.transform,
                            width=reference.width,
                            height=reference.height,
                            resampling=Resampling.bilinear,
                        )
                    )
                    raw = vrt.read(1, window=window, masked=True)
                values = np.asarray(raw.data, dtype=np.float32)
                valid = ~np.ma.getmaskarray(raw)
                valid &= np.isfinite(values)
                valid &= values != 0
                raw_band_values[name] = values
                physical_band_values[name] = apply_reflectance_scaling(
                    values, scale=scaling.scale, offset=scaling.offset
                )
                common_valid &= valid
            if scl_key is not None:
                scl_asset = item.assets[scl_key]
                scl_source = stack.enter_context(rasterio.open(scl_asset.href))
                scl_vrt = stack.enter_context(
                    WarpedVRT(
                        scl_source,
                        crs=reference.crs,
                        transform=reference.transform,
                        width=reference.width,
                        height=reference.height,
                        resampling=Resampling.nearest,
                    )
                )
                scl = scl_vrt.read(1, window=window, masked=True)
                common_valid &= ~np.ma.getmaskarray(scl)
                common_valid &= ~np.isin(
                    np.asarray(scl.data), list(SCL_EXCLUDED_CLASSES)
                )
            valid_count = int(np.count_nonzero(common_valid))
            if valid_count == 0:
                raise RasterProcessingError(
                    "No common valid pixels remain across all spectral bands and SCL."
                )
            index_values = calculate_spectral_indices(physical_band_values)
            all_values = {**raw_band_values, **index_values}
            statistics = {
                name: float(np.median(values[common_valid & np.isfinite(values)]))
                for name, values in all_values.items()
                if np.count_nonzero(common_valid & np.isfinite(values)) > 0
            }
            missing_statistics = set(SPECTRAL_FEATURE_NAMES) - set(statistics)
            if missing_statistics:
                raise RasterProcessingError(
                    "No finite values for: " + ", ".join(sorted(missing_statistics))
                )
            resolution = max(
                abs(float(reference.transform.a)), abs(float(reference.transform.e))
            )
    except RasterProcessingError:
        raise
    except Exception as exc:
        raise RasterProcessingError(
            f"Failed isolated multispectral read: {type(exc).__name__}: {exc}"
        ) from exc
    return SpectralSceneFeatures(
        values=statistics,
        valid_pixel_count=valid_count,
        valid_pixel_percentage=valid_count / total_pixels * 100.0,
        effective_resolution_m=resolution,
        asset_keys=resolved_keys,
        reflectance_scale_source=scaling.source,
        reflectance_scale=scaling.scale,
        reflectance_offset=scaling.offset,
    )


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


def _query_and_process_spectral_scenes(
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
    records: list[dict[str, Any]] = []
    for scene in result.scenes:
        record = _process_scene(scene, aoi_geojson, config)
        record["_scene_item"] = scene.item
        records.append(record)
    return records, None


def build_spectral_observation_cache(
    features: Sequence[ManagementFeature],
    *,
    query_scenes: Callable[[Any, date, date], tuple[list[dict[str, Any]], str | None]] = (
        _query_and_process_spectral_scenes
    ),
    read_features: Callable[[Any, dict[str, Any]], SpectralSceneFeatures] = (
        read_multispectral_features
    ),
) -> tuple[
    dict[str, dict[date, dict[str, Any] | None]],
    list[dict[str, str]],
    int,
]:
    start = date(2026, 3, 13) - timedelta(days=SPECTRAL_CAUSAL_LOOKBACK_DAYS - 1)
    end = date(2026, 3, 20)
    observations: dict[str, dict[date, dict[str, Any] | None]] = {}
    errors: list[dict[str, str]] = []
    spectral_cache: dict[tuple[str, str], SpectralSceneFeatures] = {}
    sentinel_items: set[str] = set()
    for feature in sorted(features, key=lambda item: item.feature_id):
        records, query_error = query_scenes(feature.geometry, start, end)
        if query_error:
            errors.append(
                {
                    "candidate_geometry_id": feature.feature_id,
                    "stage": "scene_query",
                    "error": query_error,
                }
            )
        observations[feature.feature_id] = {}
        for target in sorted(FILENAME_TARGET_DATES):
            selected = select_best_causal_scene(records, target)
            if selected is None:
                observations[feature.feature_id][target] = None
                continue
            item_id = str(selected.get("item_id") or "")
            item = selected.get("_scene_item")
            if not item_id or item is None:
                observations[feature.feature_id][target] = None
                errors.append(
                    {
                        "candidate_geometry_id": feature.feature_id,
                        "stage": "spectral_item_lookup",
                        "error": "Selected scene lacks item_id or STAC item.",
                    }
                )
                continue
            cache_key = (feature.feature_id, item_id)
            if cache_key not in spectral_cache:
                try:
                    spectral_cache[cache_key] = read_features(item, mapping(feature.geometry))
                except Exception as exc:
                    errors.append(
                        {
                            "candidate_geometry_id": feature.feature_id,
                            "sentinel_item_id": item_id,
                            "stage": "multispectral_read",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    observations[feature.feature_id][target] = None
                    continue
            spectral = spectral_cache[cache_key]
            observation = {
                key: value for key, value in selected.items() if key != "_scene_item"
            }
            observation.update(spectral.values)
            observation.update(
                {
                    "spectral_valid_pixel_count": spectral.valid_pixel_count,
                    "spectral_valid_pixel_percentage": spectral.valid_pixel_percentage,
                    "effective_resolution_m": spectral.effective_resolution_m,
                    "spectral_asset_keys": spectral.asset_keys,
                    "spectral_processing_status": "VALID_SPECTRAL_FEATURES",
                    "reflectance_scale_source": spectral.reflectance_scale_source,
                    "reflectance_scale": spectral.reflectance_scale,
                    "reflectance_offset": spectral.reflectance_offset,
                }
            )
            if int(observation["temporal_lag_days"]) < 0:
                raise ValueError("Negative temporal_lag_days after causal selection.")
            observations[feature.feature_id][target] = observation
            sentinel_items.add(item_id)
    return observations, errors, len(sentinel_items)


def build_spectral_pair_outputs(
    pairs: Sequence[TemporalPair],
    candidate_sets: Mapping[str, Mapping[str, Sequence[CandidatePolygon]]],
    observations: Mapping[str, Mapping[date, Mapping[str, Any] | None]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    feature_rows: list[dict[str, Any]] = []
    for pair in pairs:
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            candidates = pair_candidate_intersection(pair, candidate_sets, scenario)
            for candidate in candidates:
                for role, sample, target in (
                    ("before", pair.before, date(2026, 3, 13)),
                    ("after", pair.after, date(2026, 3, 20)),
                ):
                    observation = observations.get(candidate.feature.feature_id, {}).get(
                        target
                    )
                    row: dict[str, Any] = {
                        "methodology_revision": METHODOLOGY_REVISION,
                        "pair_id": pair.pair_id,
                        "sample_id": sample.get("sample_id"),
                        "snapshot_role": role,
                        "source_snapshot_date": sample.get("source_snapshot_date"),
                        "target_date": target.isoformat(),
                        "height_class": _height_level(sample),
                        "road": pair.road,
                        "km_m": pair.km_m,
                        "km": pair.km,
                        "asset_component": pair.component,
                        "distance_scenario": scenario,
                        "candidate_geometry_id": candidate.feature.feature_id,
                        "candidate_distance_m": round(candidate.distance_m, 3),
                        "ground_truth_assignment": False,
                        "sentinel_item_id": None,
                        "sentinel_scene_date": None,
                        "temporal_delta_days": None,
                        "temporal_lag_days": None,
                        "quality_status": None,
                        "scene_quality_score": None,
                        "spectral_processing_status": "NO_VALID_SPECTRAL_SCENE",
                        "spectral_valid_pixel_count": 0,
                        "spectral_valid_pixel_percentage": 0.0,
                        "effective_resolution_m": SPECTRAL_EFFECTIVE_RESOLUTION_M,
                        "resampling_strategy": (
                            "B04 10 m reference grid; bilinear reflectance; nearest SCL"
                        ),
                        "reflectance_scale_source": None,
                        "reflectance_scale": None,
                        "reflectance_offset": None,
                        "group_geometry_id": candidate.feature.feature_id,
                        "group_km_id": f"{pair.road}|{pair.km_m}",
                        "group_sentinel_acquisition_id": None,
                    }
                    row.update({feature: None for feature in SPECTRAL_FEATURE_NAMES})
                    if observation is not None:
                        row.update(
                            {
                                "sentinel_item_id": observation.get("item_id"),
                                "sentinel_scene_date": observation.get(
                                    "sentinel_scene_date"
                                ),
                                "temporal_delta_days": observation.get(
                                    "temporal_delta_days"
                                ),
                                "temporal_lag_days": observation.get(
                                    "temporal_lag_days"
                                ),
                                "quality_status": observation.get("quality_status"),
                                "scene_quality_score": observation.get(
                                    "scene_quality_score"
                                ),
                                "spectral_processing_status": observation.get(
                                    "spectral_processing_status"
                                ),
                                "spectral_valid_pixel_count": observation.get(
                                    "spectral_valid_pixel_count"
                                ),
                                "spectral_valid_pixel_percentage": observation.get(
                                    "spectral_valid_pixel_percentage"
                                ),
                                "effective_resolution_m": observation.get(
                                    "effective_resolution_m"
                                ),
                                "reflectance_scale_source": observation.get(
                                    "reflectance_scale_source"
                                ),
                                "reflectance_scale": observation.get(
                                    "reflectance_scale"
                                ),
                                "reflectance_offset": observation.get(
                                    "reflectance_offset"
                                ),
                                "group_sentinel_acquisition_id": observation.get(
                                    "item_id"
                                ),
                            }
                        )
                        row.update(
                            {
                                feature: observation.get(feature)
                                for feature in SPECTRAL_FEATURE_NAMES
                            }
                        )
                    feature_rows.append(row)

    geometry_pairs: dict[str, set[str]] = {}
    scene_pairs: dict[str, set[str]] = {}
    for row in feature_rows:
        geometry_pairs.setdefault(str(row["candidate_geometry_id"]), set()).add(
            str(row["pair_id"])
        )
        if row.get("sentinel_item_id"):
            scene_pairs.setdefault(str(row["sentinel_item_id"]), set()).add(
                str(row["pair_id"])
            )
    for row in feature_rows:
        row["shared_geometry_count"] = len(
            geometry_pairs[str(row["candidate_geometry_id"])]
        )
        row["shared_sentinel_item_count"] = (
            len(scene_pairs[str(row["sentinel_item_id"])])
            if row.get("sentinel_item_id")
            else 0
        )

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in feature_rows:
        grouped.setdefault(
            (str(row["pair_id"]), str(row["distance_scenario"])), []
        ).append(row)
    paired_rows: list[dict[str, Any]] = []
    delta_rows: list[dict[str, Any]] = []
    for pair in pairs:
        for scenario in EXPANDED_SPATIAL_SCENARIOS:
            candidates = pair_candidate_intersection(pair, candidate_sets, scenario)
            rows = grouped.get((pair.pair_id, scenario), [])
            by_geometry: dict[str, dict[str, Mapping[str, Any]]] = {}
            for row in rows:
                by_geometry.setdefault(str(row["candidate_geometry_id"]), {})[
                    str(row["snapshot_role"])
                ] = row
            valid_geometries = [
                geometry_id
                for geometry_id, roles in by_geometry.items()
                if set(roles) == {"before", "after"}
                and all(
                    roles[role].get("spectral_processing_status")
                    == "VALID_SPECTRAL_FEATURES"
                    for role in ("before", "after")
                )
            ]
            before_items = sorted(
                {
                    str(by_geometry[geometry_id]["before"]["sentinel_item_id"])
                    for geometry_id in valid_geometries
                }
            )
            after_items = sorted(
                {
                    str(by_geometry[geometry_id]["after"]["sentinel_item_id"])
                    for geometry_id in valid_geometries
                }
            )
            geometry_ids = sorted(
                candidate.feature.feature_id for candidate in candidates
            )
            base: dict[str, Any] = {
                "methodology_revision": METHODOLOGY_REVISION,
                "pair_id": pair.pair_id,
                "road": pair.road,
                "km_m": pair.km_m,
                "km": pair.km,
                "asset_component": pair.component,
                "sample_id_before": pair.before.get("sample_id"),
                "sample_id_after": pair.after.get("sample_id"),
                "class_before": pair.class_before,
                "class_after": pair.class_after,
                "class_delta": pair.class_delta,
                "class_transition": pair.class_transition,
                "binary_transition": pair.binary_transition,
                "distance_scenario": scenario,
                "candidate_polygon_count": len(candidates),
                "candidate_with_valid_pair_count": len(valid_geometries),
                "candidate_geometry_id": ";".join(geometry_ids),
                "sentinel_item_before": ";".join(before_items),
                "sentinel_item_after": ";".join(after_items),
                "temporal_lag_before_days": None,
                "temporal_lag_after_days": None,
                "same_acquisition_before_after": False,
                "shared_geometry_count": max(
                    (
                        int(row["shared_geometry_count"])
                        for row in rows
                        if str(row["candidate_geometry_id"]) in valid_geometries
                    ),
                    default=0,
                ),
                "shared_sentinel_item_before_count": max(
                    (
                        int(by_geometry[geometry_id]["before"][
                            "shared_sentinel_item_count"
                        ])
                        for geometry_id in valid_geometries
                    ),
                    default=0,
                ),
                "shared_sentinel_item_after_count": max(
                    (
                        int(by_geometry[geometry_id]["after"][
                            "shared_sentinel_item_count"
                        ])
                        for geometry_id in valid_geometries
                    ),
                    default=0,
                ),
                "group_geometry_id": ";".join(valid_geometries),
                "group_km_id": f"{pair.road}|{pair.km_m}",
                "group_sentinel_acquisition_id": ";".join(
                    sorted(set(before_items + after_items))
                ),
                "ground_truth_assignment": False,
            }
            if valid_geometries:
                before_lags = _finite_values(
                    by_geometry[geometry_id]["before"].get("temporal_lag_days")
                    for geometry_id in valid_geometries
                )
                after_lags = _finite_values(
                    by_geometry[geometry_id]["after"].get("temporal_lag_days")
                    for geometry_id in valid_geometries
                )
                base["temporal_lag_before_days"] = (
                    float(np.median(before_lags)) if before_lags.size else None
                )
                base["temporal_lag_after_days"] = (
                    float(np.median(after_lags)) if after_lags.size else None
                )
                base["same_acquisition_before_after"] = bool(
                    before_items and set(before_items) == set(after_items)
                )
            paired_rows.append(dict(base))
            delta = dict(base)
            for feature in SPECTRAL_FEATURE_NAMES:
                before_values = _finite_values(
                    by_geometry[geometry_id]["before"].get(feature)
                    for geometry_id in valid_geometries
                )
                after_values = _finite_values(
                    by_geometry[geometry_id]["after"].get(feature)
                    for geometry_id in valid_geometries
                )
                before_value = (
                    float(np.median(before_values)) if before_values.size else None
                )
                after_value = (
                    float(np.median(after_values)) if after_values.size else None
                )
                delta[f"{feature}_before"] = before_value
                delta[f"{feature}_after"] = after_value
                delta[f"delta_{feature}"] = (
                    after_value - before_value
                    if before_value is not None and after_value is not None
                    else None
                )
            delta_rows.append(delta)
    return feature_rows, paired_rows, delta_rows


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


def _numeric_distribution(values: Iterable[Any]) -> dict[str, Any]:
    data = _finite_values(values)
    return {
        "n": int(data.size),
        "mean": float(np.mean(data)) if data.size else None,
        "median": float(np.median(data)) if data.size else None,
        "std": float(np.std(data, ddof=1)) if data.size >= 2 else None,
        "q1": float(np.quantile(data, 0.25)) if data.size else None,
        "q3": float(np.quantile(data, 0.75)) if data.size else None,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _numeric_spearman(x_values: Sequence[Any], y_values: Sequence[Any]) -> dict[str, Any]:
    valid: list[tuple[float, float]] = []
    for x_value, y_value in zip(x_values, y_values):
        try:
            x_number = float(x_value)
            y_number = float(y_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x_number) and math.isfinite(y_number):
            valid.append((x_number, y_number))
    if len(valid) < 3 or len({value[0] for value in valid}) < 2:
        return {"status": "INSUFFICIENT_SAMPLE", "n": len(valid), "correlation": None, "p_value": None}
    try:
        from scipy.stats import spearmanr

        result = spearmanr(
            [value[0] for value in valid], [value[1] for value in valid]
        )
        correlation = float(result.statistic)
        p_value = float(result.pvalue)
        if not math.isfinite(correlation):
            raise ValueError("Non-finite Spearman correlation.")
        return {
            "status": "CALCULATED",
            "n": len(valid),
            "correlation": correlation,
            "p_value": p_value if math.isfinite(p_value) else None,
        }
    except (ImportError, ValueError):
        return {"status": "INSUFFICIENT_SAMPLE", "n": len(valid), "correlation": None, "p_value": None}


def summarize_spectral_cross_sectional(
    delta_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    summaries: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        metrics[scenario] = {}
        scenario_rows = [
            row for row in delta_rows if row.get("distance_scenario") == scenario
        ]
        for feature in SPECTRAL_FEATURE_NAMES:
            observations: list[dict[str, Any]] = []
            for row in scenario_rows:
                for role in ("before", "after"):
                    value = row.get(f"{feature}_{role}")
                    if value is None:
                        continue
                    observations.append(
                        {
                            "pair_id": row["pair_id"],
                            "height_class": row[f"class_{role}"],
                            "value": float(value),
                            "geometry_ids": str(row.get("group_geometry_id") or ""),
                            "sentinel_items": str(row.get(f"sentinel_item_{role}") or ""),
                        }
                    )
            low = _finite_values(
                item["value"] for item in observations if int(item["height_class"]) <= 2
            )
            high = _finite_values(
                item["value"] for item in observations if int(item["height_class"]) == 3
            )
            sufficient = low.size >= 2 and high.size >= 2
            auc = _auc_from_groups(low, high) if sufficient else None
            auc_inverted = (
                _auc_from_groups(low, high, negate=True) if sufficient else None
            )
            geometry_ids = {
                geometry_id
                for item in observations
                for geometry_id in item["geometry_ids"].split(";")
                if geometry_id
            }
            sentinel_items = {
                item_id
                for item in observations
                for item_id in item["sentinel_items"].split(";")
                if item_id
            }
            metrics[scenario][feature] = {
                "n": len(observations),
                "low_n": int(low.size),
                "high_n": int(high.size),
                "auc_feature": auc,
                "auc_directional_inverted": auc_inverted,
                "directional_auc": (
                    max(auc, auc_inverted)
                    if auc is not None and auc_inverted is not None
                    else None
                ),
                "valid_pair_local_count": len(
                    {str(item["pair_id"]) for item in observations}
                ),
                "unique_geometry_count": len(geometry_ids),
                "unique_sentinel_acquisition_count": len(sentinel_items),
            }
            for label, values in (
                ("LOW_OR_ACCEPTABLE", low),
                ("HIGH", high),
            ):
                summary = _numeric_distribution(values)
                summaries.append(
                    {
                        "methodology_revision": METHODOLOGY_REVISION,
                        "distance_scenario": scenario,
                        "feature": feature,
                        "binary_height_group": label,
                        **summary,
                        **{
                            key: metrics[scenario][feature][key]
                            for key in (
                                "auc_feature",
                                "auc_directional_inverted",
                                "directional_auc",
                                "valid_pair_local_count",
                                "unique_geometry_count",
                                "unique_sentinel_acquisition_count",
                            )
                        },
                    }
                )
    return summaries, metrics


def summarize_spectral_deltas(
    delta_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    summaries: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        metrics[scenario] = {}
        scenario_rows = [
            row for row in delta_rows if row.get("distance_scenario") == scenario
        ]
        eligible_count = sum(
            int(row.get("candidate_polygon_count") or 0) > 0 for row in scenario_rows
        )
        for feature in SPECTRAL_FEATURE_NAMES:
            valid = [
                row
                for row in scenario_rows
                if row.get(f"delta_{feature}") is not None
                and not _truthy(row.get("same_acquisition_before_after"))
            ]
            spearman = _numeric_spearman(
                [row["class_delta"] for row in valid],
                [row[f"delta_{feature}"] for row in valid],
            )
            transition_medians: list[float] = []
            for grouping, groups in (
                ("CLASS_TRANSITION", ("STABLE", "INCREASE", "DECREASE")),
                (
                    "BINARY_TRANSITION",
                    (
                        "CROSSED_ABOVE_30",
                        "CROSSED_BELOW_OR_EQUAL_30",
                        "STAYED_SAME_SIDE",
                    ),
                ),
            ):
                for group_name in groups:
                    group_rows = [
                        row
                        for row in valid
                        if row[
                            "class_transition"
                            if grouping == "CLASS_TRANSITION"
                            else "binary_transition"
                        ]
                        == group_name
                    ]
                    distribution = _numeric_distribution(
                        row[f"delta_{feature}"] for row in group_rows
                    )
                    if grouping == "CLASS_TRANSITION" and distribution["median"] is not None:
                        transition_medians.append(float(distribution["median"]))
                    summaries.append(
                        {
                            "methodology_revision": METHODOLOGY_REVISION,
                            "distance_scenario": scenario,
                            "feature": feature,
                            "grouping": grouping,
                            "group": group_name,
                            **distribution,
                            "spearman_class_delta": spearman["correlation"],
                            "spearman_p_value": spearman["p_value"],
                            "spearman_n": spearman["n"],
                        }
                    )
            all_delta = _finite_values(row[f"delta_{feature}"] for row in valid)
            overall_iqr = (
                float(np.quantile(all_delta, 0.75) - np.quantile(all_delta, 0.25))
                if all_delta.size
                else None
            )
            median_range = (
                max(transition_medians) - min(transition_medians)
                if len(transition_medians) >= 2
                else None
            )
            metrics[scenario][feature] = {
                "spearman": spearman,
                "valid_pair_count": len(valid),
                "eligible_pair_count": eligible_count,
                "coverage": len(valid) / eligible_count if eligible_count else 0.0,
                "class_transition_median_range": median_range,
                "normalized_transition_separation": (
                    median_range / overall_iqr
                    if median_range is not None
                    and overall_iqr is not None
                    and overall_iqr > 0
                    else None
                ),
            }
    return summaries, metrics


def build_spectral_feature_ranking(
    delta_metrics: Mapping[str, Mapping[str, Mapping[str, Any]]],
    cross_metrics: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    unique_sentinel_acquisitions: int,
) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for feature in SPECTRAL_FEATURE_NAMES:
        scenario_data = {
            scenario: dict(delta_metrics[scenario][feature])
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        }
        correlations = [
            scenario_data[scenario]["spearman"]["correlation"]
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        ]
        calculable = all(value is not None for value in correlations)
        sign_stable = calculable and len({_sign(value) for value in correlations}) == 1
        minimum_magnitude = (
            min(abs(float(value)) for value in correlations) if calculable else None
        )
        minimum_coverage = min(
            float(scenario_data[scenario]["coverage"])
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        )
        separations = [
            scenario_data[scenario]["normalized_transition_separation"]
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        ]
        minimum_separation = (
            min(float(value) for value in separations)
            if all(value is not None for value in separations)
            else None
        )
        minimum_valid_pairs = min(
            int(scenario_data[scenario]["valid_pair_count"])
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        )
        if minimum_valid_pairs < 20 or not calculable:
            status = "INSUFFICIENT_DATA"
        elif not sign_stable:
            status = "UNSTABLE"
        elif (
            minimum_magnitude is not None
            and minimum_magnitude >= 0.20
            and minimum_coverage >= 0.50
            and minimum_separation is not None
            and minimum_separation >= 0.50
        ):
            status = "PROMISING"
        else:
            status = "WEAK"
        ranked.append(
            {
                "feature": feature,
                "classification": status,
                "spearman_sign_stable_D50_D100": sign_stable,
                "minimum_absolute_spearman": minimum_magnitude,
                "minimum_valid_pair_coverage": minimum_coverage,
                "minimum_normalized_transition_separation": minimum_separation,
                "scenarios": {
                    scenario: {
                        **scenario_data[scenario],
                        "cross_sectional": dict(cross_metrics[scenario][feature]),
                    }
                    for scenario in EXPANDED_SPATIAL_SCENARIOS
                },
            }
        )
    priority = {
        "PROMISING": 0,
        "WEAK": 1,
        "UNSTABLE": 2,
        "INSUFFICIENT_DATA": 3,
    }
    ranked.sort(
        key=lambda item: (
            priority[item["classification"]],
            -float(item["minimum_absolute_spearman"] or 0.0),
            -float(item["minimum_valid_pair_coverage"]),
            str(item["feature"]),
        )
    )
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position

    cross_candidates: list[tuple[float, float, str]] = []
    for feature in SPECTRAL_FEATURE_NAMES:
        aucs = [
            cross_metrics[scenario][feature]["directional_auc"]
            for scenario in EXPANDED_SPATIAL_SCENARIOS
        ]
        if all(value is not None for value in aucs):
            cross_candidates.append(
                (min(float(value) for value in aucs), float(np.mean(aucs)), feature)
            )
    best_cross = max(cross_candidates, default=None)
    promising = [
        item["feature"] for item in ranked if item["classification"] == "PROMISING"
    ]
    go_to_model = (
        len(promising) >= 2
        and best_cross is not None
        and best_cross[0] >= 0.65
        and unique_sentinel_acquisitions >= 5
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "descriptive_spectral_feature_ranking",
        "methodology_revision": METHODOLOGY_REVISION,
        "documented_date_confirmation": False,
        "working_temporal_hypothesis": "H_FILENAME",
        "automatic_model_feature_selection": False,
        "ranking_rule": {
            "INSUFFICIENT_DATA": "fewer than 20 valid pairs in either scenario or non-calculable Spearman",
            "UNSTABLE": "Spearman signs differ between D50 and D100",
            "PROMISING": (
                "stable sign, minimum |Spearman| >= 0.20, minimum valid-pair coverage "
                ">= 0.50, and minimum normalized transition separation >= 0.50"
            ),
            "WEAK": "sufficient and stable, but one or more PROMISING criteria are unmet",
            "ordering": "classification, |Spearman|, coverage, feature name",
        },
        "ranking": ranked,
        "best_cross_sectional_feature": (
            {
                "feature": best_cross[2],
                "minimum_directional_auc_D50_D100": best_cross[0],
                "mean_directional_auc_D50_D100": best_cross[1],
                "D50": dict(cross_metrics["D50"][best_cross[2]]),
                "D100": dict(cross_metrics["D100"][best_cross[2]]),
            }
            if best_cross is not None
            else None
        ),
        "model_experiment_decision": (
            "GO_TO_GROUP_AWARE_MODEL_EXPERIMENT"
            if go_to_model
            else "SPECTRAL_SIGNAL_STILL_INSUFFICIENT"
        ),
        "model_experiment_rule": (
            "GO requires at least two PROMISING features, best cross-sectional "
            "directional AUC >= 0.65 in both D50/D100, and at least five unique "
            "Sentinel acquisitions for future acquisition-aware grouping."
        ),
        "unique_sentinel_acquisitions": unique_sentinel_acquisitions,
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


def _write_spectral_temporal_plots(
    delta_rows: Sequence[Mapping[str, Any]],
    ranking: Mapping[str, Any],
    output_dir: Path,
) -> tuple[list[Path], list[str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths: list[Path] = []
    skipped: list[str] = []
    transition_order = ("DECREASE", "STABLE", "INCREASE")
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        scenario_rows = [
            row
            for row in delta_rows
            if row.get("distance_scenario") == scenario
            and not _truthy(row.get("same_acquisition_before_after"))
        ]
        for feature in ("ndvi", "evi", "savi", "ndre"):
            groups = [
                [
                    float(row[f"delta_{feature}"])
                    for row in scenario_rows
                    if row.get("class_transition") == transition
                    and row.get(f"delta_{feature}") is not None
                ]
                for transition in transition_order
            ]
            plot_name = f"delta_{feature}_by_transition_{scenario}"
            if sum(bool(group) for group in groups) < 2:
                skipped.append(f"{plot_name}: fewer than two transition groups")
                continue
            figure, axis = plt.subplots(figsize=(8, 5))
            axis.boxplot(groups, tick_labels=transition_order)
            axis.axhline(0.0, color="gray", linewidth=0.8)
            axis.set_ylabel(f"Delta {feature.upper()} (after - before)")
            axis.set_xlabel("Field class transition")
            axis.set_title(f"{feature.upper()} temporal change — {scenario}")
            figure.tight_layout()
            path = output_dir / f"{plot_name}.png"
            figure.savefig(path, dpi=150)
            plt.close(figure)
            paths.append(path)

    cross_ranked = sorted(
        ranking.get("ranking", []),
        key=lambda item: (
            -min(
                float(
                    item["scenarios"][scenario]["cross_sectional"][
                        "directional_auc"
                    ]
                    or 0.0
                )
                for scenario in EXPANDED_SPATIAL_SCENARIOS
            ),
            str(item["feature"]),
        ),
    )
    top_features = [str(item["feature"]) for item in cross_ranked[:3]]
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        scenario_rows = [
            row for row in delta_rows if row.get("distance_scenario") == scenario
        ]
        for feature in top_features:
            low: list[float] = []
            high: list[float] = []
            for row in scenario_rows:
                for role in ("before", "after"):
                    value = row.get(f"{feature}_{role}")
                    if value is None:
                        continue
                    target = low if int(row[f"class_{role}"]) <= 2 else high
                    target.append(float(value))
            plot_name = f"cross_sectional_{feature}_{scenario}"
            if not low or not high:
                skipped.append(f"{plot_name}: insufficient binary-group data")
                continue
            figure, axis = plt.subplots(figsize=(8, 5))
            axis.boxplot([low, high], tick_labels=["<=30 cm", ">30 cm"])
            axis.set_ylabel(feature.upper())
            axis.set_xlabel("Exploratory binary field-height group")
            axis.set_title(f"H_FILENAME {feature.upper()} — {scenario}")
            figure.tight_layout()
            path = output_dir / f"{plot_name}.png"
            figure.savefig(path, dpi=150)
            plt.close(figure)
            paths.append(path)
    return paths, skipped


def build_spectral_dependency_diagnostics(
    preflight: Mapping[str, Any],
    feature_rows: Sequence[Mapping[str, Any]],
    delta_rows: Sequence[Mapping[str, Any]],
    *,
    unique_polygons_processed: int,
    unique_sentinel_acquisitions: int,
    elapsed_seconds: float,
    errors: Sequence[Mapping[str, str]],
    plots: Sequence[Path],
    skipped_plots: Sequence[str],
) -> dict[str, Any]:
    valid_rows = [
        row
        for row in feature_rows
        if row.get("spectral_processing_status") == "VALID_SPECTRAL_FEATURES"
    ]
    geometry_pairs: dict[str, set[str]] = {}
    scene_pairs: dict[str, set[str]] = {}
    km_pairs: dict[str, set[str]] = {}
    for row in valid_rows:
        pair_id = str(row["pair_id"])
        geometry_pairs.setdefault(str(row["candidate_geometry_id"]), set()).add(pair_id)
        scene_pairs.setdefault(str(row["sentinel_item_id"]), set()).add(pair_id)
        km_pairs.setdefault(str(row["group_km_id"]), set()).add(pair_id)
    scenario_diagnostics = {}
    for scenario in EXPANDED_SPATIAL_SCENARIOS:
        scenario_feature_rows = [
            row
            for row in valid_rows
            if row.get("distance_scenario") == scenario
        ]
        rows = [
            row
            for row in delta_rows
            if row.get("distance_scenario") == scenario
            and int(row.get("candidate_with_valid_pair_count") or 0) > 0
        ]
        valid_geometry_ids = {
            geometry_id
            for row in rows
            for geometry_id in str(row.get("group_geometry_id") or "").split(";")
            if geometry_id
        }
        same_acquisition = sum(
            _truthy(row.get("same_acquisition_before_after")) for row in rows
        )
        different_acquisition = len(rows) - same_acquisition
        scenario_diagnostics[scenario] = {
            "pairs_valid": len(rows),
            "pairs_same_acquisition": same_acquisition,
            "pairs_different_acquisition": different_acquisition,
            "percentage_different_acquisition": (
                different_acquisition / len(rows) * 100.0 if rows else 0.0
            ),
            "effective_geometry_count": len(valid_geometry_ids),
            "sentinel_acquisition_count": len(
                {
                    item_id
                    for row in rows
                    for field in ("sentinel_item_before", "sentinel_item_after")
                    for item_id in str(row.get(field) or "").split(";")
                    if item_id
                }
            ),
            "sentinel_item_count_any_valid_snapshot": len(
                {
                    str(row["sentinel_item_id"])
                    for row in scenario_feature_rows
                    if row.get("sentinel_item_id")
                }
            ),
            "sentinel_scene_date_count_any_valid_snapshot": len(
                {
                    str(row["sentinel_scene_date"])
                    for row in scenario_feature_rows
                    if row.get("sentinel_scene_date")
                }
            ),
        }
    unique_valid_observations = list(
        {
            (
                str(row["candidate_geometry_id"]),
                str(row["sentinel_item_id"]),
            ): row
            for row in valid_rows
        }.values()
    )
    spectral_values = {
        feature: _finite_values(
            row.get(feature) for row in unique_valid_observations
        )
        for feature in SPECTRAL_FEATURE_NAMES
    }

    def sanity_distribution(values: np.ndarray) -> dict[str, Any]:
        return {
            "n": int(values.size),
            "min": float(np.min(values)) if values.size else None,
            "q1": float(np.quantile(values, 0.25)) if values.size else None,
            "median": float(np.median(values)) if values.size else None,
            "q3": float(np.quantile(values, 0.75)) if values.size else None,
            "max": float(np.max(values)) if values.size else None,
            "absolute_value_gt_2_count": int(np.count_nonzero(np.abs(values) > 2.0)),
        }

    negative_lag_count = sum(
        float(row.get("temporal_lag_days") or 0) < 0 for row in valid_rows
    )
    future_scene_count = sum(
        str(row.get("sentinel_scene_date") or "9999-99-99")
        > str(row.get("target_date") or "")
        for row in valid_rows
    )
    nonfinite_feature_count = sum(
        not math.isfinite(float(row[feature]))
        for row in valid_rows
        for feature in SPECTRAL_FEATURE_NAMES
        if row.get(feature) is not None
    )
    same_acquisition_nonzero_delta_count = sum(
        _truthy(row.get("same_acquisition_before_after"))
        and any(
            row.get(f"delta_{feature}") is not None
            and abs(float(row[f"delta_{feature}"])) > 1e-12
            for feature in SPECTRAL_FEATURE_NAMES
        )
        for row in delta_rows
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "spectral_temporal_dependency_diagnostics",
        "methodology_revision": METHODOLOGY_REVISION,
        "documented_date_confirmation": False,
        "working_temporal_hypothesis": "H_FILENAME",
        "ground_truth_polygon_assignment": False,
        "primary_statistical_unit": "PAIR_LOCAL",
        "preflight": dict(preflight),
        "processing": {
            "unique_polygons_processed": unique_polygons_processed,
            "unique_sentinel_acquisitions": unique_sentinel_acquisitions,
            "elapsed_seconds": elapsed_seconds,
            "execution_errors": list(errors),
        },
        "sharing": {
            "geometry_groups": len(geometry_pairs),
            "geometry_groups_shared_by_multiple_pairs": sum(
                len(pair_ids) > 1 for pair_ids in geometry_pairs.values()
            ),
            "maximum_pairs_sharing_geometry": max(
                (len(pair_ids) for pair_ids in geometry_pairs.values()), default=0
            ),
            "sentinel_acquisition_groups": len(scene_pairs),
            "maximum_pairs_sharing_sentinel_acquisition": max(
                (len(pair_ids) for pair_ids in scene_pairs.values()), default=0
            ),
            "km_groups": len(km_pairs),
            "maximum_pairs_sharing_km": max(
                (len(pair_ids) for pair_ids in km_pairs.values()), default=0
            ),
        },
        "scenarios": scenario_diagnostics,
        "sanity_checks": {
            "negative_temporal_lag_count": negative_lag_count,
            "future_scene_selection_count": future_scene_count,
            "nonfinite_feature_count": nonfinite_feature_count,
            "same_acquisition_nonzero_delta_count": (
                same_acquisition_nonzero_delta_count
            ),
            "ratio_scale_invariance": (
                "NDVI and NDRE invariance under positive multiplicative scaling is "
                "covered by automated tests; additive offsets are applied before all indices."
            ),
            "EVI_distribution": sanity_distribution(spectral_values["evi"]),
            "SAVI_distribution": sanity_distribution(spectral_values["savi"]),
            "EVI_SAVI_distribution_unit": (
                "unique candidate_geometry_id + sentinel_item_id"
            ),
        },
        "future_group_validation_fields": [
            "pair_id",
            "group_geometry_id",
            "group_km_id",
            "group_sentinel_acquisition_id",
        ],
        "spectral_method": {
            "temporal_selection": (
                "causal scene_date <= target_date; inclusive 30-date lookback; "
                "accepted_for_timeseries first, then smallest lag, quality score, "
                "valid-pixel percentage, cloud cover, and item_id"
            ),
            "causal_windows": {
                "2026-03-13": ["2026-02-12", "2026-03-13"],
                "2026-03-20": ["2026-02-19", "2026-03-20"],
            },
            "same_acquisition_policy": (
                "preserved with zero delta but excluded from temporal summaries, "
                "Spearman ranking evidence, and temporal plots"
            ),
            "effective_grid": "B04 reference grid",
            "effective_resolution_m": SPECTRAL_EFFECTIVE_RESOLUTION_M,
            "reflectance_resampling": "bilinear",
            "SCL_resampling": "nearest",
            "common_valid_mask": "all ten reflectance bands plus SCL when available",
            "feature_statistic": "median reflectance/index over common valid AOI pixels",
            "pair_statistic": (
                "median across shared candidate geometries at each snapshot; delta is "
                "after median minus before median"
            ),
            "formulas": {
                "NDVI": "(B08 - B04) / (B08 + B04)",
                "EVI": "2.5 * (B08 - B04) / (B08 + 6*B04 - 7.5*B02 + 1)",
                "SAVI": "1.5 * (B08 - B04) / (B08 + B04 + 0.5)",
                "NDRE": "(B8A - B05) / (B8A + B05)",
            },
            "reflectance_scaling": {
                "raw_band_values_preserved": True,
                "sources_observed": sorted(
                    {
                        str(row.get("reflectance_scale_source"))
                        for row in valid_rows
                        if row.get("reflectance_scale_source")
                    }
                ),
                "scales_observed": sorted(
                    {
                        float(row["reflectance_scale"])
                        for row in valid_rows
                        if row.get("reflectance_scale") is not None
                    }
                ),
                "offsets_observed": sorted(
                    {
                        float(row["reflectance_offset"])
                        for row in valid_rows
                        if row.get("reflectance_offset") is not None
                    }
                ),
            },
        },
        "plots": {
            "generated": [str(path) for path in plots],
            "skipped": list(skipped_plots),
        },
        "independence_warning": (
            "Rows sharing geometry, KM or Sentinel acquisition are dependent and must "
            "not be treated as independent train/test observations."
        ),
    }


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


def run_spectral_temporal_calibration(
    dataset: str | Path,
    management_kmz: str | Path,
    km_markers_kmz: str | Path,
    sample_size: int,
    output_dir: str | Path,
) -> dict[str, Any]:
    del sample_size  # A unidade e todo par explicito com candidato compartilhado.
    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    preflight, pairs, candidate_sets = build_spectral_temporal_preflight(
        load_calibration_rows(dataset),
        load_management_features(management_kmz),
        load_km_markers(km_markers_kmz),
    )
    feature_by_id: dict[str, ManagementFeature] = {}
    for pair in pairs:
        for candidate in pair_candidate_intersection(pair, candidate_sets, "D100"):
            feature_by_id[candidate.feature.feature_id] = candidate.feature
    observations, errors, unique_acquisitions = build_spectral_observation_cache(
        list(feature_by_id.values())
    )
    feature_rows, paired_rows, delta_rows = build_spectral_pair_outputs(
        pairs, candidate_sets, observations
    )
    cross_summaries, cross_metrics = summarize_spectral_cross_sectional(delta_rows)
    delta_summaries, delta_metrics = summarize_spectral_deltas(delta_rows)
    ranking = build_spectral_feature_ranking(
        delta_metrics,
        cross_metrics,
        unique_sentinel_acquisitions=unique_acquisitions,
    )
    plots, skipped_plots = _write_spectral_temporal_plots(delta_rows, ranking, output)

    paired_path = output / "paired_samples.csv"
    features_path = output / "spectral_features_by_sample.csv"
    delta_path = output / "spectral_delta_by_pair.csv"
    cross_path = output / "feature_cross_sectional_summary.csv"
    delta_summary_path = output / "feature_delta_summary.csv"
    ranking_path = output / "feature_ranking.json"
    dependency_path = output / "dependency_diagnostics.json"
    _write_csv(paired_path, paired_rows, PAIRED_SAMPLE_FIELDS)
    _write_csv(features_path, feature_rows, SPECTRAL_SAMPLE_FIELDS)
    _write_csv(delta_path, delta_rows, SPECTRAL_DELTA_FIELDS)
    _write_csv(
        cross_path, cross_summaries, FEATURE_CROSS_SECTIONAL_FIELDS
    )
    _write_csv(delta_summary_path, delta_summaries, FEATURE_DELTA_SUMMARY_FIELDS)
    elapsed_seconds = time.perf_counter() - started
    ranking["performance"] = {
        "unique_polygons_processed": len(feature_by_id),
        "unique_sentinel_acquisitions": unique_acquisitions,
        "elapsed_seconds": elapsed_seconds,
    }
    ranking_path.write_text(
        json.dumps(ranking, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    diagnostics = build_spectral_dependency_diagnostics(
        preflight,
        feature_rows,
        delta_rows,
        unique_polygons_processed=len(feature_by_id),
        unique_sentinel_acquisitions=unique_acquisitions,
        elapsed_seconds=elapsed_seconds,
        errors=errors,
        plots=plots,
        skipped_plots=skipped_plots,
    )
    dependency_path.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    valid_pairs = {
        scenario: sum(
            row.get("distance_scenario") == scenario
            and int(row.get("candidate_with_valid_pair_count") or 0) > 0
            for row in delta_rows
        )
        for scenario in EXPANDED_SPATIAL_SCENARIOS
    }
    return {
        "paired_samples_csv": str(paired_path),
        "spectral_features_by_sample_csv": str(features_path),
        "spectral_delta_by_pair_csv": str(delta_path),
        "feature_cross_sectional_summary_csv": str(cross_path),
        "feature_delta_summary_csv": str(delta_summary_path),
        "feature_ranking_json": str(ranking_path),
        "dependency_diagnostics_json": str(dependency_path),
        "preflight_pair_count": preflight["pair_count"],
        "preflight_scenarios": preflight["scenarios"],
        "valid_spectral_pairs": valid_pairs,
        "unique_polygons_processed": len(feature_by_id),
        "unique_sentinel_acquisitions": unique_acquisitions,
        "top_ranked_feature": (
            ranking["ranking"][0]["feature"] if ranking["ranking"] else None
        ),
        "best_cross_sectional_feature": ranking["best_cross_sectional_feature"],
        "model_experiment_decision": ranking["model_experiment_decision"],
        "execution_errors": len(errors),
        "plots_generated": len(plots),
        "plots_skipped": skipped_plots,
        "elapsed_seconds": elapsed_seconds,
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
    modes.add_argument(
        "--spectral-temporal-preflight-only",
        action="store_true",
        help="Pair snapshots and check shared D50/D100 candidates without Sentinel-2.",
    )
    modes.add_argument(
        "--spectral-temporal-analysis",
        action="store_true",
        help="Run H_FILENAME multiband paired D50/D100 exploratory analysis.",
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
    elif arguments.spectral_temporal_preflight_only:
        runner = run_spectral_temporal_preflight
    elif arguments.spectral_temporal_analysis:
        runner = run_spectral_temporal_calibration
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
