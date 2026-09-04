"""Provider Sentinel-1 GRD complementar, sem inferencia fisica de backscatter."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom

from ...stac_client import open_stac_client
from ..models import (
    CollectionPeriod,
    EvidenceObservation,
    EvidenceStatus,
    SourceEvidence,
)


class Sentinel1RasterError(RuntimeError):
    """Indica que uma observacao Sentinel-1 nao pode ser recortada com seguranca."""


@dataclass(frozen=True)
class Sentinel1RasterMetrics:
    """Metricas dos valores detectados preservados nos assets GRD."""

    valid_pixel_count: int
    total_pixel_count: int
    valid_pixel_percentage: float
    coverage: float
    vv_amplitude_median: float
    vv_amplitude_mean: float
    vv_amplitude_std: float
    vh_amplitude_median: float | None = None
    vh_amplitude_mean: float | None = None
    vh_amplitude_std: float | None = None
    vh_vv_amplitude_ratio: float | None = None


def _finite_values(array: np.ma.MaskedArray, inside_aoi: np.ndarray) -> np.ndarray:
    values = np.asarray(array.data, dtype=np.float64)
    valid = inside_aoi & ~np.ma.getmaskarray(array) & np.isfinite(values)
    return values[valid]


def calculate_amplitude_metrics(
    vv: np.ma.MaskedArray,
    vh: np.ma.MaskedArray | None,
    *,
    inside_aoi: np.ndarray,
    total_pixel_count: int,
    coverage: float,
) -> Sentinel1RasterMetrics:
    """Resume amplitude GRD sem aplicar logaritmo ou calibracao radiometrica."""
    vv_values = _finite_values(vv, inside_aoi)
    if vv_values.size == 0:
        raise Sentinel1RasterError("A observacao nao possui pixels VV validos na AOI.")

    vh_values: np.ndarray | None = None
    if vh is not None:
        vh_values = _finite_values(vh, inside_aoi)
        if vh_values.size == 0:
            vh_values = None

    if vh_values is not None:
        vv_mask = ~np.ma.getmaskarray(vv) & np.isfinite(np.asarray(vv.data))
        vh_mask = ~np.ma.getmaskarray(vh) & np.isfinite(np.asarray(vh.data))
        valid_pixel_count = int(np.count_nonzero(inside_aoi & vv_mask & vh_mask))
    else:
        valid_pixel_count = int(vv_values.size)

    vv_median = float(np.median(vv_values))
    vh_median = float(np.median(vh_values)) if vh_values is not None else None
    ratio = None
    if vh_median is not None and np.isfinite(vv_median) and vv_median != 0:
        candidate = vh_median / vv_median
        if np.isfinite(candidate):
            ratio = float(candidate)

    valid_percentage = (
        min(100.0, valid_pixel_count / total_pixel_count * 100.0)
        if total_pixel_count > 0
        else 0.0
    )
    return Sentinel1RasterMetrics(
        valid_pixel_count=valid_pixel_count,
        total_pixel_count=total_pixel_count,
        valid_pixel_percentage=valid_percentage,
        coverage=float(np.clip(coverage, 0.0, 100.0)),
        vv_amplitude_median=vv_median,
        vv_amplitude_mean=float(np.mean(vv_values)),
        vv_amplitude_std=float(np.std(vv_values)),
        vh_amplitude_median=vh_median,
        vh_amplitude_mean=(float(np.mean(vh_values)) if vh_values is not None else None),
        vh_amplitude_std=(float(np.std(vh_values)) if vh_values is not None else None),
        vh_vv_amplitude_ratio=ratio,
    )


def _asset_key(item: Any, polarization: str) -> str | None:
    expected = polarization.lower()
    for key, asset in item.assets.items():
        if key.lower() == expected:
            return key
        bands = asset.extra_fields.get("sar:bands", [])
        if isinstance(bands, Mapping):
            bands = [bands]
        if any(str(band.get("polarization", "")).lower() == expected for band in bands):
            return key
    return None


def _georeferenced_view(stack: ExitStack, dataset: Any) -> Any:
    if dataset.crs is not None:
        return dataset
    gcps, gcp_crs = dataset.gcps
    if not gcps or gcp_crs is None:
        raise Sentinel1RasterError(
            "Asset Sentinel-1 sem CRS ou pontos de controle georreferenciados."
        )
    return stack.enter_context(WarpedVRT(dataset, crs=gcp_crs))


def _aligned_view(stack: ExitStack, href: str, reference: Any) -> Any:
    source = stack.enter_context(rasterio.open(href))
    source_view = _georeferenced_view(stack, source)
    if (
        source_view.crs == reference.crs
        and source_view.transform == reference.transform
        and source_view.width == reference.width
        and source_view.height == reference.height
    ):
        return source_view
    return stack.enter_context(
        WarpedVRT(
            source_view,
            crs=reference.crs,
            transform=reference.transform,
            width=reference.width,
            height=reference.height,
            resampling=Resampling.bilinear,
        )
    )


def read_sentinel1_window(
    item: Any,
    geometry: Mapping[str, object],
) -> Sentinel1RasterMetrics:
    """Le somente a janela VV/VH da AOI; suporta COGs GRD referenciados por GCP."""
    vv_key = _asset_key(item, "VV")
    vh_key = _asset_key(item, "VH")
    if vv_key is None:
        raise Sentinel1RasterError("A observacao Sentinel-1 nao possui asset VV.")

    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.TIF,.TIFF",
    }
    try:
        with rasterio.Env(**env_options), ExitStack() as stack:
            vv_source = stack.enter_context(rasterio.open(item.assets[vv_key].href))
            reference = _georeferenced_view(stack, vv_source)
            aoi = transform_geom(
                "EPSG:4326", reference.crs, dict(geometry), precision=15
            )
            full_window = geometry_window(reference, [aoi], boundless=True)
            full_shape = (int(full_window.height), int(full_window.width))
            full_mask = geometry_mask(
                [aoi],
                out_shape=full_shape,
                transform=reference.window_transform(full_window),
                invert=True,
            )
            total_pixel_count = int(np.count_nonzero(full_mask))
            if total_pixel_count == 0:
                raise Sentinel1RasterError("A AOI nao contem pixels na grade Sentinel-1.")

            window = geometry_window(reference, [aoi])
            output_transform = reference.window_transform(window)
            inside_aoi = geometry_mask(
                [aoi],
                out_shape=(int(window.height), int(window.width)),
                transform=output_transform,
                invert=True,
            )
            covered_count = int(np.count_nonzero(inside_aoi))
            coverage = min(100.0, covered_count / total_pixel_count * 100.0)
            vv = reference.read(1, window=window, masked=True)
            vh = None
            if vh_key is not None:
                vh_view = _aligned_view(stack, item.assets[vh_key].href, reference)
                vh = vh_view.read(1, window=window, masked=True)
            return calculate_amplitude_metrics(
                vv,
                vh,
                inside_aoi=inside_aoi,
                total_pixel_count=total_pixel_count,
                coverage=coverage,
            )
    except (WindowError, ValueError, rasterio.errors.RasterioError) as exc:
        raise Sentinel1RasterError(f"Falha ao recortar assets Sentinel-1: {exc}") from exc


def _item_datetime(item: Any) -> datetime:
    observed = item.datetime
    if observed is None:
        raw = item.properties.get("datetime") or item.properties.get("start_datetime")
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed


def _polarizations(item: Any) -> tuple[str, ...]:
    values = item.properties.get("sar:polarizations") or []
    return tuple(sorted({str(value).upper() for value in values}))


def _quality(
    observations: list[EvidenceObservation],
) -> tuple[float, dict[str, float]]:
    count = len(observations)
    dual_fraction = sum(
        "VH" in str(observation.metrics.get("polarizations", ""))
        for observation in observations
    ) / count
    valid_mean = float(
        np.mean([float(item.metrics["valid_pixel_percentage"]) for item in observations])
    )
    coverage_mean = float(
        np.mean([float(item.metrics["coverage"]) for item in observations])
    )
    components = {
        "vv_presence": 25.0,
        "vh_presence": 25.0 * dual_fraction,
        "valid_pixels": 25.0 * valid_mean / 100.0,
        "aoi_coverage": 15.0 * coverage_mean / 100.0,
        "observation_support": 10.0 * min(1.0, count / 3.0),
    }
    return float(np.clip(sum(components.values()), 0.0, 100.0)), components


def _normalize_relative_orbit(value: object) -> int | None:
    """Normaliza o inteiro definido pela extensao STAC sat; invalido vira unknown."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, np.integer)):
        candidate = int(value)
    elif isinstance(value, str):
        try:
            candidate = int(value.strip())
        except ValueError:
            return None
    elif isinstance(value, (float, np.floating)) and float(value).is_integer():
        candidate = int(value)
    else:
        return None
    return candidate if candidate > 0 else None


def group_observations_by_relative_orbit(
    observations: Iterable[EvidenceObservation],
) -> dict[int | None, tuple[EvidenceObservation, ...]]:
    """Separa series Sentinel-1; ``None`` representa a trilha auditavel unknown."""
    grouped: dict[int | None, list[EvidenceObservation]] = defaultdict(list)
    for observation in observations:
        orbit = _normalize_relative_orbit(observation.metrics.get("relative_orbit"))
        grouped[orbit].append(observation)
    return {
        orbit: tuple(items)
        for orbit, items in sorted(
            grouped.items(),
            key=lambda entry: (entry[0] is None, entry[0] if entry[0] is not None else 0),
        )
    }


def _observation_values(
    observations: Iterable[EvidenceObservation], name: str
) -> list[float]:
    return [
        float(observation.metrics[name])
        for observation in observations
        if observation.metrics.get(name) is not None
    ]


def _median_observation_metric(
    observations: Iterable[EvidenceObservation], name: str
) -> float | None:
    values = _observation_values(observations, name)
    return float(np.median(values)) if values else None


def _mean_observation_metric(
    observations: Iterable[EvidenceObservation], name: str
) -> float:
    values = _observation_values(observations, name)
    return float(np.mean(values)) if values else 0.0


def _aggregate_observations(
    observations: tuple[EvidenceObservation, ...],
) -> dict[str, Any]:
    """Resume uma unica geometria orbital, sem inferir significado fisico."""
    return {
        "observation_count": len(observations),
        "vv_amplitude_median": _median_observation_metric(
            observations, "vv_amplitude_median"
        ),
        "vh_amplitude_median": _median_observation_metric(
            observations, "vh_amplitude_median"
        ),
        "vh_vv_amplitude_ratio_median": _median_observation_metric(
            observations, "vh_vv_amplitude_ratio"
        ),
        "mean_valid_pixel_percentage": _mean_observation_metric(
            observations, "valid_pixel_percentage"
        ),
        "mean_coverage": _mean_observation_metric(observations, "coverage"),
        "first_observation": min(item.observed_at for item in observations).isoformat(),
        "latest_observation": max(item.observed_at for item in observations).isoformat(),
    }


def select_canonical_orbit_observations(
    observations: Iterable[EvidenceObservation],
) -> tuple[int | None, tuple[EvidenceObservation, ...]]:
    """Seleciona deterministicamente uma serie orbital conhecida para uso temporal.

    Prioriza quantidade, cobertura, pixels validos, recencia e, por fim, o menor
    numero de orbita. Observacoes ``unknown`` nunca recebem uma orbita por inferencia.
    """
    grouped = group_observations_by_relative_orbit(observations)
    known_groups = {
        orbit: items for orbit, items in grouped.items() if orbit is not None
    }
    if not known_groups:
        return None, ()

    def priority(
        entry: tuple[int, tuple[EvidenceObservation, ...]],
    ) -> tuple[int, float, float, datetime, int]:
        orbit, items = entry
        return (
            len(items),
            _mean_observation_metric(items, "coverage"),
            _mean_observation_metric(items, "valid_pixel_percentage"),
            max(item.observed_at for item in items),
            -orbit,
        )

    return max(known_groups.items(), key=priority)


def _temporal_comparability(
    relative_orbits: list[int], unassigned_count: int
) -> str:
    if not relative_orbits:
        return "relative_orbit_unavailable"
    if len(relative_orbits) > 1:
        return "multiple_relative_orbits_requires_grouping"
    if unassigned_count:
        return "unassigned_relative_orbits_require_grouping"
    return "single_relative_orbit_available"


class Sentinel1Provider:
    """Coleta evidencia Sentinel-1 IW VV/VH complementar em modo shadow."""

    source = "sentinel-1"

    def __init__(
        self,
        *,
        endpoint: str,
        collection: str = "sentinel-1-grd",
        max_scenes: int = 8,
        client_factory: Callable[[str], Any] = open_stac_client,
        raster_reader: Callable[[Any, Mapping[str, object]], Sentinel1RasterMetrics] = (
            read_sentinel1_window
        ),
    ) -> None:
        if max_scenes <= 0:
            raise ValueError("Sentinel-1 max_scenes must be positive.")
        self.endpoint = endpoint
        self.collection = collection
        self.max_scenes = max_scenes
        self._client_factory = client_factory
        self._raster_reader = raster_reader

    def collect_evidence(
        self,
        geometry: Mapping[str, object],
        analysis_period: CollectionPeriod,
    ) -> SourceEvidence:
        client = self._client_factory(self.endpoint)
        search = client.search(
            collections=[self.collection],
            intersects=dict(geometry),
            datetime=(
                f"{analysis_period.start_date.isoformat()}/"
                f"{analysis_period.end_date.isoformat()}"
            ),
            query={"sar:instrument_mode": {"eq": "IW"}},
        )
        items = list(search.item_collection())
        iw_items = [
            item
            for item in items
            if str(item.properties.get("sar:instrument_mode", "")).upper() == "IW"
        ]
        compatible = [item for item in iw_items if _asset_key(item, "VV") is not None]
        if not compatible:
            return SourceEvidence(
                source=self.source,
                status=EvidenceStatus.NO_COVERAGE,
                coverage=0.0,
                provenance=self._provenance(),
                warnings=("No compatible Sentinel-1 IW VV observations were found.",),
            )

        prioritized = sorted(
            compatible,
            key=lambda item: (
                _asset_key(item, "VH") is not None,
                _item_datetime(item),
                str(item.id),
            ),
            reverse=True,
        )[: self.max_scenes]
        observations: list[EvidenceObservation] = []
        warnings: list[str] = []
        for item in sorted(prioritized, key=_item_datetime):
            metadata_polarizations = _polarizations(item)
            polarizations = tuple(
                polarization
                for polarization in ("VV", "VH")
                if _asset_key(item, polarization) is not None
            )
            if "VH" not in polarizations:
                warnings.append(f"{item.id}: VH polarization unavailable; quality reduced.")
            try:
                raster = self._raster_reader(item, geometry)
            except Exception as exc:
                warnings.append(f"{item.id}: raster unavailable ({type(exc).__name__}).")
                continue
            properties = item.properties
            relative_orbit = _normalize_relative_orbit(
                properties.get("sat:relative_orbit")
            )
            metrics = {
                "item_id": str(item.id),
                "platform": properties.get("platform") or properties.get("constellation"),
                "orbit_state": properties.get("sat:orbit_state"),
                "relative_orbit": relative_orbit,
                "instrument_mode": properties.get("sar:instrument_mode"),
                "polarizations": ",".join(polarizations),
                "metadata_polarizations": ",".join(metadata_polarizations),
                **raster.__dict__,
            }
            if "VH" in polarizations and raster.vh_amplitude_median is None:
                metrics["polarizations"] = "VV"
                warnings.append(f"{item.id}: VH has no valid pixels in the AOI.")
            observations.append(
                EvidenceObservation(observed_at=_item_datetime(item), metrics=metrics)
            )

        if not observations:
            return SourceEvidence(
                source=self.source,
                status=EvidenceStatus.UNAVAILABLE,
                provenance=self._provenance(),
                warnings=tuple(warnings or ["Sentinel-1 raster observations were unavailable."]),
            )

        orbit_states = sorted(
            {
                str(item.metrics["orbit_state"])
                for item in observations
                if item.metrics.get("orbit_state")
            }
        )
        if len(orbit_states) > 1:
            warnings.append(
                "Multiple orbit states are present; no temporal trend was inferred."
            )
        grouped_observations = group_observations_by_relative_orbit(observations)
        relative_orbits = [
            orbit for orbit in grouped_observations if orbit is not None
        ]
        unassigned_count = len(grouped_observations.get(None, ()))
        if len(relative_orbits) > 1:
            formatted_orbits = ", ".join(str(orbit) for orbit in relative_orbits)
            warnings.append(
                "Multiple Sentinel-1 relative orbits are present "
                f"({formatted_orbits}); global metrics are descriptive only and "
                "temporal comparison must use a single relative orbit."
            )
        if unassigned_count:
            warnings.append(
                f"{unassigned_count} Sentinel-1 observation(s) have no valid "
                "sat:relative_orbit; they remain in the unknown group and are "
                "excluded from canonical relative-orbit selection."
            )
        metrics_by_relative_orbit = {
            str(orbit) if orbit is not None else "unknown": _aggregate_observations(items)
            for orbit, items in grouped_observations.items()
        }
        canonical_relative_orbit, canonical_observations = (
            select_canonical_orbit_observations(observations)
        )
        canonical_metrics = (
            {
                "relative_orbit": canonical_relative_orbit,
                **_aggregate_observations(canonical_observations),
            }
            if canonical_relative_orbit is not None
            else None
        )
        quality, components = _quality(observations)
        coverage = float(np.mean([float(item.metrics["coverage"]) for item in observations]))

        return SourceEvidence(
            source=self.source,
            status=EvidenceStatus.AVAILABLE,
            observations=tuple(observations),
            quality=quality,
            coverage=coverage,
            observed_at=max(item.observed_at for item in observations),
            metrics={
                "observation_count": len(observations),
                "latest_observation": max(item.observed_at for item in observations).isoformat(),
                # Campos globais legados: podem misturar geometrias e sao apenas
                # descritivos; consumidores temporais devem usar canonical_metrics.
                "vv_amplitude_median_across_observations": _median_observation_metric(
                    observations, "vv_amplitude_median"
                ),
                "vh_amplitude_median_across_observations": _median_observation_metric(
                    observations, "vh_amplitude_median"
                ),
                "vh_vv_amplitude_ratio_median": _median_observation_metric(
                    observations, "vh_vv_amplitude_ratio"
                ),
                "global_metrics_temporal_use": "descriptive_only",
                "mean_valid_pixel_percentage": float(
                    np.mean(
                        [float(item.metrics["valid_pixel_percentage"]) for item in observations]
                    )
                ),
                "mean_coverage": coverage,
                "dual_polarization_observation_count": sum(
                    "VH" in str(item.metrics.get("polarizations", ""))
                    for item in observations
                ),
                "orbit_states": ",".join(orbit_states),
                "orbit_state_values": orbit_states,
                "relative_orbits": relative_orbits,
                "unassigned_relative_orbit_observation_count": unassigned_count,
                "metrics_by_relative_orbit": metrics_by_relative_orbit,
                "canonical_relative_orbit": canonical_relative_orbit,
                "canonical_observation_count": len(canonical_observations),
                "canonical_metrics": canonical_metrics,
                "temporal_comparability": _temporal_comparability(
                    relative_orbits, unassigned_count
                ),
                "quality_formula": (
                    "25 VV + 25 VH fraction + 25 valid-pixel fraction + "
                    "15 AOI coverage + 10 observation support (3 observations)"
                ),
                **{f"quality_component_{key}": value for key, value in components.items()},
            },
            provenance=self._provenance(),
            warnings=tuple(warnings),
        )

    def _provenance(self) -> dict[str, Any]:
        return {
            "provider": type(self).__name__,
            "stac_endpoint": self.endpoint,
            "collection": self.collection,
            "instrument_mode": "IW",
            "preferred_polarizations": "VV,VH",
            "asset_semantics": "detected_grd_amplitude_values_uncalibrated_by_pipeline",
            "radiometric_conversion": "none",
            "spatial_read": "windowed_cog",
            "temporal_grouping": "sat:relative_orbit",
            "canonical_relative_orbit_selection": (
                "observation_count_desc,mean_coverage_desc,"
                "mean_valid_pixel_percentage_desc,latest_observation_desc,"
                "relative_orbit_asc"
            ),
        }
