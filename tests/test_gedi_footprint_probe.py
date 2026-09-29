"""Validacao offline do probe de footprints GEDI02_A."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import pytest
from shapely.geometry import Polygon

from scripts.probe_gedi_footprints import (
    classify_gedi_quality,
    parse_gedi_hdf5,
    summarize_gedi_validation,
    validate_gedi_footprints,
)


def _aoi() -> Polygon:
    return Polygon([
        (-0.001, -0.001),
        (0.001, -0.001),
        (0.001, 0.001),
        (-0.001, 0.001),
        (-0.001, -0.001),
    ])


def _write_beam(
    handle: h5py.File,
    name: str,
    longitudes: list[float],
    latitudes: list[float],
) -> None:
    beam = handle.create_group(name)
    count = len(longitudes)
    beam.create_dataset("lon_lowestmode", data=np.asarray(longitudes))
    beam.create_dataset("lat_lowestmode", data=np.asarray(latitudes))
    beam.create_dataset(
        "shot_number", data=np.arange(10**17, 10**17 + count, dtype=np.uint64)
    )
    beam.create_dataset("delta_time", data=np.arange(count) + 60.25)
    beam.create_dataset("quality_flag", data=np.asarray([1, 1, 0, 1, 1, 1, 1][:count]))
    beam.create_dataset("degrade_flag", data=np.asarray([0, 1, 0, 0, 0, 0, 0][:count]))
    beam.create_dataset("sensitivity", data=np.linspace(0.8, 0.9, count))
    beam.create_dataset("elev_lowestmode", data=np.arange(count) + 700.0)
    beam.create_dataset("elev_highestreturn", data=np.arange(count) + 710.0)
    rh = np.tile(np.arange(101, dtype=float), (count, 1))
    beam.create_dataset("rh", data=rh)


def _write_granule(path: Path) -> None:
    with h5py.File(path, "w") as handle:
        _write_beam(
            handle,
            "BEAM0000",
            [0.0, 0.0012, 0.0020, 0.0030, 0.0040, np.nan, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 91.0],
        )
        unsupported = handle.create_group("BEAM0101")
        unsupported.create_dataset("unrelated", data=[1])
        handle.create_group("METADATA")


def test_reads_real_footprint_fields_and_discovers_beams(tmp_path: Path) -> None:
    path = tmp_path / "GEDI02_A_test.h5"
    _write_granule(path)

    parsed = parse_gedi_hdf5(
        path,
        geometry=_aoi(),
        granule_observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )

    assert parsed["footprints_total"] == 5
    assert parsed["footprints_inside_aoi"] == 1
    assert parsed["footprints_near_aoi"] == 1
    assert parsed["footprints_within_50m"] == 2
    assert parsed["footprints_outside_relevance"] == 3
    assert set(parsed["schema"]["beams"]) == {"BEAM0000", "BEAM0101"}
    assert parsed["schema"]["beams"]["BEAM0101"]["status"] == "unsupported_schema"
    closest = parsed["nearest_footprints"][0]
    assert closest["shot_number"] == 10**17
    assert closest["observed_at"] == "2018-01-01T00:01:00.250000+00:00"
    assert closest["quality_flag"] == 1
    assert closest["degrade_flag"] == 0
    assert closest["sensitivity"] == pytest.approx(0.8)
    assert closest["elev_lowestmode"] == 700.0
    assert closest["elev_highestreturn"] == 710.0
    assert closest["rh98"] == 98.0
    assert closest["quality_status"] == "usable_candidate"


def test_invalid_coordinates_are_ignored_and_nearest_five_are_sorted(tmp_path: Path) -> None:
    path = tmp_path / "GEDI02_A_distance.h5"
    with h5py.File(path, "w") as handle:
        _write_beam(
            handle,
            "BEAM1011",
            [0.0012, 0.0014, 0.0016, 0.0018, 0.0020, 0.0022, np.nan],
            [0.0] * 7,
        )

    parsed = parse_gedi_hdf5(path, geometry=_aoi(), near_distance_meters=0)

    assert parsed["footprints_total"] == 6
    assert parsed["minimum_distance_to_aoi_m"] == pytest.approx(22.264, abs=0.1)
    nearest = parsed["nearest_footprints"]
    assert len(nearest) == 5
    assert [item["distance_to_aoi_m"] for item in nearest] == sorted(
        item["distance_to_aoi_m"] for item in nearest
    )
    assert [item["longitude"] for item in nearest] == [
        0.0012,
        0.0014,
        0.0016,
        0.0018,
        0.002,
    ]


@pytest.mark.parametrize(
    ("quality_flag", "degrade_flag", "expected"),
    [
        (1, 0, "usable_candidate"),
        (0, 0, "quality_rejected"),
        (1, 1, "quality_rejected"),
        (None, 0, "unknown_quality"),
        (1, None, "unknown_quality"),
        (2, 0, "unknown_quality"),
    ],
)
def test_quality_rule_is_conservative(quality_flag, degrade_flag, expected) -> None:
    assert classify_gedi_quality(quality_flag, degrade_flag)[0] == expected


def test_summary_keeps_global_nearest_five_and_sample_decision() -> None:
    def footprint(distance: float, quality: str = "quality_rejected") -> dict[str, object]:
        return {
            "granule_id": "g.h5",
            "beam": "BEAM0000",
            "shot_number": int(distance),
            "observed_at": "2020-01-01T00:00:00+00:00",
            "latitude": 0.0,
            "longitude": distance / 100000,
            "distance_to_aoi_m": distance,
            "quality_status": quality,
        }

    granules = [
        {
            "parse_status": "parsed",
            "footprints_total": 3,
            "footprints_inside_aoi": 0,
            "footprints_near_aoi": 0,
            "footprints_outside_relevance": 3,
            "nearest_footprints": [footprint(value) for value in (300, 50, 5)],
            "relevant_footprints": [],
            "observed_at_start": "2020-01-01T00:00:00+00:00",
            "observed_at_end": "2020-01-01T00:01:00+00:00",
        },
        {
            "parse_status": "parsed",
            "footprints_total": 3,
            "footprints_inside_aoi": 0,
            "footprints_near_aoi": 0,
            "footprints_outside_relevance": 3,
            "nearest_footprints": [footprint(value) for value in (2000, 1000, 100)],
            "relevant_footprints": [],
            "observed_at_start": "2020-01-02T00:00:00+00:00",
            "observed_at_end": "2020-01-02T00:01:00+00:00",
        },
    ]
    source = {
        "short_name": "GEDI02_A",
        "historical": {"candidate_granule_count": 17, "sample": []},
    }

    summary = summarize_gedi_validation(source, granules)

    assert summary["candidate_granules"] == 17
    assert summary["granules_inspected"] == 2
    assert summary["minimum_distance_to_aoi_m"] == 5
    assert summary["diagnostic_distance_class"] == "VERY_CLOSE"
    assert [item["distance_to_aoi_m"] for item in summary["nearest_footprints"]] == [
        5,
        50,
        100,
        300,
        1000,
    ]
    assert summary["observation_dates"] == ["2020-01-01", "2020-01-02"]
    assert summary["decision"] == "NO_RELEVANT_FOOTPRINTS_IN_SAMPLE"


def test_usable_relevant_footprint_changes_decision() -> None:
    source = {
        "short_name": "GEDI02_A",
        "historical": {"candidate_granule_count": 1, "sample": []},
    }
    footprint = {
        "quality_status": "usable_candidate",
        "distance_to_aoi_m": 0.0,
        "granule_id": "g.h5",
        "beam": "BEAM0000",
        "latitude": 0.0,
        "longitude": 0.0,
    }
    granule = {
        "parse_status": "parsed",
        "footprints_total": 1,
        "footprints_inside_aoi": 1,
        "footprints_near_aoi": 0,
        "footprints_outside_relevance": 0,
        "nearest_footprints": [footprint],
        "relevant_footprints": [footprint],
    }

    assert summarize_gedi_validation(source, [granule])["decision"] == (
        "GO_TO_PROVIDER_IMPLEMENTATION"
    )


def test_missing_authentication_preserves_discovery() -> None:
    report = {
        "sources": {
            "gedi_l2a": {
                "short_name": "GEDI02_A",
                "historical": {"candidate_granule_count": 17, "sample": []},
            }
        }
    }

    validation = validate_gedi_footprints(
        report,
        "unused.geojson",
        authentication=({"status": "auth_required", "strategy": None}, None),
    )

    assert validation["authentication"]["status"] == "auth_required"
    assert validation["gedi"]["candidate_granules"] == 17
    assert validation["gedi"]["granules_inspected"] == 0
    assert validation["gedi"]["decision"] == "AUTH_REQUIRED"


def test_download_failure_is_fail_soft_and_next_granule_is_parsed(tmp_path: Path) -> None:
    good_path = tmp_path / "GEDI02_A_good.h5"
    _write_granule(good_path)

    class Earthaccess:
        calls = 0

        @classmethod
        def download(cls, urls, **kwargs):
            cls.calls += 1
            if cls.calls == 1:
                raise RuntimeError("private detail")
            return [good_path]

    sample = [
        {
            "granule_id": "bad.h5",
            "download_url": "https://example.test/bad.h5",
            "beginning_datetime": "2020-01-02T00:00:00Z",
        },
        {
            "granule_id": "good.h5",
            "download_url": "https://example.test/good.h5",
            "beginning_datetime": "2020-01-01T00:00:00Z",
        },
    ]
    report = {
        "sources": {
            "gedi_l2a": {
                "short_name": "GEDI02_A",
                "historical": {"candidate_granule_count": 2, "sample": sample},
            }
        }
    }
    geometry_path = tmp_path / "aoi.geojson"
    geometry_path.write_text(
        '{"type":"Polygon","coordinates":[[[-0.001,-0.001],[0.001,-0.001],'
        '[0.001,0.001],[-0.001,0.001],[-0.001,-0.001]]]}',
        encoding="utf-8",
    )

    validation = validate_gedi_footprints(
        report,
        geometry_path,
        authentication=({"status": "authenticated", "strategy": "test"}, Earthaccess),
    )

    assert validation["gedi"]["granules_attempted"] == 2
    assert validation["gedi"]["granules_inspected"] == 1
    assert validation["gedi"]["granules"][0]["message"] == "Earthdata download failed."
    assert "private detail" not in str(validation)
