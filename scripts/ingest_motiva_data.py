"""Ingestao dos dados de referencia fornecidos pela CCR Motiva.

Le os arquivos originais do desafio e popula as tabelas de referencia do banco:

* ``Marco km_rodoanel 2.kmz``  -> marcos quilometricos (KMZ real, ZIP com doc.kml);
* ``classificacao_rocada.kmz`` -> poligonos de metodo de rocada (KML cru, apesar
  da extensao .kmz);
* ``RA-RET-ROC-LIMP-*.xlsx``   -> unifilar de condicao: classes de altura por
  quilometragem e posicao transversal.

O parser usa apenas a biblioteca padrao (``zipfile`` + ``xml.etree``); nao exige
``openpyxl`` nem ``lxml``.

Uso::

    python -m scripts.ingest_motiva_data --data-dir "C:/.../Arquivos - Dados challenge MOTIVA"

A ingestao e idempotente: as tabelas de referencia sao recriadas a cada execucao.
O historico de analises (tabela ``analysis``) nunca e tocado.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree as ET

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.satellite_monitoring.database import (
    DEFAULT_HEIGHT_LIMIT_CM,
    HEIGHT_CLASS_RANGES,
    STRICT_HEIGHT_LIMIT_CM,
    CrossSectionPosition,
    FieldConditionObservation,
    Highway,
    KmMarker,
    MowingPolygon,
    Segment,
    init_database,
    session_scope,
)

HIGHWAY_CODE = "SP-021"
HIGHWAY_NAME = "Rodoanel Mario Covas - Trecho Oeste"
SEGMENT_LENGTH_M = 500

# Posicoes que respondem ao limite estrito de 10 cm (areas nobres: dispositivos,
# trevos e alcas). Ver ANTT PER p. 31 item 3 e ARTESP Anexo 06 item b.1.1.
STRICT_LIMIT_KINDS = {"dispositivo"}

BASIS_STRICT = "ANTT PER p.31 item 3; ARTESP Anexo 06 b.1.1 (areas nobres, 10 cm)"
BASIS_DEFAULT = "ANTT PER p.31 item 6; ARTESP Anexo 06 b.1.1 (faixa de dominio, 30 cm)"


# --------------------------------------------------------------------------- #
# Utilidades XML
# --------------------------------------------------------------------------- #
def _strip_namespace(tree: ET.Element) -> ET.Element:
    for element in tree.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return tree


def _load_kml(path: Path) -> ET.Element:
    """Le um KML que pode estar dentro de um ZIP (.kmz) ou ser XML cru."""

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            name = next(
                (item for item in archive.namelist() if item.lower().endswith(".kml")),
                None,
            )
            if name is None:
                raise ValueError(f"{path.name}: nenhum .kml dentro do arquivo")
            data = archive.read(name)
    else:
        data = path.read_bytes()
    return _strip_namespace(ET.fromstring(data))


# --------------------------------------------------------------------------- #
# Marcos quilometricos
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParsedKmMarker:
    km: int
    longitude: float
    latitude: float


def parse_km_markers(path: Path) -> list[ParsedKmMarker]:
    """Extrai os marcos quilometricos.

    O km aparece apenas no ``<description>`` como texto livre ("SP021\\nkm 12"),
    com formatacao inconsistente. A posicao vem do ``<Point>``; o bloco
    ``<LookAt>`` e uma camera e nao deve ser usado como coordenada.
    """

    root = _load_kml(path)
    markers: dict[int, ParsedKmMarker] = {}
    for placemark in root.iter("Placemark"):
        point = placemark.find("Point")
        if point is None:
            continue
        coordinates = point.findtext("coordinates") or ""
        parts = coordinates.strip().split(",")
        if len(parts) < 2:
            continue
        description = placemark.findtext("description") or ""
        match = re.search(r"km\s*(\d+)", description, re.IGNORECASE)
        if match is None:
            continue
        km = int(match.group(1))
        markers[km] = ParsedKmMarker(
            km=km, longitude=float(parts[0]), latitude=float(parts[1])
        )
    return [markers[km] for km in sorted(markers)]


# --------------------------------------------------------------------------- #
# Poligonos de classificacao de rocada
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParsedMowingPolygon:
    method: str
    km: int | None
    area_m2: float | None
    longitude: float | None
    latitude: float | None
    geometry: dict[str, Any]


def _ring_coordinates(element: ET.Element) -> list[list[float]]:
    text = element.findtext("LinearRing/coordinates") or ""
    ring: list[list[float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            ring.append([float(parts[0]), float(parts[1])])
    return ring


def parse_mowing_polygons(path: Path) -> list[ParsedMowingPolygon]:
    """Extrai os poligonos de metodo de rocada.

    Atencao: os nomes declarados no ``<Schema>`` estao deslocados em relacao aos
    valores. Verificado contra a geometria: ``classe`` contem a latitude, ``KM``
    contem a longitude e ``Latitude`` contem a area em m2. A classe real esta em
    ``<name>`` e o km em ``<description>``.
    """

    root = _load_kml(path)
    polygons: list[ParsedMowingPolygon] = []
    for placemark in root.iter("Placemark"):
        polygon_element = placemark.find("Polygon")
        if polygon_element is None:
            continue

        fields: dict[str, str] = {}
        for simple in placemark.iter("SimpleData"):
            name = simple.get("name")
            if name:
                fields[name] = (simple.text or "").strip()

        def _number(raw: str | None) -> float | None:
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        outer = polygon_element.find("outerBoundaryIs")
        if outer is None:
            continue
        rings = [_ring_coordinates(outer)]
        for inner in polygon_element.findall("innerBoundaryIs"):
            hole = _ring_coordinates(inner)
            if hole:
                rings.append(hole)
        if not rings[0]:
            continue

        description = (placemark.findtext("description") or "").strip()
        km_match = re.search(r"\d+", description)
        method = (placemark.findtext("name") or "").strip() or "nao informado"

        polygons.append(
            ParsedMowingPolygon(
                method=method,
                km=int(km_match.group()) if km_match else None,
                area_m2=_number(fields.get("Latitude")),
                longitude=_number(fields.get("KM")),
                latitude=_number(fields.get("classe")),
                geometry={"type": "Polygon", "coordinates": rings},
            )
        )
    return polygons


# --------------------------------------------------------------------------- #
# Unifilar (XLSX)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParsedCondition:
    position_code: str
    position_description: str
    chainage_m: int
    height_class: str


def _column_index(reference: str) -> int:
    letters = "".join(ch for ch in reference if ch.isalpha())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _row_index(reference: str) -> int:
    digits = "".join(ch for ch in reference if ch.isdigit())
    return int(digits) if digits else 0


def _read_sheet_cells(path: Path) -> dict[tuple[int, int], str]:
    """Le a primeira planilha do xlsx retornando {(linha, coluna): valor}."""

    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings_root = _strip_namespace(
                ET.fromstring(archive.read("xl/sharedStrings.xml"))
            )
            for item in strings_root.findall("si"):
                shared.append("".join(node.text or "" for node in item.iter("t")))

        sheet_names = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        ]
        if not sheet_names:
            raise ValueError(f"{path.name}: nenhuma planilha encontrada")
        sheet_root = _strip_namespace(ET.fromstring(archive.read(sorted(sheet_names)[0])))

    cells: dict[tuple[int, int], str] = {}
    for cell in sheet_root.iter("c"):
        reference = cell.get("r")
        if not reference:
            continue
        cell_type = cell.get("t")
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter("t"))
        else:
            raw = cell.findtext("v")
            if raw is None:
                continue
            if cell_type == "s":
                try:
                    value = shared[int(raw)]
                except (ValueError, IndexError):
                    value = ""
            else:
                value = raw
        value = value.strip()
        if value:
            cells[(_row_index(reference), _column_index(reference))] = value
    return cells


def _normalize_class(value: str) -> str | None:
    text = value.strip().upper()
    if text in HEIGHT_CLASS_RANGES:
        return text
    try:
        number = int(float(text))
    except ValueError:
        return None
    return str(number) if str(number) in HEIGHT_CLASS_RANGES else None


def parse_unifilar(path: Path) -> tuple[date | None, list[ParsedCondition]]:
    """Extrai a matriz de condicao: posicao transversal x quilometragem."""

    cells = _read_sheet_cells(path)
    if not cells:
        return None, []

    # Linha de cabecalho: a que contem a maior quantidade de quilometragens.
    header_row, km_columns = None, {}
    for row in sorted({row for row, _ in cells}):
        candidates: dict[int, int] = {}
        for (cell_row, column), value in cells.items():
            if cell_row != row or column < _column_index("F"):
                continue
            try:
                metres = int(float(value))
            except ValueError:
                continue
            if 0 <= metres <= 400_000:
                candidates[column] = metres
        if len(candidates) > len(km_columns):
            header_row, km_columns = row, candidates
    if header_row is None or not km_columns:
        return None, []

    conditions: list[ParsedCondition] = []
    for row in sorted({row for row, _ in cells if row > header_row}):
        code = cells.get((row, _column_index("A")), "")
        if not re.fullmatch(r"1\.\d+", code.strip()):
            continue
        description = cells.get((row, _column_index("B")), "").strip()
        for column, metres in km_columns.items():
            raw = cells.get((row, column))
            if raw is None:
                continue
            height_class = _normalize_class(raw)
            if height_class is None:
                continue
            conditions.append(
                ParsedCondition(
                    position_code=code.strip(),
                    position_description=description,
                    chainage_m=metres,
                    height_class=height_class,
                )
            )

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", path.name)
    observed_on = (
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if match
        else None
    )
    return observed_on, conditions


# --------------------------------------------------------------------------- #
# Classificacao das posicoes transversais
# --------------------------------------------------------------------------- #
def classify_position(description: str) -> tuple[str | None, str | None]:
    """Deriva (lado, tipo) da descricao do item do unifilar."""

    text = description.upper()
    side = None
    if "INT" in text:
        side = "interna"
    if "EXT" in text:
        side = "externa"

    if "DISPOSITIVO" in text:
        kind = "dispositivo"
    elif "MARGINAL" in text:
        kind = "marginal"
    elif "LATERAL" in text:
        kind = "lateral"
    elif "CENTRAL" in text:
        kind = "central"
    elif "PISTA" in text:
        kind = "pista"
    else:
        kind = None
    return side, kind


def height_limit_for(kind: str | None) -> tuple[float, str]:
    if kind in STRICT_LIMIT_KINDS:
        return STRICT_HEIGHT_LIMIT_CM, BASIS_STRICT
    return DEFAULT_HEIGHT_LIMIT_CM, BASIS_DEFAULT


# --------------------------------------------------------------------------- #
# Localizacao dos arquivos e semeadura
# --------------------------------------------------------------------------- #
def _find(root: Path, pattern: str) -> list[Path]:
    return sorted(path for path in root.rglob(pattern) if path.is_file())


def _clear_reference_tables(session: Session) -> None:
    session.execute(delete(FieldConditionObservation))
    session.execute(delete(Segment))
    session.execute(delete(CrossSectionPosition))
    session.execute(delete(MowingPolygon))
    session.execute(delete(KmMarker))
    session.execute(delete(Highway))


def seed(data_dir: Path) -> dict[str, int]:
    marker_files = _find(data_dir, "Marco km*.kmz")
    polygon_files = _find(data_dir, "classificacao_rocada.kmz")
    unifilar_files = _find(data_dir, "*.xlsx")
    if not (marker_files or polygon_files or unifilar_files):
        raise FileNotFoundError(f"Nenhum arquivo da CCR encontrado em {data_dir}")

    counters = {
        "km_markers": 0,
        "mowing_polygons": 0,
        "positions": 0,
        "segments": 0,
        "field_observations": 0,
        "surveys": 0,
    }

    init_database()
    with session_scope() as session:
        _clear_reference_tables(session)

        highway = Highway(code=HIGHWAY_CODE, name=HIGHWAY_NAME)
        session.add(highway)
        session.flush()

        for path in marker_files:
            for marker in parse_km_markers(path):
                session.add(
                    KmMarker(
                        highway_id=highway.id,
                        km=marker.km,
                        longitude=marker.longitude,
                        latitude=marker.latitude,
                    )
                )
                counters["km_markers"] += 1

        for path in polygon_files:
            for polygon in parse_mowing_polygons(path):
                session.add(
                    MowingPolygon(
                        highway_id=highway.id,
                        km=polygon.km,
                        method=polygon.method,
                        area_m2=polygon.area_m2,
                        centroid_longitude=polygon.longitude,
                        centroid_latitude=polygon.latitude,
                        geometry=polygon.geometry,
                    )
                )
                counters["mowing_polygons"] += 1

        positions: dict[str, CrossSectionPosition] = {}
        segments: dict[tuple[str, int], Segment] = {}

        for path in unifilar_files:
            observed_on, conditions = parse_unifilar(path)
            if observed_on is None or not conditions:
                continue
            counters["surveys"] += 1

            for condition in conditions:
                position = positions.get(condition.position_code)
                if position is None:
                    side, kind = classify_position(condition.position_description)
                    limit, basis = height_limit_for(kind)
                    position = CrossSectionPosition(
                        code=condition.position_code,
                        description=condition.position_description,
                        side=side,
                        kind=kind,
                        height_limit_cm=limit,
                        regulatory_basis=basis,
                    )
                    session.add(position)
                    session.flush()
                    positions[condition.position_code] = position
                    counters["positions"] += 1

                key = (condition.position_code, condition.chainage_m)
                segment = segments.get(key)
                if segment is None:
                    segment = Segment(
                        highway_id=highway.id,
                        position_id=position.id,
                        chainage_start_m=condition.chainage_m,
                        chainage_end_m=condition.chainage_m + SEGMENT_LENGTH_M,
                    )
                    session.add(segment)
                    session.flush()
                    segments[key] = segment
                    counters["segments"] += 1

                minimum, maximum = HEIGHT_CLASS_RANGES[condition.height_class]
                session.add(
                    FieldConditionObservation(
                        segment_id=segment.id,
                        observed_on=observed_on,
                        height_class=condition.height_class,
                        height_min_cm=minimum,
                        height_max_cm=maximum,
                        applicable=condition.height_class != "X",
                        source="ccr_unifilar",
                        source_file=path.name,
                    )
                )
                counters["field_observations"] += 1

    return counters


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="Pasta com os arquivos originais fornecidos pela CCR Motiva.",
    )
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    data_dir = arguments.data_dir.expanduser()
    if not data_dir.is_dir():
        parser.error(f"Diretorio inexistente: {data_dir}")

    counters = seed(data_dir)
    print("Ingestao concluida:")
    for name in sorted(counters):
        print(f"  {name}: {counters[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
