"""Segmentacao longitudinal experimental alinhada a rodovias Motiva."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from statistics import median
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from pyproj import CRS, Geod, Transformer
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.ops import linemerge, split, substring, transform, unary_union


CLASSES = ("cortar", "nao_cortar", "inconclusivo")
DECISIVE_CLASSES = ("cortar", "nao_cortar")
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class RoadAlignedConfig:
    section_lengths_m: tuple[int, ...] = (25, 50, 100)
    dominance_thresholds: tuple[float, ...] = (0.60, 0.70, 0.75)
    max_road_distance_m: float = 250.0
    ambiguity_tolerance_m: float = 25.0
    min_louveira_zone_reduction_pct: float = 50.0
    max_class_reallocation_pct: float = 15.0
    max_unassigned_area_pct: float = 10.0
    max_frango_inconclusive_pct: float = 25.0

    def __post_init__(self) -> None:
        if not self.section_lengths_m or any(value <= 0 for value in self.section_lengths_m):
            raise ValueError("Section lengths must be positive.")
        if not self.dominance_thresholds or any(
            not 0.5 < value <= 1.0 for value in self.dominance_thresholds
        ):
            raise ValueError("Dominance thresholds must be in (0.5, 1.0].")
        if self.max_road_distance_m <= 0 or self.ambiguity_tolerance_m < 0:
            raise ValueError("Road association distances must be non-negative.")
        percentage_limits = (
            self.min_louveira_zone_reduction_pct,
            self.max_class_reallocation_pct,
            self.max_unassigned_area_pct,
            self.max_frango_inconclusive_pct,
        )
        if any(value < 0.0 or value > 100.0 for value in percentage_limits):
            raise ValueError("Safety percentages must be in [0, 100].")


@dataclass(frozen=True)
class RoadAssociation:
    status: str
    road_ref: str | None
    road_name: str | None
    distance_m: float | None
    metric_crs: str
    line_wgs84: Any | None
    line_metric: Any | None
    candidate_audit: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "road_ref": self.road_ref,
            "road_name": self.road_name,
            "distance_m": self.distance_m,
            "metric_crs": self.metric_crs,
            "candidate_audit": list(self.candidate_audit),
        }


def _canonical_hash(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def metric_crs_for_geometry(geometry_wgs84: Any) -> CRS:
    centroid = geometry_wgs84.centroid
    zone = max(1, min(60, int((centroid.x + 180.0) // 6.0) + 1))
    epsg = (32600 if centroid.y >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def _line_parts(geometry: Any) -> list[LineString]:
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type == "MultiLineString":
        return list(geometry.geoms)
    return []


def _group_road_features(road_document: Mapping[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for index, feature in enumerate(road_document.get("features") or []):
        geometry = shape(feature.get("geometry"))
        if geometry.is_empty or geometry.geom_type not in {"LineString", "MultiLineString"}:
            continue
        properties = feature.get("properties") or {}
        feature_id = str(properties.get("feature_id") or feature.get("id") or index)
        road_ref = str(properties.get("road_ref") or feature_id)
        road_name = str(properties.get("road_name") or road_ref)
        key = (road_ref, road_name)
        group = groups.setdefault(
            key,
            {
                "road_ref": road_ref,
                "road_name": road_name,
                "feature_ids": [],
                "geometries": [],
            },
        )
        group["feature_ids"].append(feature_id)
        group["geometries"].append(geometry)
    return [groups[key] for key in sorted(groups)]


def associate_road(
    raw_cells: Sequence[dict[str, Any]],
    road_document: Mapping[str, Any],
    *,
    max_distance_m: float = 250.0,
    ambiguity_tolerance_m: float = 25.0,
) -> RoadAssociation:
    """Associa a AOI efetiva a uma rodovia unica usando distancia metrica."""
    footprint_wgs84 = unary_union([shape(cell["geometry"]) for cell in raw_cells])
    metric_crs = metric_crs_for_geometry(footprint_wgs84)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    footprint_metric = transform(forward.transform, footprint_wgs84)
    candidates: list[dict[str, Any]] = []
    for group in _group_road_features(road_document):
        united = unary_union(group["geometries"])
        merged = united if united.geom_type == "LineString" else linemerge(united)
        parts = _line_parts(merged)
        if not parts:
            continue
        metric_parts = [transform(forward.transform, part) for part in parts]
        closest_index, closest_metric = min(
            enumerate(metric_parts), key=lambda item: item[1].distance(footprint_metric)
        )
        distance = float(closest_metric.distance(footprint_metric))
        candidates.append(
            {
                "road_ref": group["road_ref"],
                "road_name": group["road_name"],
                "feature_ids": sorted(group["feature_ids"]),
                "distance_m": distance,
                "line_wgs84": parts[closest_index],
                "line_metric": closest_metric,
            }
        )
    candidates.sort(
        key=lambda item: (item["distance_m"], item["road_ref"], item["road_name"])
    )
    audit = tuple(
        {
            "road_ref": item["road_ref"],
            "road_name": item["road_name"],
            "distance_m": item["distance_m"],
            "feature_ids": item["feature_ids"],
        }
        for item in candidates[:5]
    )
    if not candidates or candidates[0]["distance_m"] > max_distance_m:
        return RoadAssociation(
            "ROAD_ASSOCIATION_NOT_FOUND",
            None,
            None,
            candidates[0]["distance_m"] if candidates else None,
            metric_crs.to_string(),
            None,
            None,
            audit,
        )
    nearest = candidates[0]
    if (
        len(candidates) > 1
        and candidates[1]["distance_m"] - nearest["distance_m"]
        <= ambiguity_tolerance_m
    ):
        return RoadAssociation(
            "ROAD_ASSOCIATION_AMBIGUOUS",
            None,
            None,
            nearest["distance_m"],
            metric_crs.to_string(),
            None,
            None,
            audit,
        )
    return RoadAssociation(
        "ASSOCIATED",
        nearest["road_ref"],
        nearest["road_name"],
        nearest["distance_m"],
        metric_crs.to_string(),
        nearest["line_wgs84"],
        nearest["line_metric"],
        audit,
    )


def _geometry_points(geometry: Any) -> list[Point]:
    points: list[Point] = []
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for polygon in polygons:
        points.extend(Point(coordinate) for coordinate in polygon.exterior.coords)
        for interior in polygon.interiors:
            points.extend(Point(coordinate) for coordinate in interior.coords)
    return points


def _perpendicular_cutter(line: LineString, distance: float, span: float) -> LineString:
    epsilon = min(1.0, max(0.01, line.length / 10000.0))
    before = line.interpolate(max(0.0, distance - epsilon))
    after = line.interpolate(min(line.length, distance + epsilon))
    dx = after.x - before.x
    dy = after.y - before.y
    magnitude = math.hypot(dx, dy)
    if magnitude == 0:
        raise ValueError("Road tangent is undefined at section boundary.")
    normal_x = -dy / magnitude
    normal_y = dx / magnitude
    point = line.interpolate(distance)
    return LineString(
        [
            (point.x - normal_x * span, point.y - normal_y * span),
            (point.x + normal_x * span, point.y + normal_y * span),
        ]
    )


def _split_footprint_by_chainage(
    footprint: Any,
    line: LineString,
    boundaries: Sequence[float],
) -> list[Any]:
    parts = [footprint]
    bounds = footprint.bounds
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 4.0 + 1000.0
    for boundary in boundaries:
        cutter = _perpendicular_cutter(line, boundary, span)
        next_parts: list[Any] = []
        for part in parts:
            if part.is_empty or not part.intersects(cutter):
                next_parts.append(part)
                continue
            divided = split(part, cutter)
            next_parts.extend(piece for piece in divided.geoms if not piece.is_empty)
        parts = next_parts
    return parts


def build_longitudinal_section_geometries(
    aoi_wgs84: Any,
    association: RoadAssociation,
    *,
    section_length_m: int,
) -> list[dict[str, Any]]:
    """Divide uma AOI em faixas de chainage sem classificar sua evidencia."""
    if association.status != "ASSOCIATED" or association.line_metric is None:
        raise ValueError(association.status)
    if section_length_m <= 0:
        raise ValueError("Section length must be positive.")
    metric_crs = CRS.from_user_input(association.metric_crs)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    reverse = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    footprint = transform(forward.transform, aoi_wgs84)
    line = association.line_metric
    chainages = [line.project(point) for point in _geometry_points(footprint)]
    if not chainages:
        raise ValueError("AOI has no vertices for chainage calculation.")
    first_index = max(0, int(math.floor(min(chainages) / section_length_m)))
    last_index = min(
        int(math.ceil(line.length / section_length_m)),
        int(math.floor(max(chainages) / section_length_m)) + 1,
    )
    internal_boundaries = [
        index * section_length_m
        for index in range(first_index + 1, last_index)
        if 0 < index * section_length_m < line.length
    ]
    pieces = _split_footprint_by_chainage(footprint, line, internal_boundaries)
    pieces_by_index: dict[int, list[Any]] = {}
    for piece in pieces:
        chainage = float(line.project(piece.representative_point()))
        index = min(last_index - 1, max(first_index, int(chainage // section_length_m)))
        pieces_by_index.setdefault(index, []).append(piece)

    sections: list[dict[str, Any]] = []
    geod = Geod(ellps="WGS84")
    for sequence, index in enumerate(sorted(pieces_by_index), start=1):
        geometry_metric = unary_union(pieces_by_index[index]).intersection(footprint)
        if geometry_metric.is_empty or geometry_metric.area <= 0:
            continue
        start_distance = float(index * section_length_m)
        end_distance = float(min((index + 1) * section_length_m, line.length))
        geometry_wgs84 = transform(reverse.transform, geometry_metric)
        geodesic_area, _ = geod.geometry_area_perimeter(geometry_wgs84)
        sections.append(
            {
                "section_id": f"section_{sequence:04d}",
                "sequence_index": sequence,
                "road_ref": association.road_ref,
                "road_name": association.road_name,
                "start_distance_m": start_distance,
                "end_distance_m": end_distance,
                "length_m": end_distance - start_distance,
                "selected_area_m2": abs(float(geodesic_area)),
                "projected_area_m2": float(geometry_metric.area),
                "geometry_metric": geometry_metric,
                "geometry": mapping(geometry_wgs84),
                "centerline_geometry": mapping(
                    transform(
                        reverse.transform,
                        substring(line, start_distance, end_distance),
                    )
                ),
            }
        )
    return sections


def _minimum_level(values: Sequence[str]) -> str:
    return min(values, key=lambda value: LEVEL_ORDER.get(value, -1), default="low")


def _level_summary(values: Sequence[str]) -> dict[str, Any]:
    counts = Counter(values)
    return {
        "minimum": _minimum_level(values),
        "counts": {level: int(counts.get(level, 0)) for level in LEVEL_ORDER},
    }


def _cell_area(cell: Mapping[str, Any]) -> float:
    return float(
        cell.get("effective_area_m2", cell.get("intersection_area_m2", 0.0))
    )


def build_road_sections(
    raw_cells: Sequence[dict[str, Any]],
    association: RoadAssociation,
    *,
    section_length_m: int,
    dominance_threshold: float,
) -> list[dict[str, Any]]:
    """Particiona a area raw por chainage e agrega evidencia ponderada por area."""
    if association.status != "ASSOCIATED" or association.line_metric is None:
        raise ValueError(association.status)
    if section_length_m <= 0:
        raise ValueError("Section length must be positive.")
    metric_crs = CRS.from_user_input(association.metric_crs)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    raw_geometries_metric = {
        str(cell["cell_id"]): transform(forward.transform, shape(cell["geometry"]))
        for cell in raw_cells
    }
    footprint = unary_union(list(raw_geometries_metric.values()))
    footprint_wgs84 = transform(
        Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True).transform,
        footprint,
    )
    geometry_sections = build_longitudinal_section_geometries(
        footprint_wgs84,
        association,
        section_length_m=section_length_m,
    )
    sections: list[dict[str, Any]] = []
    for geometry_section in geometry_sections:
        section_geometry = geometry_section["geometry_metric"]
        start_distance = float(geometry_section["start_distance_m"])
        end_distance = float(geometry_section["end_distance_m"])
        evidence = {recommendation: 0.0 for recommendation in CLASSES}
        high_confidence = {recommendation: 0.0 for recommendation in DECISIVE_CLASSES}
        intersecting_cells: list[dict[str, Any]] = []
        raw_cell_ids: list[str] = []
        for cell in raw_cells:
            cell_id = str(cell["cell_id"])
            cell_geometry = raw_geometries_metric[cell_id]
            intersection = cell_geometry.intersection(section_geometry)
            if intersection.is_empty or intersection.area <= 1e-9:
                continue
            fraction = min(1.0, float(intersection.area / cell_geometry.area))
            weighted_area = _cell_area(cell) * fraction
            evidence[str(cell["recommendation"])] += weighted_area
            if (
                cell.get("confidence") == "high"
                and cell["recommendation"] in DECISIVE_CLASSES
            ):
                high_confidence[str(cell["recommendation"])] += weighted_area
            intersecting_cells.append(cell)
            raw_cell_ids.append(cell_id)
        effective_area = float(sum(evidence.values()))
        if effective_area <= 0:
            continue
        percentages = {
            recommendation: evidence[recommendation] / effective_area * 100.0
            for recommendation in CLASSES
        }
        conflict = all(high_confidence[value] > 1e-9 for value in DECISIVE_CLASSES)
        if conflict:
            recommendation = "inconclusivo"
            reason = "CONFLICTING_HIGH_CONFIDENCE_EVIDENCE"
        elif percentages["cortar"] >= dominance_threshold * 100.0:
            recommendation = "cortar"
            reason = "CORTAR_AREA_DOMINANT"
        elif percentages["nao_cortar"] >= dominance_threshold * 100.0:
            recommendation = "nao_cortar"
            reason = "NAO_CORTAR_AREA_DOMINANT"
        else:
            recommendation = "inconclusivo"
            reason = "NO_DECISIVE_CLASS_REACHED_DOMINANCE"
        supporting_confidences = [
            str(cell.get("confidence", "low"))
            for cell in intersecting_cells
            if cell["recommendation"] == recommendation
        ]
        confidence = (
            _minimum_level(supporting_confidences)
            if recommendation in DECISIVE_CLASSES and supporting_confidences
            else "low"
        )
        sections.append(
            {
                "section_id": geometry_section["section_id"],
                "sequence_index": geometry_section["sequence_index"],
                "road_ref": association.road_ref,
                "road_name": association.road_name,
                "start_distance_m": start_distance,
                "end_distance_m": end_distance,
                "length_m": end_distance - start_distance,
                "geometry": geometry_section["geometry"],
                "centerline_geometry": geometry_section["centerline_geometry"],
                "effective_area_m2": effective_area,
                "area_m2": effective_area,
                "raw_cell_count": len(set(raw_cell_ids)),
                "raw_cell_ids": sorted(set(raw_cell_ids)),
                "cortar_area_m2": evidence["cortar"],
                "nao_cortar_area_m2": evidence["nao_cortar"],
                "inconclusive_area_m2": evidence["inconclusivo"],
                "area_pct_by_raw_class": percentages,
                "high_confidence_cortar_area_m2": high_confidence["cortar"],
                "high_confidence_nao_cortar_area_m2": high_confidence["nao_cortar"],
                "high_confidence_conflict": conflict,
                "recommendation": recommendation,
                "classification_reason": reason,
                "confidence": confidence,
                "analysis_quality": _minimum_level(
                    [str(cell.get("analysis_quality", "low")) for cell in intersecting_cells]
                ),
            }
        )
    return sections


def merge_consecutive_sections(
    sections: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Une apenas chainages contiguos da mesma rodovia e recommendation."""
    ordered = sorted(
        sections,
        key=lambda item: (
            str(item["road_ref"]),
            float(item["start_distance_m"]),
            int(item["sequence_index"]),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    for section in ordered:
        if not groups:
            groups.append([section])
            continue
        previous = groups[-1][-1]
        consecutive = math.isclose(
            float(previous["end_distance_m"]),
            float(section["start_distance_m"]),
            abs_tol=1e-6,
        )
        if (
            consecutive
            and previous["road_ref"] == section["road_ref"]
            and previous["recommendation"] == section["recommendation"]
        ):
            groups[-1].append(section)
        else:
            groups.append([section])

    zones: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        raw_ids = sorted(
            {cell_id for section in group for cell_id in section["raw_cell_ids"]}
        )
        zones.append(
            {
                "zone_id": f"road_zone_{index:04d}",
                "road_ref": group[0]["road_ref"],
                "road_name": group[0]["road_name"],
                "start_distance_m": float(group[0]["start_distance_m"]),
                "end_distance_m": float(group[-1]["end_distance_m"]),
                "length_m": float(group[-1]["end_distance_m"])
                - float(group[0]["start_distance_m"]),
                "recommendation": group[0]["recommendation"],
                "area_m2": float(sum(float(section["area_m2"]) for section in group)),
                "raw_cell_count": len(raw_ids),
                "section_count": len(group),
                "confidence_summary": _level_summary(
                    [str(section["confidence"]) for section in group]
                ),
                "analysis_quality": _minimum_level(
                    [str(section["analysis_quality"]) for section in group]
                ),
                "geometry": mapping(
                    unary_union([shape(section["geometry"]) for section in group])
                ),
            }
        )
    return zones


def _area_by_class(items: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {
        recommendation: float(
            sum(
                float(item["area_m2"])
                for item in items
                if item["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }


def evaluate_road_aligned_configuration(
    raw_segmentation: dict[str, Any],
    association: RoadAssociation,
    *,
    section_length_m: int,
    dominance_threshold: float,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    started = clock()
    raw_hash_before = _canonical_hash(raw_segmentation)
    raw_cells = deepcopy(list(raw_segmentation.get("cells") or []))
    sections = build_road_sections(
        raw_cells,
        association,
        section_length_m=section_length_m,
        dominance_threshold=dominance_threshold,
    )
    zones = merge_consecutive_sections(sections)
    raw_areas = {
        recommendation: float(
            sum(
                _cell_area(cell)
                for cell in raw_cells
                if cell["recommendation"] == recommendation
            )
        )
        for recommendation in CLASSES
    }
    operational_areas = _area_by_class(zones)
    segmentable_area = float(sum(operational_areas.values()))
    selected_area = float(raw_segmentation.get("selected_area_m2") or sum(raw_areas.values()))
    unassigned_area = max(0.0, selected_area - segmentable_area)
    class_deltas = {
        recommendation: operational_areas[recommendation] - raw_areas[recommendation]
        for recommendation in CLASSES
    }
    class_reallocation_pct = (
        sum(abs(value) for value in class_deltas.values())
        / (2.0 * sum(raw_areas.values()))
        * 100.0
        if sum(raw_areas.values())
        else 0.0
    )
    raw_footprint = unary_union([shape(cell["geometry"]) for cell in raw_cells])
    zone_footprint = unary_union([shape(zone["geometry"]) for zone in zones])
    outside = zone_footprint.difference(raw_footprint)
    metric_crs = metric_crs_for_geometry(raw_footprint)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    outside_area = (
        float(transform(forward.transform, outside).area) if not outside.is_empty else 0.0
    )
    overlap_area = 0.0
    for index, section in enumerate(sections):
        first = shape(section["geometry"])
        for other in sections[index + 1 :]:
            intersection = first.intersection(shape(other["geometry"]))
            if not intersection.is_empty:
                overlap_area += float(transform(forward.transform, intersection).area)
    zone_lengths = [float(zone["length_m"]) for zone in zones]
    confidence_increase_count = sum(
        section["confidence"] == "high"
        and section["recommendation"] in DECISIVE_CLASSES
        and any(
            cell["confidence"] != "high"
            for cell in raw_cells
            if cell["cell_id"] in section["raw_cell_ids"]
            and cell["recommendation"] == section["recommendation"]
        )
        for section in sections
    )
    conflict_decisive_count = sum(
        section["high_confidence_conflict"]
        and section["recommendation"] != "inconclusivo"
        for section in sections
    )
    raw_hash_after = _canonical_hash(raw_segmentation)
    elapsed = max(0.0, clock() - started)
    return {
        "section_length_m": section_length_m,
        "dominance_threshold": dominance_threshold,
        "road_association": association.to_dict(),
        "selected_area_m2": selected_area,
        "road_segmentable_area_m2": segmentable_area,
        "unassigned_area_m2": unassigned_area,
        "unassigned_area_pct": unassigned_area / selected_area * 100.0 if selected_area else 0.0,
        "section_count": len(sections),
        "merged_zone_count": len(zones),
        "area_by_class_m2": operational_areas,
        "area_pct_by_class": {
            recommendation: (
                operational_areas[recommendation] / segmentable_area * 100.0
                if segmentable_area
                else 0.0
            )
            for recommendation in CLASSES
        },
        "raw_area_by_class_m2": raw_areas,
        "class_area_deltas_m2": class_deltas,
        "area_delta_vs_raw_pct": class_reallocation_pct,
        "median_zone_length_m": float(median(zone_lengths)) if zone_lengths else 0.0,
        "largest_zone_length_m": max(zone_lengths, default=0.0),
        "processing_time_seconds": round(elapsed, 6),
        "geometry_outside_aoi_m2": outside_area,
        "material_section_overlap_m2": overlap_area,
        "confidence_increase_count": int(confidence_increase_count),
        "conflicting_high_confidence_decisive_count": int(conflict_decisive_count),
        "raw_hash_before": raw_hash_before,
        "raw_hash_after": raw_hash_after,
        "raw_unchanged": raw_hash_before == raw_hash_after,
        "sections": sections,
        "zones": zones,
    }


def evaluate_road_aligned_matrix(
    cases: Mapping[str, dict[str, Any]],
    road_document: Mapping[str, Any],
    *,
    configuration: RoadAlignedConfig | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    config = configuration or RoadAlignedConfig()
    associations = {
        case_name: associate_road(
            list(raw.get("cells") or []),
            road_document,
            max_distance_m=config.max_road_distance_m,
            ambiguity_tolerance_m=config.ambiguity_tolerance_m,
        )
        for case_name, raw in cases.items()
    }
    results: dict[tuple[str, int, float], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for case_name in sorted(cases):
        association = associations[case_name]
        if association.status != "ASSOCIATED":
            continue
        for length in sorted(set(config.section_lengths_m)):
            for dominance in sorted(set(config.dominance_thresholds)):
                result = evaluate_road_aligned_configuration(
                    cases[case_name],
                    association,
                    section_length_m=length,
                    dominance_threshold=dominance,
                    clock=clock,
                )
                results[(case_name, length, dominance)] = result
                rows.append(
                    {key: value for key, value in result.items() if key not in {"sections", "zones"}}
                    | {"case": case_name}
                )

    evaluations: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for length in sorted(set(config.section_lengths_m)):
        for dominance in sorted(set(config.dominance_thresholds)):
            by_case = {
                case_name: results.get((case_name, length, dominance))
                for case_name in sorted(cases)
            }
            reasons: list[str] = []
            if any(value is None for value in by_case.values()):
                reasons.append("ROAD_ASSOCIATION_UNAVAILABLE")
            else:
                complete = [value for value in by_case.values() if value is not None]
                louveira = by_case.get("louveira")
                frango = by_case.get("frango_assado")
                raw_louveira_zones = len(cases["louveira"].get("zones") or [])
                louveira_reduction = (
                    (raw_louveira_zones - louveira["merged_zone_count"])
                    / raw_louveira_zones
                    * 100.0
                    if louveira and raw_louveira_zones
                    else 0.0
                )
                if louveira_reduction < config.min_louveira_zone_reduction_pct:
                    reasons.append("LOUVEIRA_REDUCTION_NOT_MATERIAL")
                if any(
                    value["area_delta_vs_raw_pct"] > config.max_class_reallocation_pct
                    for value in complete
                ):
                    reasons.append("CLASS_AREA_DISTORTION_EXCEEDED")
                if any(
                    value["unassigned_area_pct"] > config.max_unassigned_area_pct
                    for value in complete
                ):
                    reasons.append("UNASSIGNED_AREA_EXCEEDED")
                if (
                    frango
                    and frango["area_pct_by_class"]["inconclusivo"]
                    > config.max_frango_inconclusive_pct
                ):
                    reasons.append("FRANGO_BECAME_TOO_INCONCLUSIVE")
                if any(value["geometry_outside_aoi_m2"] > 1e-6 for value in complete):
                    reasons.append("GEOMETRY_OUTSIDE_AOI")
                if any(value["material_section_overlap_m2"] > 1e-6 for value in complete):
                    reasons.append("SECTIONS_OVERLAP_MATERIALLY")
                if any(value["confidence_increase_count"] for value in complete):
                    reasons.append("CONFIDENCE_ARTIFICIALLY_INCREASED")
                if any(
                    value["conflicting_high_confidence_decisive_count"]
                    for value in complete
                ):
                    reasons.append("HIGH_CONFIDENCE_CONFLICT_NOT_PRESERVED")
                if any(not value["raw_unchanged"] for value in complete):
                    reasons.append("RAW_SEGMENTATION_CHANGED")
            row = {
                "section_length_m": length,
                "dominance_threshold": dominance,
                "eligible": not reasons,
                "rejection_reasons": reasons,
                "combined_area_delta_vs_raw_pct": sum(
                    value["area_delta_vs_raw_pct"]
                    for value in by_case.values()
                    if value is not None
                ),
            }
            evaluations.append(row)
            if row["eligible"]:
                eligible.append(row)

    recommended = min(
        eligible,
        key=lambda row: (
            row["combined_area_delta_vs_raw_pct"],
            row["section_length_m"],
            -row["dominance_threshold"],
        ),
        default=None,
    )
    recommended_length = recommended["section_length_m"] if recommended else None
    recommended_dominance = recommended["dominance_threshold"] if recommended else None
    outputs = (
        {
            case_name: results[(case_name, recommended_length, recommended_dominance)]
            for case_name in sorted(cases)
        }
        if recommended
        else {}
    )
    return {
        "status": "experimental",
        "mode": "shadow",
        "configuration": asdict(config),
        "road_associations": {
            case_name: association.to_dict()
            for case_name, association in associations.items()
        },
        "experiments": rows,
        "candidate_evaluation": evaluations,
        "recommended_section_length": recommended_length,
        "recommended_dominance": recommended_dominance,
        "recommended_outputs": outputs,
    }
