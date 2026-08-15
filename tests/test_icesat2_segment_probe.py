"""Validacao offline de segmentos ATL08/ATL08QL em HDF5 minimo."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
from shapely.geometry import Polygon

from scripts.probe_nasa_coverage import (
    _observation_key,
    calculate_age_days,
    parse_icesat2_hdf5,
    validate_icesat2_segments,
)


def _write_beam(
    handle: h5py.File,
    beam: str,
    longitudes: list[float],
    latitudes: list[float],
) -> None:
    land = handle.create_group(f"{beam}/land_segments")
    shape = (1, 5)
    longitude_values = np.full(shape, np.nan)
    latitude_values = np.full(shape, np.nan)
    longitude_values.flat[:len(longitudes)] = longitudes
    latitude_values.flat[:len(latitudes)] = latitudes
    land.create_dataset("longitude_20m", data=longitude_values)
    land.create_dataset("latitude_20m", data=latitude_values)
    land.create_dataset("terrain_flg", data=np.array([0], dtype=np.uint8))
    land.create_dataset("cloud_flag_atm", data=np.array([0], dtype=np.int8))
    land.create_dataset("msw_flag", data=np.array([0], dtype=np.int8))
    land.create_dataset("night_flag", data=np.array([1], dtype=np.uint8))
    land.create_dataset("urban_flag", data=np.array([0], dtype=np.uint8))
    canopy = land.create_group("canopy")
    terrain = land.create_group("terrain")
    canopy.create_dataset("h_canopy_20m", data=np.full(shape, 1.5))
    terrain.create_dataset("h_te_best_fit_20m", data=np.full(shape, 710.0))


def _write_granule(path: Path, *, multiple_beams: bool = False) -> None:
    with h5py.File(path, "w") as handle:
        _write_beam(handle, "gt1l", [0.0, 0.0012, 0.01], [0.0, 0.0, 0.0])
        if multiple_beams:
            _write_beam(handle, "gt2r", [0.0005], [0.0005])


def _test_aoi() -> Polygon:
    return Polygon([
        (-0.001, -0.001),
        (0.001, -0.001),
        (0.001, 0.001),
        (-0.001, 0.001),
        (-0.001, -0.001),
    ])


def _write_5_by_n_granule(path: Path) -> None:
    fill_value = -9999.0
    with h5py.File(path, "w") as handle:
        land = handle.create_group("gt1l/land_segments")
        latitude = np.array([
            [0.0, 0.0],
            [0.0, 0.0],
            [0.0, 0.0],
            [fill_value, 0.0],
            [0.0, 0.0],
        ])
        longitude = np.array([
            [0.0, 0.0100],
            [0.0001, 0.0101],
            [0.0002, 0.0102],
            [fill_value, 0.0103],
            [0.0004, 0.0104],
        ])
        latitude_dataset = land.create_dataset("latitude_20m", data=latitude)
        longitude_dataset = land.create_dataset("longitude_20m", data=longitude)
        latitude_dataset.attrs["_FillValue"] = fill_value
        longitude_dataset.attrs["_FillValue"] = fill_value
        # Os centros de 100 m ficam fora da AOI para provar a preferencia por 20 m.
        land.create_dataset("latitude", data=np.array([0.0, 0.0]))
        land.create_dataset("longitude", data=np.array([0.02, 0.03]))
        land.create_dataset("terrain_flg", data=np.array([0, 0], dtype=np.uint8))
        land.create_dataset("cloud_flag_atm", data=np.array([0, 0], dtype=np.int8))
        land.create_dataset("msw_flag", data=np.array([0, 0], dtype=np.int8))
        canopy = land.create_group("canopy")
        terrain = land.create_group("terrain")
        canopy.create_dataset("h_canopy_20m", data=np.array([
            [1.0, 11.0],
            [2.0, 12.0],
            [3.0, 13.0],
            [4.0, 14.0],
            [5.0, 15.0],
        ]))
        terrain.create_dataset("h_te_best_fit_20m", data=np.array([
            [101.0, 201.0],
            [102.0, 202.0],
            [103.0, 203.0],
            [104.0, 204.0],
            [105.0, 205.0],
        ]))
        canopy.create_dataset("h_canopy", data=np.array([99.0, 99.0]))
        terrain.create_dataset("h_te_best_fit", data=np.array([999.0, 999.0]))


def _write_100m_only_granule(path: Path) -> None:
    with h5py.File(path, "w") as handle:
        land = handle.create_group("gt1l/land_segments")
        land.create_dataset("latitude", data=np.array([0.0]))
        land.create_dataset("longitude", data=np.array([0.0]))
        land.create_dataset("terrain_flg", data=np.array([0], dtype=np.uint8))
        canopy = land.create_group("canopy")
        terrain = land.create_group("terrain")
        canopy.create_dataset("h_canopy", data=np.array([1.5]))
        terrain.create_dataset("h_te_best_fit", data=np.array([710.0]))


def test_parses_atl08_multiple_beams_and_spatial_relations(tmp_path: Path) -> None:
    path = tmp_path / "ATL08_20260801010203_01230102_007_01.h5"
    _write_granule(path, multiple_beams=True)

    parsed = parse_icesat2_hdf5(
        path,
        geometry=_test_aoi(),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        near_distance_meters=50,
    )

    assert parsed["product"] == "ATL08"
    assert parsed["preliminary"] is False
    assert set(parsed["schema"]["beams"]) == {"gt1l", "gt2r"}
    assert parsed["segments_total"] == 4
    assert parsed["segments_inside_aoi"] == 2
    assert parsed["segments_near_aoi"] == 1
    assert parsed["segments_outside_relevance"] == 1
    assert parsed["usable_segments"] == 3
    assert {item["spatial_relation"] for item in parsed["relevant_segments"]} == {
        "inside_aoi",
        "near_aoi",
    }
    assert all(item["spatial_support_m"] == 20 for item in parsed["relevant_segments"])


def test_parses_atl08ql_as_preliminary_and_calculates_age(tmp_path: Path) -> None:
    path = tmp_path / "ATL08QL_20260810010203_01230102_007_01.h5"
    _write_granule(path)

    parsed = parse_icesat2_hdf5(
        path,
        geometry=_test_aoi(),
        observed_at=datetime(2026, 8, 10, 23, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 14, 1, tzinfo=timezone.utc),
    )

    assert parsed["product"] == "ATL08QL"
    assert parsed["product_role"] == "expedited_preliminary"
    assert parsed["preliminary"] is True
    assert parsed["relevant_segments"][0]["age_days"] == 4


def test_5_by_n_20m_arrays_remain_aligned_and_fill_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "ATL08_20260801010203_01230102_007_01.h5"
    _write_5_by_n_granule(path)

    parsed = parse_icesat2_hdf5(
        path,
        geometry=_test_aoi(),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        near_distance_meters=50,
    )

    schema = parsed["schema"]["beams"]["gt1l"]
    assert schema["spatial_support_m"] == 20
    assert schema["fallback_reason"] is None
    assert schema["datasets_used"][:4] == [
        "latitude_20m",
        "longitude_20m",
        "terrain/h_te_best_fit_20m",
        "canopy/h_canopy_20m",
    ]
    assert parsed["segments_total"] == 9
    assert parsed["segments_inside_aoi"] == 4
    assert parsed["segments_outside_relevance"] == 5
    assert [
        (
            item["longitude"],
            item["vegetation_height_m"],
            item["terrain_height_m"],
        )
        for item in parsed["relevant_segments"]
    ] == [
        (0.0, 1.0, 101.0),
        (0.0001, 2.0, 102.0),
        (0.0002, 3.0, 103.0),
        (0.0004, 5.0, 105.0),
    ]


def test_falls_back_to_100m_only_when_20m_datasets_are_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ATL08_20260801010203_01230102_007_01.h5"
    _write_100m_only_granule(path)

    parsed = parse_icesat2_hdf5(
        path,
        geometry=_test_aoi(),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )

    schema = parsed["schema"]["beams"]["gt1l"]
    assert schema["spatial_support_m"] == 100
    assert schema["fallback_reason"] == "20m_datasets_unavailable"
    assert parsed["relevant_segments"][0]["spatial_support_m"] == 100
    assert parsed["relevant_segments"][0]["fallback_reason"] == (
        "20m_datasets_unavailable"
    )


def test_final_and_quick_look_share_observation_key_without_product_confusion() -> None:
    final = "ATL08_20260810010203_01230102_007_01.h5"
    quick_look = "ATL08QL_20260810010203_01230102_007_01.h5"

    assert _observation_key(final) == _observation_key(quick_look)
    assert _observation_key(final) is not None


def test_age_days_never_uses_fractional_days_or_negative_values() -> None:
    generated = datetime(2026, 8, 14, 1, tzinfo=timezone.utc)

    assert calculate_age_days(datetime(2026, 8, 10, 23, tzinfo=timezone.utc), generated) == 4
    assert calculate_age_days(datetime(2026, 8, 15, tzinfo=timezone.utc), generated) == 0


def test_missing_authentication_preserves_discovery() -> None:
    report = {
        "sources": {
            "icesat2_atl08": {
                "short_name": "ATL08",
                "historical": {"candidate_granule_count": 18, "sample": []},
            },
            "icesat2_atl08ql": {
                "short_name": "ATL08QL",
                "diagnostic_recent_period": {
                    "candidate_granule_count": 2,
                    "sample": [],
                },
            },
        }
    }

    validation = validate_icesat2_segments(
        report,
        "unused.geojson",
        authentication=({"status": "auth_required", "strategy": None}, None),
    )

    assert validation["authentication"]["status"] == "auth_required"
    assert validation["atl08"]["candidate_granules"] == 18
    assert validation["atl08"]["decision"] == "AUTH_REQUIRED"
    assert validation["atl08ql"]["candidate_granules"] == 2
    assert validation["atl08ql"]["decision"] == "AUTH_REQUIRED"
