"""Probe CMR testado sem acesso a internet."""

from __future__ import annotations

import json
from datetime import date
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from scripts.probe_nasa_coverage import (
    PRODUCTS,
    Product,
    build_cmr_query,
    load_aoi,
    parse_candidate_granules,
    probe_coverage,
)

AOI_PATH = "data/aoi/aoi_louveira_teste.geojson"


def _cmr_payload(entries: list[dict]) -> bytes:
    return json.dumps({"feed": {"entry": entries}}).encode("utf-8")


def test_extracts_real_louveira_aoi_as_polygon() -> None:
    spatial = load_aoi(AOI_PATH)

    assert spatial.query_type == "polygon"
    assert spatial.bbox == (
        -46.95598865806708,
        -23.12195338218655,
        -46.95452534224725,
        -23.119678343668863,
    )
    assert len(spatial.coordinates) == 18
    assert spatial.fallback_reason is None


def test_builds_gedi_l2a_v002_query() -> None:
    spatial = load_aoi(AOI_PATH)
    query = parse_qs(urlparse(build_cmr_query(
        PRODUCTS[0],
        spatial,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
    )).query)

    assert query["short_name"] == ["GEDI02_A"]
    assert query["version"] == ["002"]
    assert query["page_size"] == ["5"]
    assert "polygon" in query
    assert query["temporal"] == ["2026-07-01T00:00:00Z,2026-07-31T23:59:59Z"]


def test_builds_icesat2_atl08_v007_historical_query() -> None:
    spatial = load_aoi(AOI_PATH)
    query = parse_qs(urlparse(build_cmr_query(PRODUCTS[1], spatial)).query)

    assert query["short_name"] == ["ATL08"]
    assert query["version"] == ["007"]
    assert "polygon" in query
    assert "temporal" not in query


def test_parses_candidate_granules_without_dumping_full_cmr_response() -> None:
    entry = {
        "id": "G123-TEST",
        "title": "example-granule",
        "producer_granule_id": "producer-id",
        "time_start": "2026-07-01T00:00:00Z",
        "time_end": "2026-07-01T01:00:00Z",
        "data_center": "TEST_PROVIDER",
        "boxes": ["-24 -47 -23 -46"],
        "links": [{"href": "https://example.test/granule", "rel": "data", "ignored": "value"}],
        "large_internal_field": {"must": "not leak"},
    }

    parsed = parse_candidate_granules(
        _cmr_payload([entry]),
        {"CMR-Hits": "12"},
    )

    assert parsed["candidate_granule_count"] == 12
    assert parsed["candidate_status"] == "candidates_found"
    assert parsed["sample"] == [{
        "granule_id": "producer-id",
        "title": "example-granule",
        "concept_id": "G123-TEST",
        "beginning_datetime": "2026-07-01T00:00:00Z",
        "ending_datetime": "2026-07-01T01:00:00Z",
        "provider": "TEST_PROVIDER",
        "links": [{"href": "https://example.test/granule", "rel": "data"}],
        "spatial": {"boxes": ["-24 -47 -23 -46"]},
    }]


def test_parses_zero_candidates() -> None:
    parsed = parse_candidate_granules(
        _cmr_payload([]),
        {"cmr-hits": "0"},
    )

    assert parsed == {
        "candidate_status": "no_candidates",
        "candidate_granule_count": 0,
        "sample": [],
    }


def test_one_source_failure_does_not_prevent_the_other() -> None:
    def fake_http_get(url: str, timeout: float):
        query = parse_qs(urlparse(url).query)
        assert timeout == 15.0
        if query["short_name"] == ["GEDI02_A"]:
            raise URLError("private network detail")
        entry = {"id": "G-ATL08", "title": "ATL08 candidate"}
        return _cmr_payload([entry]), {"CMR-Hits": "1"}

    report = probe_coverage(
        AOI_PATH,
        date(2026, 7, 1),
        date(2026, 7, 31),
        http_get=fake_http_get,
    )
    sources = report["sources"]

    assert sources["gedi_l2a"]["candidate_status"] == "query_error"
    assert sources["gedi_l2a"]["technical_decision"] == "UNABLE_TO_VERIFY"
    assert sources["icesat2_atl08"]["candidate_status"] == "candidates_found"
    assert sources["icesat2_atl08"]["technical_decision"] == "GO_TO_FOOTPRINT_VALIDATION"
    assert "private network detail" not in json.dumps(report)
