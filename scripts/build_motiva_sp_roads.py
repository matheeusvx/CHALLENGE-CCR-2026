"""Constrói a malha estadual paulista da Motiva a partir dos KMZ da ARTESP."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from xml.etree import ElementTree

from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "data/roads/motiva_sp_roads_manifest_v1.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/roads/processed/motiva-sp-roads-state.geojson"
DEFAULT_PUBLIC_OUTPUT = REPOSITORY_ROOT / "apps/web/public/data/motiva-sp-roads.geojson"
DEFAULT_REPORT = REPOSITORY_ROOT / "outputs/roads/motiva_sp_roads_build_report.json"
DEFAULT_RAW_DIR = REPOSITORY_ROOT / "data/roads/raw"

ARTESP_SOURCE = "ARTESP_MALHA_RODOVIARIA_KMZ"
EXPECTED_STATE_CONCESSIONS = (
    "autoban",
    "rodoanel_oeste",
    "spvias",
    "sorocabana",
    "renovias",
)
PROGRESS_NAMES = {
    "autoban": "AutoBAn",
    "rodoanel_oeste": "Rodoanel Oeste",
    "spvias": "SPVias",
    "sorocabana": "Sorocabana",
    "renovias": "Renovias",
}
FEDERAL_PENDING = {
    "riosp_sp": "FEDERAL_GEOMETRY_PENDING",
    "minas_sp_sp": "FEDERAL_GEOMETRY_PENDING",
}

# Envelope conservador para rejeitar somente geometrias claramente fora de SP.
SP_EXPECTED_BOUNDS = (-53.2, -25.5, -44.0, -19.7)
SP_EXPECTED_ENVELOPE = box(*SP_EXPECTED_BOUNDS)
ALLOWED_GEOMETRY_TYPES = {"LineString", "MultiLineString"}
ROAD_REF_PATTERN = re.compile(
    r"(?<![A-Z0-9])(SP[AI]?)[\s_-]*([0-9]{2,3})(?:\s*[/]\s*([0-9]{2,3}))?",
    flags=re.IGNORECASE,
)
SECONDARY_ARTESP_REF_PATTERN = re.compile(
    r"(?<![A-Z0-9])(SPA|SPI)[\s_-]*([0-9]{2,3})[\s_-]+([0-9]{2,3})(?![0-9])",
    flags=re.IGNORECASE,
)


class RoadsBuildError(RuntimeError):
    """Erro fatal ou estrutural do builder."""


def load_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RoadsBuildError(f"Manifest não encontrado: {source}") from exc
    except json.JSONDecodeError as exc:
        raise RoadsBuildError(f"Manifest JSON inválido: {source}: {exc.msg}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("concessions"), list):
        raise RoadsBuildError("Manifest deve conter uma lista 'concessions'.")
    ids = [str(item.get("concession_id") or "") for item in document["concessions"]]
    missing = sorted(set(EXPECTED_STATE_CONCESSIONS) - set(ids))
    if missing:
        raise RoadsBuildError(
            "Manifest não contém as concessões estaduais esperadas: " + ", ".join(missing)
        )
    return document


def discover_kml_member(archive: zipfile.ZipFile) -> str:
    candidates = [
        name
        for name in archive.namelist()
        if name.casefold().endswith(".kml") and not name.endswith("/")
    ]
    if not candidates:
        raise RoadsBuildError("KMZ não contém arquivo KML.")
    return min(
        candidates,
        key=lambda name: (
            Path(name).name.casefold() != "doc.kml",
            name.count("/"),
            len(name),
            name.casefold(),
        ),
    )


def validate_kmz(path: str | Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            member = discover_kml_member(archive)
            return bool(archive.read(member)) and archive.testzip() is None
    except (OSError, zipfile.BadZipFile, RoadsBuildError):
        return False


def _default_downloader(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Motiva-SP-Roads-Builder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        destination.write_bytes(response.read())


def ensure_kmz_download(
    url: str,
    destination: str | Path,
    *,
    force: bool = False,
    downloader: Callable[[str, Path], None] = _default_downloader,
) -> dict[str, Any]:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force and validate_kmz(target):
        status = "cache_hit"
    else:
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            if temporary.exists():
                temporary.unlink()
            downloader(url, temporary)
            if not validate_kmz(temporary):
                raise RoadsBuildError(f"Download não é um KMZ válido: {url}")
            temporary.replace(target)
            status = "downloaded_force" if force else "downloaded"
        finally:
            if temporary.exists():
                temporary.unlink()
    payload = target.read_bytes()
    return {
        "url": url,
        "path": str(target),
        "status": status,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def extract_kml_from_kmz(path: str | Path) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            member = discover_kml_member(archive)
            return member, archive.read(member)
    except zipfile.BadZipFile as exc:
        raise RoadsBuildError(f"KMZ inválido: {path}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child_text(element: ElementTree.Element, name: str) -> str | None:
    child = next((item for item in element if _local_name(item.tag) == name), None)
    if child is None:
        return None
    value = " ".join(text.strip() for text in child.itertext() if text.strip())
    return value or None


def _coordinates(text: str | None) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    for token in (text or "").replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            longitude, latitude = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and -180 <= longitude <= 180
            and -90 <= latitude <= 90
        ):
            values.append((longitude, latitude))
    return values


def _properties(placemark: ElementTree.Element) -> dict[str, str]:
    properties: dict[str, str] = {}
    for node in placemark.iter():
        local = _local_name(node.tag)
        if local == "SimpleData" and node.get("name"):
            properties[str(node.get("name"))] = (node.text or "").strip()
        elif local == "Data" and node.get("name"):
            value = next(
                (child for child in node if _local_name(child.tag) == "value"), None
            )
            properties[str(node.get("name"))] = (
                " ".join(value.itertext()).strip() if value is not None else ""
            )
    name = _direct_child_text(placemark, "name")
    description = _direct_child_text(placemark, "description")
    if name:
        properties["__placemark_name"] = name
    if description:
        properties["__description"] = description
    return properties


def _line_geometry(placemark: ElementTree.Element) -> BaseGeometry | None:
    lines: list[LineString] = []
    for node in placemark.iter():
        if _local_name(node.tag) != "LineString":
            continue
        coordinate_node = next(
            (child for child in node.iter() if _local_name(child.tag) == "coordinates"),
            None,
        )
        coordinates = _coordinates(
            coordinate_node.text if coordinate_node is not None else None
        )
        if len(set(coordinates)) >= 2:
            lines.append(LineString(coordinates))
    if not lines:
        return None
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


def parse_kml_placemarks(kml: bytes | str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(kml)
    except ElementTree.ParseError as exc:
        raise RoadsBuildError(f"KML XML inválido: {exc}") from exc
    placemarks: list[tuple[ElementTree.Element, tuple[str, ...]]] = []

    def visit(node: ElementTree.Element, folder_path: tuple[str, ...]) -> None:
        local_name = _local_name(node.tag)
        current_path = folder_path
        if local_name in {"Document", "Folder"}:
            folder_name = _direct_child_text(node, "name")
            if folder_name:
                current_path = (*folder_path, folder_name)
        if local_name == "Placemark":
            placemarks.append((node, current_path))
            return
        for child in node:
            if _local_name(child.tag) in {"Document", "Folder", "Placemark"}:
                visit(child, current_path)

    visit(root, ())
    records: list[dict[str, Any]] = []
    for index, (placemark, folder_path) in enumerate(placemarks, start=1):
        geometry = _line_geometry(placemark)
        properties = _properties(placemark)
        if folder_path:
            properties["__folder_path"] = " / ".join(folder_path)
        records.append(
            {
                "source_index": index,
                "properties": properties,
                "geometry": geometry,
                "source_geometry_types": sorted(
                    {
                        _local_name(node.tag)
                        for node in placemark.iter()
                        if _local_name(node.tag)
                        in {"Point", "LineString", "Polygon", "MultiGeometry"}
                    }
                ),
            }
        )
    return records


def is_official_road_axis(properties: Mapping[str, Any]) -> bool:
    """Identifica eixos na hierarquia oficial, sem aceitar ramais e ativos auxiliares."""
    folder_path = _ascii(properties.get("__folder_path")).casefold()
    return (
        ("ativo linear" in folder_path or "ativos lineares" in folder_path)
        and "tracado" in folder_path
    )


def _ascii(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in normalized if not unicodedata.combining(character))


def normalize_road_ref(value: Any) -> str | None:
    text = _ascii(value).upper()
    match = SECONDARY_ARTESP_REF_PATTERN.search(text) or ROAD_REF_PATTERN.search(text)
    if match is None:
        return None
    prefix, primary, secondary = match.groups()
    reference = f"{prefix.upper()}-{int(primary):03d}"
    if secondary:
        reference += f"/{int(secondary):03d}"
    return reference


def canonical_road_ref(value: Any) -> str | None:
    """Retorna a referência viária-base, sem o sufixo administrativo do road_id."""
    return normalize_road_ref(value)


def _all_road_refs(properties: Mapping[str, Any]) -> set[str]:
    preferred_values = [
        value
        for key, value in properties.items()
        if any(
            token in _ascii(key).casefold()
            for token in ("rodovia", "codigo", "cod_rod", "sigla", "road", "ref")
        )
    ]
    all_values = [*preferred_values, *properties.values()]
    references: set[str] = set()
    for value in all_values:
        text = _ascii(value).upper()
        matches = [
            *SECONDARY_ARTESP_REF_PATTERN.finditer(text),
            *ROAD_REF_PATTERN.finditer(text),
        ]
        for match in matches:
            prefix, primary, secondary = match.groups()
            reference = f"{prefix.upper()}-{int(primary):03d}"
            if secondary:
                reference += f"/{int(secondary):03d}"
            references.add(reference)
    return references


def _name_signature(value: Any) -> set[str]:
    ignored = {
        "rodovia",
        "avenida",
        "do",
        "da",
        "de",
        "dos",
        "das",
        "e",
        "trecho",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _ascii(value).casefold())
        if len(token) >= 3 and token not in ignored
    }


def match_manifest_road(
    properties: Mapping[str, Any], roads: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, str, list[str]]:
    source_refs = _all_road_refs(properties)
    identifier_matches: list[Mapping[str, Any]] = []
    for road in roads:
        canonical = normalize_road_ref(road.get("ref") or road.get("road_id"))
        if canonical and canonical in source_refs:
            identifier_matches.append(road)
    specific_matches = [
        road
        for road in identifier_matches
        if "/" in str(normalize_road_ref(road.get("ref") or road.get("road_id")) or "")
    ]
    if len(specific_matches) == 1:
        return specific_matches[0], "exact_specific_road_ref", sorted(source_refs)
    if len(identifier_matches) == 1:
        return identifier_matches[0], "exact_normalized_road_ref", sorted(source_refs)
    if len(identifier_matches) > 1:
        source_range = source_km_range(properties)
        if source_range is not None:
            range_matches = [
                road
                for road in identifier_matches
                if _ranges_overlap(
                    source_range, road.get("km_start"), road.get("km_end")
                )
            ]
            if len(range_matches) == 1:
                return range_matches[0], "canonical_road_ref_and_km_range", sorted(source_refs)
        return None, "ambiguous_normalized_road_ref", sorted(source_refs)

    source_text = " ".join(str(value) for value in properties.values())
    source_tokens = _name_signature(source_text)
    name_matches = []
    for road in roads:
        tokens = _name_signature(road.get("name"))
        if len(tokens) >= 2 and tokens.issubset(source_tokens):
            name_matches.append(road)
    if len(name_matches) == 1:
        return name_matches[0], "unique_manifest_road_name", sorted(source_refs)

    alias_matches = []
    for road in roads:
        road_id = road.get("road_id")
        if normalize_road_ref(road_id) is not None:
            continue
        tokens = _name_signature(road_id)
        if len(tokens) >= 2 and tokens.issubset(source_tokens):
            alias_matches.append(road)
    if len(alias_matches) == 1:
        return alias_matches[0], "unique_manifest_road_id_alias", sorted(source_refs)
    return None, "no_confident_road_match", sorted(source_refs)


def _repair_line_geometry(geometry: BaseGeometry | None) -> BaseGeometry | None:
    if geometry is None or geometry.is_empty:
        return None
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    if candidate.geom_type in ALLOWED_GEOMETRY_TYPES and not candidate.is_empty:
        return candidate
    line_parts = [
        item
        for item in getattr(candidate, "geoms", ())
        if item.geom_type == "LineString" and not item.is_empty
    ]
    if not line_parts:
        return None
    return line_parts[0] if len(line_parts) == 1 else MultiLineString(line_parts)


def geometry_is_in_expected_sp_extent(geometry: BaseGeometry) -> bool:
    return geometry.intersects(SP_EXPECTED_ENVELOPE)


def _number_from_text(value: Any) -> float | None:
    match = re.search(r"-?[0-9]+(?:[.,][0-9]+)?", str(value or ""))
    if match is None:
        return None
    return float(match.group(0).replace(",", "."))


def source_km_range(properties: Mapping[str, Any]) -> tuple[float, float] | None:
    start = None
    end = None
    for key, value in properties.items():
        normalized = _ascii(key).casefold().replace(" ", "_")
        if "km" not in normalized:
            continue
        if any(token in normalized for token in ("inicial", "inicio", "start", "de_km")):
            start = _number_from_text(value)
        elif any(token in normalized for token in ("final", "fim", "end", "ate_km")):
            end = _number_from_text(value)
    if start is not None and end is not None:
        return (min(start, end), max(start, end))

    def km_markers(value: Any) -> list[float]:
        markers: list[float] = []
        pattern = re.compile(
            r"\bkm\s*[_:=\-]?\s*([0-9]{1,3})(?:\s*\+\s*([0-9]{1,3})|[.,]([0-9]+))?",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(_ascii(value)):
            integer, meters, decimal = match.groups()
            marker = float(integer)
            if meters:
                marker += float(meters) / (10 ** len(meters))
            elif decimal:
                marker += float(decimal) / (10 ** len(decimal))
            markers.append(marker)
        return markers

    contextual_markers: list[float] = []
    for key, value in properties.items():
        normalized = _ascii(key).casefold()
        if any(token in normalized for token in ("km", "trecho", "segmento", "description")):
            contextual_markers.extend(km_markers(value))
    if len(contextual_markers) >= 2:
        return (min(contextual_markers), max(contextual_markers))
    if len(contextual_markers) == 1:
        return (contextual_markers[0], contextual_markers[0])
    return None


def _ranges_overlap(
    source: tuple[float, float], expected_start: Any, expected_end: Any
) -> bool:
    try:
        expected = (float(expected_start), float(expected_end))
    except (TypeError, ValueError):
        return True
    return max(source[0], min(expected)) <= min(source[1], max(expected))


def _feature_properties(
    concession: Mapping[str, Any],
    road: Mapping[str, Any],
    geometry_config: Mapping[str, Any],
    *,
    strategy: str,
    match_method: str,
    source_name: str | None,
) -> dict[str, Any]:
    return {
        "road_id": str(road["road_id"]),
        "road_ref": normalize_road_ref(road.get("ref") or road.get("road_id")),
        "road_name": str(road.get("name") or road["road_id"]),
        "concession_id": str(concession["concession_id"]),
        "concession_name": str(concession.get("display_name") or ""),
        "motiva_participation_pct": concession.get("motiva_participation_pct"),
        "source": "ARTESP",
        "source_resource_id": geometry_config.get("resource_id"),
        "source_license": geometry_config.get("license"),
        "km_start": road.get("km_start"),
        "km_end": road.get("km_end"),
        "geometry_status": "valid",
        "geometry_strategy": strategy,
        "source_match_method": match_method,
        "source_feature_name": source_name,
    }


def _ambiguous_feature_properties(
    concession: Mapping[str, Any],
    candidate_roads: Sequence[Mapping[str, Any]],
    geometry_config: Mapping[str, Any],
    *,
    road_ref: str,
    source_name: str | None,
) -> dict[str, Any]:
    names = {str(road.get("name") or "").strip() for road in candidate_roads}
    names.discard("")
    return {
        "road_id": None,
        "road_ref": road_ref,
        "road_name": next(iter(names)) if len(names) == 1 else f"Rodovia {road_ref}",
        "candidate_road_ids": sorted(str(road["road_id"]) for road in candidate_roads),
        "candidate_km_ranges": [
            {
                "road_id": str(road["road_id"]),
                "km_start": road.get("km_start"),
                "km_end": road.get("km_end"),
            }
            for road in sorted(candidate_roads, key=lambda item: str(item["road_id"]))
        ],
        "concession_id": str(concession["concession_id"]),
        "concession_name": str(concession.get("display_name") or ""),
        "motiva_participation_pct": concession.get("motiva_participation_pct"),
        "source": "ARTESP",
        "source_resource_id": geometry_config.get("resource_id"),
        "source_license": geometry_config.get("license"),
        "km_start": None,
        "km_end": None,
        "geometry_status": "AMBIGUOUS_SUBSEGMENT_MATCH",
        "geometry_strategy": "official_concession_geometry",
        "source_match_method": "canonical_road_ref_without_subsegment_assignment",
        "source_feature_name": source_name,
    }


def ambiguous_subsegment_candidates(
    properties: Mapping[str, Any], roads: Sequence[Mapping[str, Any]]
) -> tuple[str | None, list[Mapping[str, Any]]]:
    source_refs = _all_road_refs(properties)
    candidates_by_ref: dict[str, list[Mapping[str, Any]]] = {}
    for road in roads:
        reference = canonical_road_ref(road.get("ref") or road.get("road_id"))
        if reference and reference in source_refs:
            candidates_by_ref.setdefault(reference, []).append(road)
    ambiguous = [
        (reference, candidates)
        for reference, candidates in candidates_by_ref.items()
        if len(candidates) > 1
    ]
    if len(ambiguous) != 1:
        return None, []
    return ambiguous[0]


def process_concession_kmz(
    concession: Mapping[str, Any], kmz_path: str | Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    member, kml = extract_kml_from_kmz(kmz_path)
    records = parse_kml_placemarks(kml)
    roads = list(concession.get("roads") or [])
    geometry_config = dict(concession.get("geometry") or {})
    features: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    rejected = Counter()
    attribute_keys = sorted(
        {str(key) for record in records for key in record["properties"]}
    )
    for record in records:
        geometry = _repair_line_geometry(record["geometry"])
        if geometry is None:
            rejected["INVALID_OR_UNSUPPORTED_GEOMETRY"] += 1
            continue
        if not is_official_road_axis(record["properties"]):
            rejected["NON_ROAD_AXIS_LINE"] += 1
            continue
        if not geometry_is_in_expected_sp_extent(geometry):
            rejected["OUTSIDE_SP_EXPECTED_EXTENT"] += 1
            warnings.append(
                f"OUTSIDE_SP_EXPECTED_EXTENT: placemark {record['source_index']}"
            )
            continue
        road, match_method, source_refs = match_manifest_road(record["properties"], roads)
        if road is None:
            base_ref, candidates = ambiguous_subsegment_candidates(
                record["properties"], roads
            )
            if match_method == "ambiguous_normalized_road_ref" and base_ref and candidates:
                properties = _ambiguous_feature_properties(
                    concession,
                    candidates,
                    geometry_config,
                    road_ref=base_ref,
                    source_name=record["properties"].get("__placemark_name"),
                )
                properties["feature_id"] = (
                    f"{concession['concession_id']}:{base_ref}:ambiguous:"
                    f"{record['source_index']:05d}"
                )
                features.append(
                    {
                        "type": "Feature",
                        "properties": properties,
                        "geometry": mapping(geometry),
                    }
                )
                warnings.append(
                    "AMBIGUOUS_SUBSEGMENT_MATCH: "
                    f"placemark {record['source_index']}; road_ref={base_ref}; "
                    f"candidate_road_ids={properties['candidate_road_ids']}"
                )
                continue
            rejected["UNMATCHED_ROAD_GEOMETRY"] += 1
            warnings.append(
                "UNMATCHED_ROAD_GEOMETRY: "
                f"placemark {record['source_index']}; refs={source_refs}; "
                f"name={record['properties'].get('__placemark_name')}"
            )
            continue
        source_range = source_km_range(record["properties"])
        strategy = "official_concession_geometry"
        if source_range is not None:
            if not _ranges_overlap(source_range, road.get("km_start"), road.get("km_end")):
                rejected["OUTSIDE_MANIFEST_KM_RANGE"] += 1
                continue
            strategy = "km_filtered_geometry"
        properties = _feature_properties(
            concession,
            road,
            geometry_config,
            strategy=strategy,
            match_method=match_method,
            source_name=record["properties"].get("__placemark_name"),
        )
        properties["feature_id"] = (
            f"{concession['concession_id']}:{road['road_id']}:{record['source_index']:05d}"
        )
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(geometry),
            }
        )
    features.sort(key=lambda feature: str(feature["properties"]["feature_id"]))
    return features, {
        "kml_member": member,
        "source_placemarks": len(records),
        "source_attribute_keys": attribute_keys,
        "source_geometry_types": sorted(
            {
                geometry_type
                for record in records
                for geometry_type in record["source_geometry_types"]
            }
        ),
        "features": len(features),
        "features_rejected": sum(rejected.values()),
        "rejection_counts": dict(sorted(rejected.items())),
        "road_ids": sorted(
            {
                str(feature["properties"]["road_id"])
                for feature in features
                if feature["properties"]["road_id"] is not None
            }
        ),
        "ambiguous_subsegment_road_ids": sorted(
            {
                str(road_id)
                for feature in features
                for road_id in feature["properties"].get("candidate_road_ids", [])
            }
        ),
        "source": "ARTESP",
        "warnings": list(dict.fromkeys(warnings)),
        "errors": errors,
    }


def approximate_length_km(features: Sequence[Mapping[str, Any]]) -> float:
    geod = Geod(ellps="WGS84")
    total_m = 0.0
    for feature in features:
        geometry_document = feature.get("geometry") or {}
        geometry_type = geometry_document.get("type")
        coordinates = geometry_document.get("coordinates") or []
        lines = [coordinates] if geometry_type == "LineString" else coordinates
        for line in lines:
            if len(line) >= 2:
                longitudes = [float(point[0]) for point in line]
                latitudes = [float(point[1]) for point in line]
                total_m += abs(float(geod.line_length(longitudes, latitudes)))
    return total_m / 1000.0


def validate_output_geojson(document: Mapping[str, Any]) -> dict[str, Any]:
    if document.get("type") != "FeatureCollection":
        raise RoadsBuildError("GeoJSON final não é FeatureCollection.")
    features = document.get("features")
    if not isinstance(features, list) or not features:
        raise RoadsBuildError("GeoJSON final está vazio.")
    required = {
        "road_id",
        "road_ref",
        "road_name",
        "concession_id",
        "concession_name",
        "motiva_participation_pct",
        "source",
        "source_resource_id",
        "source_license",
        "km_start",
        "km_end",
        "geometry_status",
    }
    geometry_types = Counter()
    road_ids: set[str] = set()
    for index, feature in enumerate(features, start=1):
        properties = feature.get("properties") or {}
        missing = sorted(required - set(properties))
        if missing:
            raise RoadsBuildError(
                f"Feature {index} sem propriedades obrigatórias: {', '.join(missing)}"
            )
        geometry_type = (feature.get("geometry") or {}).get("type")
        if geometry_type not in ALLOWED_GEOMETRY_TYPES:
            raise RoadsBuildError(f"Feature {index} possui geometria inválida: {geometry_type}")
        geometry_types[str(geometry_type)] += 1
        if properties["road_id"] is not None:
            road_ids.add(str(properties["road_id"]))
    return {
        "valid": True,
        "feature_count": len(features),
        "road_ids": sorted(road_ids),
        "geometry_types": dict(sorted(geometry_types.items())),
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_motiva_sp_roads(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    public_output_path: str | Path = DEFAULT_PUBLIC_OUTPUT,
    report_path: str | Path = DEFAULT_REPORT,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    force_download: bool = False,
    downloader: Callable[[str, Path], None] = _default_downloader,
    progress: Callable[[str], None] = print,
    generated_at: str | None = None,
) -> dict[str, Any]:
    manifest_source = Path(manifest_path)
    manifest = load_manifest(manifest_source)
    concessions_by_id = {
        str(item["concession_id"]): item for item in manifest["concessions"]
    }
    features: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    concession_reports: dict[str, Any] = {}
    build_warnings: list[str] = []
    build_errors: list[str] = []
    processed: list[str] = []
    failed: list[str] = []
    total_rejected = 0

    for index, concession_id in enumerate(EXPECTED_STATE_CONCESSIONS, start=1):
        progress(f"[{index}/5] Processando {PROGRESS_NAMES[concession_id]}...")
        concession = concessions_by_id[concession_id]
        geometry_config = dict(concession.get("geometry") or {})
        report = {
            "features": 0,
            "road_ids": [],
            "source": "ARTESP",
            "warnings": [],
            "errors": [],
        }
        try:
            if geometry_config.get("preferred_source") != ARTESP_SOURCE:
                raise RoadsBuildError("Fonte da concessão não é ARTESP KMZ.")
            url = str(geometry_config.get("download_url") or "").strip()
            if not url:
                raise RoadsBuildError("download_url ausente no manifest.")
            kmz_path = Path(raw_dir) / f"{concession_id}.kmz"
            download = ensure_kmz_download(
                url,
                kmz_path,
                force=force_download,
                downloader=downloader,
            )
            download["concession_id"] = concession_id
            downloads.append(download)
            concession_features, parsed_report = process_concession_kmz(
                concession, kmz_path
            )
            report.update(parsed_report)
            features.extend(concession_features)
            total_rejected += int(parsed_report["features_rejected"])
            build_warnings.extend(
                f"{concession_id}: {warning}" for warning in parsed_report["warnings"]
            )
            if not concession_features:
                raise RoadsBuildError("Nenhuma geometria rodoviária foi identificada.")
            processed.append(concession_id)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            report["errors"] = [*report.get("errors", []), message]
            build_errors.append(f"{concession_id}: {message}")
            failed.append(concession_id)
        concession_reports[concession_id] = report

    features.sort(key=lambda feature: str(feature["properties"]["feature_id"]))
    if not features:
        raise RoadsBuildError(
            "Build sem features: " + "; ".join(build_errors or ["nenhuma fonte válida"])
        )
    geojson = {
        "type": "FeatureCollection",
        "name": "motiva-sp-roads-state",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": features,
    }
    validation = validate_output_geojson(geojson)
    all_expected_roads = [
        str(road["road_id"])
        for concession_id in EXPECTED_STATE_CONCESSIONS
        for road in concessions_by_id[concession_id].get("roads", [])
    ]
    exactly_matched_road_ids = set(validation["road_ids"])
    ambiguous_subsegment_road_ids = {
        str(road_id)
        for concession_report in concession_reports.values()
        for road_id in concession_report.get("ambiguous_subsegment_road_ids", [])
    }
    matched_road_ids = exactly_matched_road_ids | ambiguous_subsegment_road_ids
    unmatched_road_ids = sorted(set(all_expected_roads) - matched_road_ids)
    build_warnings.extend(
        f"ROAD_ENTRY_WITHOUT_OFFICIAL_AXIS_GEOMETRY: {road_id}"
        for road_id in unmatched_road_ids
    )
    report = {
        "generated_at": generated_at or _generated_at(),
        "manifest_path": str(manifest_source),
        "concessions_requested": len(EXPECTED_STATE_CONCESSIONS),
        "concessions_processed": len(processed),
        "concessions_processed_ids": processed,
        "concessions_failed": len(failed),
        "concessions_failed_ids": failed,
        "roads_expected": len(all_expected_roads),
        "roads_matched": len(matched_road_ids),
        "roads_exactly_matched": len(exactly_matched_road_ids),
        "roads_ambiguously_matched": len(ambiguous_subsegment_road_ids),
        "ambiguous_subsegment_road_ids": sorted(ambiguous_subsegment_road_ids),
        "roads_unmatched": len(unmatched_road_ids),
        "unmatched_road_ids": unmatched_road_ids,
        "features_written": len(features),
        "features_rejected": total_rejected,
        "geometry_types": validation["geometry_types"],
        "approximate_total_length_km": approximate_length_km(features),
        "downloads": downloads,
        "warnings": list(dict.fromkeys(build_warnings)),
        "errors": build_errors,
        "federal_geometry_status": FEDERAL_PENDING,
        "concessions": concession_reports,
        "output_validation": validation,
    }
    output = Path(output_path)
    public_output = Path(public_output_path)
    report_output = Path(report_path)
    _write_json(output, geojson)
    _write_json(public_output, geojson)
    _write_json(report_output, report)
    return {
        "report": report,
        "geojson": geojson,
        "output_path": str(output),
        "public_output_path": str(public_output),
        "report_path": str(report_output),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Constrói a malha GeoJSON das concessões estaduais Motiva em SP."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Manifest JSON (padrão: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"GeoJSON processado (padrão: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Baixa novamente os KMZ mesmo quando o cache é válido.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_motiva_sp_roads(
            arguments.manifest,
            arguments.output,
            force_download=arguments.force_download,
        )
    except Exception as exc:
        print(f"Erro fatal: {exc}", file=sys.stderr)
        return 1
    report = result["report"]
    print("Build concluído.")
    print(f"Concessões processadas: {report['concessions_processed']}")
    print(f"Rodovias identificadas: {report['roads_matched']}")
    print(f"Features: {report['features_written']}")
    print(f"GeoJSON: {result['output_path']}")
    print(f"Relatório: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
