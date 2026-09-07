"""Testes offline da calibracao radiometrica Sentinel-1 Level-1."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from src.satellite_monitoring.multisource import CollectionPeriod, EvidenceStatus
from src.satellite_monitoring.multisource.providers import sentinel1 as sentinel1_provider
from src.satellite_monitoring.multisource.providers import (
    sentinel1_calibration as calibration_module,
)
from src.satellite_monitoring.multisource.providers.sentinel1 import (
    Sentinel1Provider,
    read_sentinel1_window,
)
from src.satellite_monitoring.multisource.providers.sentinel1_calibration import (
    Sentinel1CalibrationError,
    Sentinel1CalibrationUnavailable,
    calculate_sigma0_metrics,
    calibrate_sigma0,
    interpolate_sigma_nought_lut,
    load_calibration_lut,
    parse_calibration_lut,
    sigma0_linear_to_db,
)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [-47.0, -23.02],
            [-46.98, -23.02],
            [-46.98, -23.0],
            [-47.0, -23.0],
            [-47.0, -23.02],
        ]
    ],
}
PERIOD = CollectionPeriod(date(2026, 8, 1), date(2026, 8, 31))


def _calibration_xml(
    *,
    polarization: str = "VV",
    lines: tuple[int, ...] = (0, 2),
    pixels: tuple[int, ...] = (0, 2),
    sigma_by_line: tuple[tuple[float, ...], ...] = ((40.0, 60.0), (60.0, 80.0)),
) -> bytes:
    vectors = "".join(
        "<calibrationVector>"
        f"<azimuthTime>2026-08-01T00:00:{index:02d}</azimuthTime>"
        f"<line>{line}</line>"
        f'<pixel count="{len(pixels)}">{" ".join(map(str, pixels))}</pixel>'
        f'<sigmaNought count="{len(sigma)}">{" ".join(map(str, sigma))}</sigmaNought>'
        "</calibrationVector>"
        for index, (line, sigma) in enumerate(zip(lines, sigma_by_line, strict=True))
    )
    return (
        "<calibration><adsHeader><productType>GRD</productType>"
        f"<polarisation>{polarization}</polarisation></adsHeader>"
        f'<calibrationVectorList count="{len(lines)}">'
        f"{vectors}</calibrationVectorList></calibration>"
    ).encode()


def _constant_lut(value: float = 50.0, *, polarization: str = "VV"):
    return parse_calibration_lut(
        _calibration_xml(
            polarization=polarization,
            lines=(0, 1),
            pixels=(0, 1),
            sigma_by_line=((value, value), (value, value)),
        )
    )


def test_known_dn_and_lut_produce_expected_sigma0_and_db() -> None:
    sigma0 = calibrate_sigma0(np.ma.array([[100.0]]), _constant_lut(50.0))
    sigma0_db = sigma0_linear_to_db(sigma0)

    assert sigma0[0, 0] == pytest.approx(4.0)
    assert sigma0_db[0, 0] == pytest.approx(10.0 * np.log10(4.0))


def test_interpolation_is_exact_at_lut_point() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    value = interpolate_sigma_nought_lut(
        lut, np.array([[2.0]]), np.array([[2.0]])
    )

    assert value[0, 0] == pytest.approx(80.0)


def test_interpolation_between_range_pixels() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    value = interpolate_sigma_nought_lut(
        lut, np.array([[0.0]]), np.array([[1.0]])
    )

    assert value[0, 0] == pytest.approx(50.0)


def test_interpolation_between_calibration_vector_lines() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    value = interpolate_sigma_nought_lut(
        lut, np.array([[1.0]]), np.array([[0.0]])
    )

    assert value[0, 0] == pytest.approx(50.0)


def test_bilinear_interpolation_uses_range_and_azimuth() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    value = interpolate_sigma_nought_lut(
        lut, np.array([[1.0]]), np.array([[1.0]])
    )

    assert value[0, 0] == pytest.approx(60.0)


def test_native_window_column_offset_selects_correct_lut_sample() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    sigma0 = calibrate_sigma0(
        np.ma.array([[100.0]]), lut, line_offset=0, sample_offset=1
    )

    assert sigma0[0, 0] == pytest.approx(4.0)


def test_native_window_row_offset_selects_correct_lut_line() -> None:
    lut = parse_calibration_lut(_calibration_xml())

    sigma0 = calibrate_sigma0(
        np.ma.array([[100.0]]), lut, line_offset=1, sample_offset=0
    )

    assert sigma0[0, 0] == pytest.approx(4.0)


def test_vv_vh_metrics_use_only_covalid_pixels() -> None:
    vv = np.ma.array([[4.0, 4.0], [4.0, 4.0]], mask=False)
    vh = np.ma.array([[1.0, 16.0], [1.0, 1.0]], mask=[[False, True], [False, False]])

    metrics = calculate_sigma0_metrics(
        vv,
        vh,
        inside_aoi=np.ones((2, 2), dtype=bool),
    )

    assert metrics.vv_sigma0_median_linear == 4.0
    assert metrics.vh_sigma0_median_linear == 1.0
    assert metrics.vv_sigma0_median_db == pytest.approx(10.0 * np.log10(4.0))
    assert metrics.vh_sigma0_median_db == 0.0
    assert metrics.vh_vv_sigma0_ratio_median == pytest.approx(0.25)
    assert metrics.vh_minus_vv_db_median == pytest.approx(-10.0 * np.log10(4.0))


def test_nodata_nan_and_zero_are_masked_before_db() -> None:
    dn = np.ma.array(
        [[100.0, -9999.0], [np.nan, 0.0]],
        mask=[[False, True], [False, False]],
    )

    sigma0 = calibrate_sigma0(dn, _constant_lut())
    sigma0_db = sigma0_linear_to_db(sigma0)

    assert sigma0.count() == 1
    assert sigma0_db.count() == 1
    assert sigma0[0, 0] == pytest.approx(4.0)
    assert np.all(np.isfinite(sigma0.compressed()))
    assert np.all(np.isfinite(sigma0_db.compressed()))


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (b"<calibration>", "invalid_calibration_xml"),
        (b"<calibration/>", "missing_adsHeader"),
        (
            b"<calibration><adsHeader><productType>GRD</productType>"
            b"<polarisation>VV</polarisation></adsHeader>"
            b'<calibrationVectorList count="1">'
            b"<calibrationVector><line>0</line>"
            b'<pixel count="2">0 1</pixel></calibrationVector>'
            b"</calibrationVectorList></calibration>",
            "invalid_calibrationVectorList_count",
        ),
        (
            b"<calibration><adsHeader><productType>GRD</productType>"
            b"<polarisation>VV</polarisation></adsHeader>"
            b'<calibrationVectorList count="2">'
            b"<calibrationVector><line>0</line>"
            b'<pixel count="2">0 1</pixel></calibrationVector>'
            b"<calibrationVector><line>1</line>"
            b'<pixel count="2">0 1</pixel>'
            b'<sigmaNought count="2">50 50</sigmaNought></calibrationVector>'
            b"</calibrationVectorList></calibration>",
            "missing_sigmaNought",
        ),
    ],
)
def test_invalid_or_incomplete_calibration_metadata_is_rejected(
    document: bytes, reason: str
) -> None:
    with pytest.raises(Sentinel1CalibrationError, match=reason):
        parse_calibration_lut(document)


def _asset(href: str) -> SimpleNamespace:
    return SimpleNamespace(href=href, extra_fields={})


def _item(vv_href: str, vh_href: str | None = None) -> SimpleNamespace:
    assets = {"vv": _asset(vv_href)}
    if vh_href is not None:
        assets["vh"] = _asset(vh_href)
    return SimpleNamespace(
        id="S1_TEST",
        datetime=datetime(2026, 8, 20, tzinfo=timezone.utc),
        properties={
            "datetime": "2026-08-20T00:00:00Z",
            "platform": "sentinel-1a",
            "sar:instrument_mode": "IW",
            "sar:polarizations": ["VV", "VH"] if vh_href else ["VV"],
            "sat:orbit_state": "descending",
            "sat:relative_orbit": 53,
        },
        assets=assets,
    )


def _profile() -> dict[str, object]:
    return {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": from_origin(-47.0, -23.0, 0.01, 0.01),
        "nodata": -9999.0,
    }


def test_window_reader_calibrates_vv_and_vh_on_native_grid(monkeypatch) -> None:
    monkeypatch.setattr(
        sentinel1_provider,
        "load_calibration_lut",
        lambda href: _constant_lut(
            polarization="VH" if href.endswith("cal-vh") else "VV"
        ),
    )
    with MemoryFile() as vv_memory, MemoryFile() as vh_memory:
        with vv_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 100.0, dtype=np.float32), 1)
        with vh_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 50.0, dtype=np.float32), 1)
        item = _item(vv_memory.name, vh_memory.name)
        item.assets["schema-calibration-vv"] = _asset("memory://cal-vv")
        item.assets["schema-calibration-vh"] = _asset("memory://cal-vh")

        metrics = read_sentinel1_window(item, GEOMETRY)

    assert metrics.vv_amplitude_median == 100.0
    assert metrics.vh_amplitude_median == 50.0
    assert metrics.radiometric_calibration_status == "calibrated"
    assert metrics.vv_radiometric_calibration_status == "calibrated"
    assert metrics.vh_radiometric_calibration_status == "calibrated"
    assert metrics.vv_sigma0_median_linear == pytest.approx(4.0)
    assert metrics.vh_sigma0_median_linear == pytest.approx(1.0)
    assert metrics.vh_vv_sigma0_ratio_median == pytest.approx(0.25)


def test_missing_lut_preserves_raw_amplitude_with_unavailable_status() -> None:
    with MemoryFile() as vv_memory:
        with vv_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 100.0, dtype=np.float32), 1)

        metrics = read_sentinel1_window(_item(vv_memory.name), GEOMETRY)

    assert metrics.vv_amplitude_median == 100.0
    assert metrics.radiometric_calibration_status == "unavailable"
    assert metrics.vv_sigma0_median_linear is None
    assert "missing_vv_calibration_asset" in metrics._radiometric_calibration_warning


def test_calibration_failure_does_not_drop_provider_observation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sentinel1_provider,
        "load_calibration_lut",
        lambda _: (_ for _ in ()).throw(
            Sentinel1CalibrationError("invalid_calibration_xml")
        ),
    )
    with MemoryFile() as vv_memory:
        with vv_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 100.0, dtype=np.float32), 1)
        item = _item(vv_memory.name)
        item.assets["schema-calibration-vv"] = _asset("memory://invalid-calibration")

        class Search:
            def item_collection(self):
                return [item]

        class Client:
            def search(self, **_):
                return Search()

        provider = Sentinel1Provider(
            endpoint="https://example.test",
            client_factory=lambda _: Client(),
        )
        evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert len(evidence.observations) == 1
    assert evidence.observations[0].metrics["vv_amplitude_median"] == 100.0
    assert evidence.observations[0].metrics["radiometric_calibration_status"] == "failed"
    assert evidence.metrics["calibrated_observation_count"] == 0
    assert evidence.metrics["uncalibrated_observation_count"] == 1
    assert evidence.metrics["calibration_failure_count"] == 1
    assert evidence.metrics["failure_counts"] == {"calibration_failed": 1}
    assert any("raw GRD amplitude metrics were preserved" in warning for warning in evidence.warnings)


def test_calibration_download_retries_transient_failures_only(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _):
            return _calibration_xml()

    def urlopen(*_, **__):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise URLError("temporary")
        return Response()

    load_calibration_lut.cache_clear()
    monkeypatch.setattr(calibration_module, "urlopen", urlopen)
    monkeypatch.setattr(calibration_module.time, "sleep", delays.append)

    lut = load_calibration_lut("https://example.test/transient.xml")

    assert lut.polarization == "VV"
    assert calls == 3
    assert delays == [0.25, 0.5]
    load_calibration_lut.cache_clear()


def test_calibration_download_does_not_retry_permanent_http_error(monkeypatch) -> None:
    calls = 0

    def urlopen(*_, **__):
        nonlocal calls
        calls += 1
        raise HTTPError("https://example.test/missing.xml", 404, "missing", {}, None)

    load_calibration_lut.cache_clear()
    monkeypatch.setattr(calibration_module, "urlopen", urlopen)
    monkeypatch.setattr(
        calibration_module.time,
        "sleep",
        lambda _: pytest.fail("HTTP 404 must not retry"),
    )

    with pytest.raises(Sentinel1CalibrationUnavailable):
        load_calibration_lut("https://example.test/missing.xml")

    assert calls == 1
    load_calibration_lut.cache_clear()


def test_measurement_assets_are_opened_once_per_scene(monkeypatch) -> None:
    monkeypatch.setattr(
        sentinel1_provider,
        "load_calibration_lut",
        lambda href: _constant_lut(
            polarization="VH" if href.endswith("cal-vh") else "VV"
        ),
    )
    original_open = sentinel1_provider.rasterio.open
    opened: list[str] = []

    def counting_open(path, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(sentinel1_provider.rasterio, "open", counting_open)
    with MemoryFile() as vv_memory, MemoryFile() as vh_memory:
        with vv_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 100.0, dtype=np.float32), 1)
        with vh_memory.open(**_profile()) as target:
            target.write(np.full((2, 2), 50.0, dtype=np.float32), 1)
        item = _item(vv_memory.name, vh_memory.name)
        item.assets["schema-calibration-vv"] = _asset("memory://cal-vv")
        item.assets["schema-calibration-vh"] = _asset("memory://cal-vh")

        metrics = read_sentinel1_window(item, GEOMETRY)

    assert metrics.radiometric_calibration_status == "calibrated"
    assert opened.count(vv_memory.name) == 1
    assert opened.count(vh_memory.name) == 1
