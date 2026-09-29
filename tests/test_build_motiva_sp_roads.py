"""Testes offline do builder da malha estadual Motiva em São Paulo."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts import build_motiva_sp_roads as roads


def _kml(*placemarks: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Fixture</name>'
        '<Folder><name>Ativos Lineares</name><Folder><name>Traçado</name>'
        + "".join(placemarks)
        + "</Folder></Folder></Document></kml>"
    ).encode("utf-8")


def _line_placemark(
    name: str,
    coordinates: str = "-46.8,-23.5,0 -46.7,-23.4,0",
    *,
    extra_line: str | None = None,
) -> str:
    lines = f"<LineString><coordinates>{coordinates}</coordinates></LineString>"
    if extra_line:
        lines = (
            "<MultiGeometry>"
            + lines
            + f"<LineString><coordinates>{extra_line}</coordinates></LineString>"
            + "</MultiGeometry>"
        )
    return (
        f"<Placemark><name>{name}</name><ExtendedData>"
        f'<Data name="rodovia"><value>{name}</value></Data>'
        f"</ExtendedData>{lines}</Placemark>"
    )


def _write_kmz(path: Path, kml: bytes, member: str = "nested/doc.kml") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, kml)
    return path


def _manifest() -> dict[str, Any]:
    definitions = (
        ("autoban", "AutoBAn", "SP-330"),
        ("rodoanel_oeste", "Rodoanel Oeste", "SP-021"),
        ("spvias", "SPVias", "SP-270"),
        ("sorocabana", "Sorocabana", "SP-075"),
        ("renovias", "Renovias", "SP-340"),
    )
    return {
        "concessions": [
            {
                "concession_id": concession_id,
                "display_name": display_name,
                "motiva_participation_pct": 100,
                "geometry": {
                    "preferred_source": roads.ARTESP_SOURCE,
                    "download_url": f"https://example.test/{concession_id}.kmz",
                    "resource_id": f"resource-{concession_id}",
                    "license": "Public Domain",
                },
                "roads": [
                    {
                        "road_id": road_id,
                        "name": f"Rodovia {road_id}",
                        "km_start": 0,
                        "km_end": 100,
                    }
                ],
            }
            for concession_id, display_name, road_id in definitions
        ]
    }


def test_loads_existing_manifest() -> None:
    manifest = roads.load_manifest(roads.DEFAULT_MANIFEST)
    assert len(manifest["concessions"]) >= 5


def test_invalid_manifest_fails_objectively(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(roads.RoadsBuildError, match="JSON inválido"):
        roads.load_manifest(path)


def test_extracts_kmz_and_discovers_nested_kml(tmp_path: Path) -> None:
    kmz = _write_kmz(tmp_path / "sample.kmz", _kml(_line_placemark("SP-330")))
    member, content = roads.extract_kml_from_kmz(kmz)
    assert member == "nested/doc.kml"
    assert b"SP-330" in content


def test_parses_linestring() -> None:
    records = roads.parse_kml_placemarks(_kml(_line_placemark("SP-330")))
    assert records[0]["geometry"].geom_type == "LineString"


def test_parses_multilinestring() -> None:
    records = roads.parse_kml_placemarks(
        _kml(
            _line_placemark(
                "SP-330",
                extra_line="-46.6,-23.3,0 -46.5,-23.2,0",
            )
        )
    )
    assert records[0]["geometry"].geom_type == "MultiLineString"


def test_rejects_non_line_geometry() -> None:
    polygon = (
        "<Placemark><name>SP-330</name><Polygon><outerBoundaryIs><LinearRing>"
        "<coordinates>-46,-23 -45,-23 -45,-22 -46,-23</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )
    records = roads.parse_kml_placemarks(_kml(polygon))
    assert records[0]["geometry"] is None
    assert "Polygon" in records[0]["source_geometry_types"]


def test_distinguishes_official_axis_from_auxiliary_line() -> None:
    official = roads.parse_kml_placemarks(_kml(_line_placemark("SP-330")))[0]
    auxiliary_kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        '<Folder><name>Ativos Lineares</name><Folder><name>Dispositivos</name>'
        + _line_placemark("SP-330")
        + "</Folder></Folder></Document></kml>"
    ).encode("utf-8")
    auxiliary = roads.parse_kml_placemarks(auxiliary_kml)[0]
    assert roads.is_official_road_axis(official["properties"])
    assert not roads.is_official_road_axis(auxiliary["properties"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SP330", "SP-330"),
        ("sp 021", "SP-021"),
        ("SPI-102/330", "SPI-102/330"),
        ("SPA 160/250", "SPA-160/250"),
        ("SPA-104-079", "SPA-104/079"),
    ],
)
def test_normalizes_road_reference(raw: str, expected: str) -> None:
    assert roads.normalize_road_ref(raw) == expected


@pytest.mark.parametrize(
    ("road_id", "expected"),
    [
        ("SP-127-A", "SP-127"),
        ("SP-127-B", "SP-127"),
        ("SP-250-1", "SP-250"),
        ("SP-250-3", "SP-250"),
        ("SP-270-2", "SP-270"),
    ],
)
def test_canonical_road_ref_for_subdivided_ids(
    road_id: str, expected: str
) -> None:
    assert roads.canonical_road_ref(road_id) == expected


def test_subsegment_is_matched_by_reliable_km_range() -> None:
    manifest_roads = [
        {
            "road_id": "SP-250-1",
            "ref": "SP-250",
            "name": "Trecho 1",
            "km_start": 45,
            "km_end": 68.7,
        },
        {
            "road_id": "SP-250-2",
            "ref": "SP-250",
            "name": "Trecho 2",
            "km_start": 70.99,
            "km_end": 101.18,
        },
    ]
    matched, method, _references = roads.match_manifest_road(
        {"rodovia": "SP-250", "km inicial": "71", "km final": "100"},
        manifest_roads,
    )
    assert matched is not None
    assert matched["road_id"] == "SP-250-2"
    assert method == "canonical_road_ref_and_km_range"


def test_ambiguous_subsegment_keeps_canonical_geometry(tmp_path: Path) -> None:
    concession = {
        "concession_id": "spvias",
        "display_name": "SPVias",
        "motiva_participation_pct": 100,
        "geometry": {
            "resource_id": "fixture",
            "license": "Public Domain",
        },
        "roads": [
            {
                "road_id": "SP-127-A",
                "ref": "SP-127",
                "name": "Trecho A",
                "km_start": 105.9,
                "km_end": 148.35,
            },
            {
                "road_id": "SP-127-B",
                "ref": "SP-127",
                "name": "Trecho B",
                "km_start": 158.3,
                "km_end": 213.15,
            },
        ],
    }
    kmz = _write_kmz(tmp_path / "sp127.kmz", _kml(_line_placemark("SP-127")))
    features, report = roads.process_concession_kmz(concession, kmz)
    assert len(features) == 1
    properties = features[0]["properties"]
    assert properties["road_id"] is None
    assert properties["road_ref"] == "SP-127"
    assert properties["geometry_status"] == "AMBIGUOUS_SUBSEGMENT_MATCH"
    assert properties["candidate_road_ids"] == ["SP-127-A", "SP-127-B"]
    assert report["ambiguous_subsegment_road_ids"] == ["SP-127-A", "SP-127-B"]
    assert any("AMBIGUOUS_SUBSEGMENT_MATCH" in item for item in report["warnings"])


@pytest.mark.parametrize("road_ref", ["SP-330", "SP-348", "SP-021"])
def test_existing_base_roads_remain_exact_matches(road_ref: str) -> None:
    matched, method, _references = roads.match_manifest_road(
        {"rodovia": road_ref},
        [{"road_id": road_ref, "name": f"Rodovia {road_ref}"}],
    )
    assert matched is not None
    assert matched["road_id"] == road_ref
    assert method == "exact_normalized_road_ref"


def test_excludes_geometry_clearly_outside_sp() -> None:
    assert roads.geometry_is_in_expected_sp_extent(
        roads.LineString([(-46.8, -23.5), (-46.7, -23.4)])
    )
    assert not roads.geometry_is_in_expected_sp_extent(
        roads.LineString([(-43.2, -22.9), (-43.1, -22.8)])
    )


def test_prefers_specific_access_reference_over_parent_road() -> None:
    roads_in_manifest = [
        {"road_id": "SP-330", "name": "Rodovia Anhanguera"},
        {"road_id": "SPI-102/330", "name": "Acesso"},
    ]
    matched, method, references = roads.match_manifest_road(
        {"__folder_path": "Traçado / SP-330 / SPI-102/330"}, roads_in_manifest
    )
    assert matched is not None
    assert matched["road_id"] == "SPI-102/330"
    assert method == "exact_specific_road_ref"
    assert references == ["SP-330", "SPI-102/330"]


def test_download_cache_avoids_second_download(tmp_path: Path) -> None:
    payload = tmp_path / "fixture.kmz"
    _write_kmz(payload, _kml(_line_placemark("SP-330")))
    calls = 0

    def downloader(_url: str, destination: Path) -> None:
        nonlocal calls
        calls += 1
        destination.write_bytes(payload.read_bytes())

    destination = tmp_path / "raw" / "autoban.kmz"
    first = roads.ensure_kmz_download(
        "https://example.test/autoban.kmz", destination, downloader=downloader
    )
    second = roads.ensure_kmz_download(
        "https://example.test/autoban.kmz", destination, downloader=downloader
    )
    assert first["status"] == "downloaded"
    assert second["status"] == "cache_hit"
    assert calls == 1


def test_build_report_required_properties_and_deterministic_output(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payloads: dict[str, bytes] = {}
    for concession in manifest["concessions"]:
        road_id = concession["roads"][0]["road_id"]
        kmz = _write_kmz(
            tmp_path / f"source-{concession['concession_id']}.kmz",
            _kml(_line_placemark(road_id)),
        )
        payloads[concession["concession_id"]] = kmz.read_bytes()

    def downloader(url: str, destination: Path) -> None:
        concession_id = Path(url).stem
        destination.write_bytes(payloads[concession_id])

    first = roads.build_motiva_sp_roads(
        manifest_path,
        tmp_path / "first.geojson",
        public_output_path=tmp_path / "first-public.geojson",
        report_path=tmp_path / "first-report.json",
        raw_dir=tmp_path / "raw",
        downloader=downloader,
        progress=lambda _message: None,
        generated_at="2026-08-25T00:00:00+00:00",
    )
    second = roads.build_motiva_sp_roads(
        manifest_path,
        tmp_path / "second.geojson",
        public_output_path=tmp_path / "second-public.geojson",
        report_path=tmp_path / "second-report.json",
        raw_dir=tmp_path / "raw",
        downloader=downloader,
        progress=lambda _message: None,
        generated_at="2026-08-25T00:00:00+00:00",
    )
    assert (tmp_path / "first.geojson").read_bytes() == (
        tmp_path / "second.geojson"
    ).read_bytes()
    assert first["report"]["features_written"] == 5
    assert first["report"]["concessions_processed"] == 5
    assert first["report"]["federal_geometry_status"] == roads.FEDERAL_PENDING
    assert second["report"]["roads_matched"] == 5
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
    assert required.issubset(first["geojson"]["features"][0]["properties"])


def test_cli_help_works() -> None:
    completed = subprocess.run(
        [sys.executable, str(roads.Path(roads.__file__)), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "--force-download" in completed.stdout


def test_cli_default_manifest_and_success_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_builder(manifest: Path, output: Path, **_kwargs: Any) -> dict[str, Any]:
        captured.update(manifest=manifest, output=output)
        return {
            "report": {
                "concessions_processed": 5,
                "roads_matched": 5,
                "features_written": 5,
            },
            "output_path": str(output),
            "report_path": str(roads.DEFAULT_REPORT),
        }

    monkeypatch.setattr(roads, "build_motiva_sp_roads", fake_builder)
    assert roads.main([]) == 0
    assert captured["manifest"] == roads.DEFAULT_MANIFEST
    assert captured["output"] == roads.DEFAULT_OUTPUT


def test_cli_fatal_error_returns_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise roads.RoadsBuildError("fixture failure")

    monkeypatch.setattr(roads, "build_motiva_sp_roads", fail)
    assert roads.main([]) != 0
