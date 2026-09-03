"""Testes offline do provider Sentinel-1 operacional em shadow mode."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.multisource import (
    CollectionPeriod,
    EvidenceStatus,
    MultisourceOrchestrator,
)
from src.satellite_monitoring.multisource.providers.sentinel1 import (
    Sentinel1Provider,
    Sentinel1RasterMetrics,
    calculate_amplitude_metrics,
    read_sentinel1_window,
)
from src.satellite_monitoring.multisource.runtime import collect_multisource_evidence


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[-47.0, -23.02], [-46.98, -23.02], [-46.98, -23.0], [-47.0, -23.0], [-47.0, -23.02]]
    ],
}
PERIOD = CollectionPeriod(date(2026, 8, 1), date(2026, 8, 31))


def _asset(href: str = "memory://asset") -> SimpleNamespace:
    return SimpleNamespace(href=href, extra_fields={})


def _item(
    item_id: str,
    day: int,
    *,
    vh: bool = True,
    orbit_state: str = "ascending",
) -> SimpleNamespace:
    assets = {"vv": _asset()}
    if vh:
        assets["vh"] = _asset()
    return SimpleNamespace(
        id=item_id,
        datetime=datetime(2026, 8, day, tzinfo=timezone.utc),
        properties={
            "datetime": f"2026-08-{day:02d}T00:00:00Z",
            "platform": "sentinel-1a",
            "sar:instrument_mode": "IW",
            "sar:polarizations": ["VV", "VH"] if vh else ["VV"],
            "sat:orbit_state": orbit_state,
            "sat:relative_orbit": 42,
        },
        assets=assets,
    )


def _raster(
    *,
    coverage: float = 96.0,
    ratio: float | None = 0.25,
) -> Sentinel1RasterMetrics:
    return Sentinel1RasterMetrics(
        valid_pixel_count=96,
        total_pixel_count=100,
        valid_pixel_percentage=96.0,
        coverage=coverage,
        vv_amplitude_median=100.0,
        vv_amplitude_mean=101.0,
        vv_amplitude_std=3.0,
        vh_amplitude_median=25.0 if ratio is not None else None,
        vh_amplitude_mean=26.0 if ratio is not None else None,
        vh_amplitude_std=2.0 if ratio is not None else None,
        vh_vv_amplitude_ratio=ratio,
    )


class _Search:
    def __init__(self, items):
        self._items = items

    def item_collection(self):
        return list(self._items)


class _Client:
    def __init__(self, items):
        self.items = items
        self.arguments = None

    def search(self, **kwargs):
        self.arguments = kwargs
        return _Search(self.items)


def _provider(items, *, reader=lambda *_: _raster()):
    client = _Client(items)
    provider = Sentinel1Provider(
        endpoint="https://planetarycomputer.microsoft.com/api/stac/v1",
        max_scenes=8,
        client_factory=lambda _: client,
        raster_reader=reader,
    )
    return provider, client


def test_disabled_multisource_does_not_construct_provider() -> None:
    config = MonitoringConfig(
        geometry=GEOMETRY,
        start_date=PERIOD.start_date,
        end_date=PERIOD.end_date,
    )

    result = collect_multisource_evidence(
        config,
        GEOMETRY,
        provider_factory=lambda **_: pytest.fail("provider must stay disabled"),
    )

    assert result is None


def test_disabled_sentinel1_keeps_shadow_sources_empty() -> None:
    config = MonitoringConfig(
        geometry=GEOMETRY,
        start_date=PERIOD.start_date,
        end_date=PERIOD.end_date,
        multisource_enabled=True,
        sentinel1_enabled=False,
        multisource_fusion_mode="shadow",
    )

    result = collect_multisource_evidence(
        config,
        GEOMETRY,
        provider_factory=lambda **_: pytest.fail("provider must stay disabled"),
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result["sources"] == []


def test_search_uses_requested_aoi_period_collection_and_iw_mode() -> None:
    provider, client = _provider([_item("scene", 20)])

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert client.arguments == {
        "collections": ["sentinel-1-grd"],
        "intersects": GEOMETRY,
        "datetime": "2026-08-01/2026-08-31",
        "query": {"sar:instrument_mode": {"eq": "IW"}},
    }


def test_zero_scenes_returns_no_coverage() -> None:
    provider, _ = _provider([])

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.NO_COVERAGE
    assert evidence.coverage == 0.0
    assert evidence.observations == ()


def test_dual_polarization_metrics_quality_and_orbit_provenance() -> None:
    provider, _ = _provider(
        [_item("older", 10, orbit_state="ascending"), _item("newer", 20, orbit_state="descending")]
    )

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert len(evidence.observations) == 2
    assert evidence.coverage == 96.0
    assert evidence.quality == pytest.approx(95.066667)
    assert evidence.metrics["observation_count"] == 2
    assert evidence.metrics["dual_polarization_observation_count"] == 2
    assert evidence.metrics["orbit_states"] == "ascending,descending"
    assert evidence.observations[0].metrics["relative_orbit"] == 42
    assert evidence.observations[1].metrics["vh_vv_amplitude_ratio"] == 0.25
    assert any("Multiple orbit states" in warning for warning in evidence.warnings)
    assert evidence.provenance["radiometric_conversion"] == "none"


def test_max_scenes_prefers_most_recent_compatible_observation() -> None:
    read_ids: list[str] = []

    def reader(item, _geometry):
        read_ids.append(item.id)
        return _raster()

    client = _Client([_item("older", 10), _item("newer", 20)])
    provider = Sentinel1Provider(
        endpoint="https://example.test",
        max_scenes=1,
        client_factory=lambda _: client,
        raster_reader=reader,
    )

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert read_ids == ["newer"]
    assert evidence.metrics["observation_count"] == 1


def test_missing_vh_remains_available_with_reduced_quality() -> None:
    provider, _ = _provider(
        [_item("vv-only", 20, vh=False)],
        reader=lambda *_: _raster(ratio=None),
    )

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert evidence.observations[0].metrics["vh_amplitude_median"] is None
    assert evidence.metrics["dual_polarization_observation_count"] == 0
    assert evidence.quality < 75
    assert any("VH polarization unavailable" in warning for warning in evidence.warnings)


def test_nodata_nonfinite_values_and_zero_ratio_are_safe() -> None:
    inside = np.ones((2, 2), dtype=bool)
    vv = np.ma.array([[0.0, np.nan], [0.0, -9999.0]], mask=[[False, False], [False, True]])
    vh = np.ma.array([[2.0, 3.0], [4.0, 5.0]], mask=False)

    metrics = calculate_amplitude_metrics(
        vv,
        vh,
        inside_aoi=inside,
        total_pixel_count=4,
        coverage=100.0,
    )

    assert metrics.valid_pixel_count == 2
    assert metrics.valid_pixel_percentage == 50.0
    assert metrics.vv_amplitude_median == 0.0
    assert metrics.vh_vv_amplitude_ratio is None
    assert all(
        np.isfinite(value)
        for value in (
            metrics.vv_amplitude_median,
            metrics.vv_amplitude_mean,
            metrics.vv_amplitude_std,
        )
    )


def test_window_reader_uses_aoi_mask_and_remote_raster_nodata_semantics() -> None:
    transform = from_origin(-47.0, -23.0, 0.01, 0.01)
    profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": -9999.0,
    }
    with MemoryFile() as vv_memory, MemoryFile() as vh_memory:
        with vv_memory.open(**profile) as target:
            target.write(np.array([[10.0, -9999.0], [np.nan, 30.0]], dtype=np.float32), 1)
        with vh_memory.open(**profile) as target:
            target.write(np.array([[2.0, 4.0], [6.0, 8.0]], dtype=np.float32), 1)
        item = _item("local", 20)
        item.assets["vv"] = _asset(vv_memory.name)
        item.assets["vh"] = _asset(vh_memory.name)

        metrics = read_sentinel1_window(item, GEOMETRY)

    assert metrics.total_pixel_count == 4
    assert metrics.valid_pixel_count == 2
    assert metrics.coverage == 100.0
    assert metrics.vv_amplitude_median == 20.0
    assert metrics.vh_vv_amplitude_ratio == pytest.approx(0.25)


def test_raster_failure_is_fail_soft_for_the_source() -> None:
    def fail(*_):
        raise RuntimeError("raster failure")

    provider, _ = _provider([_item("broken", 20)], reader=fail)

    evidence = provider.collect_evidence(GEOMETRY, PERIOD)

    assert evidence.status is EvidenceStatus.UNAVAILABLE
    assert "raster failure" not in repr(evidence)
    assert "RuntimeError" in evidence.warnings[0]


def test_stac_failure_isolated_by_orchestrator() -> None:
    provider = Sentinel1Provider(
        endpoint="https://example.test",
        client_factory=lambda _: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )

    evidence = MultisourceOrchestrator([provider]).collect(GEOMETRY, PERIOD)[0]

    assert evidence.status is EvidenceStatus.ERROR
    assert evidence.provenance["error_type"] == "RuntimeError"
    assert "secret detail" not in repr(evidence)


def test_runtime_shadow_payload_is_deterministic_with_injected_clock() -> None:
    provider, _ = _provider([_item("scene", 20)])
    config = MonitoringConfig(
        geometry=GEOMETRY,
        start_date=PERIOD.start_date,
        end_date=PERIOD.end_date,
        multisource_enabled=True,
        sentinel1_enabled=True,
        multisource_fusion_mode="shadow",
    )

    result = collect_multisource_evidence(
        config,
        GEOMETRY,
        provider_factory=lambda **_: provider,
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result["fusion_mode"] == "shadow"
    assert result["official_recommendation_changed"] is False
    assert result["generated_at"] == "2026-09-01T00:00:00+00:00"
    assert result["sources"][0]["status"] == "available"
