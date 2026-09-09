"""Leitura multibanda e validacao controlada para a ablacao offline."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, shape
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold

from ..features.vegetation_mask import (
    HeightMaskConfig,
    build_height_valid_mask,
    diagnose_height_mask_pixels,
)
from ..indices import calculate_ndvi
from ..models.validation import build_training_pipeline
from ..raster_processing import (
    RasterProcessingError,
    SCL_EXCLUDED_CLASSES,
    apply_reflectance_scaling,
    find_asset_key,
    resolve_physical_reflectance_scaling,
)

ANALYSIS_RESOLUTION_M = 20.0
CONTINUOUS_RESAMPLING = "bilinear"
CATEGORICAL_RESAMPLING = "nearest"
REFLECTANCE_SANITY_MIN = -0.2
REFLECTANCE_SANITY_MAX = 1.6
ABLATION_RANDOM_SEED = 20260818

MULTIBAND_ASSET_SPECS: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "red": ({"red"}, ("B04", "red")),
    "nir": ({"nir", "nir08"}, ("B08", "nir")),
    "red_edge_1": ({"rededge"}, ("B05",)),
    "red_edge_2": ({"rededge"}, ("B06",)),
    "red_edge_3": ({"rededge"}, ("B07",)),
    "narrow_nir": ({"nir09", "nir"}, ("B8A",)),
    "swir1": ({"swir16"}, ("B11", "swir16")),
    "swir2": ({"swir22"}, ("B12", "swir22")),
}
MULTIBAND_RAW_FEATURES = (
    "red_edge_1_reflectance",
    "red_edge_2_reflectance",
    "red_edge_3_reflectance",
    "narrow_nir_reflectance",
    "swir1_reflectance",
    "swir2_reflectance",
)
MULTIBAND_INDEX_FEATURES = ("ndre", "ndii")


class HeightMaskRejectedError(RasterProcessingError):
    """Cena lida corretamente, mas sem pureza vegetal suficiente."""

    def __init__(self, message: str, *, diagnostics: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class MissingMultibandAssetError(RasterProcessingError):
    """Cena nao possui uma das bandas obrigatorias do experimento."""


@dataclass(frozen=True)
class MultibandObservation:
    values: dict[str, float]
    height_valid_pixel_count: int
    height_total_pixel_count: int
    vegetation_fraction: float
    mixed_pixel_risk: str
    source_resolution: dict[str, float]
    analysis_resolution: float
    continuous_resampling_method: str
    categorical_resampling_method: str
    asset_keys: dict[str, str]
    reflectance_scale_sources: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _asset_key(item: Any, names: set[str], fallbacks: tuple[str, ...]) -> str | None:
    keys_by_lowercase = {key.lower(): key for key in item.assets}
    for fallback in fallbacks:
        if fallback.lower() in keys_by_lowercase:
            return keys_by_lowercase[fallback.lower()]
    return find_asset_key(item, names, fallbacks)


def resolve_multiband_asset_keys(item: Any) -> dict[str, str]:
    resolved = {
        name: _asset_key(item, common_names, fallbacks)
        for name, (common_names, fallbacks) in MULTIBAND_ASSET_SPECS.items()
    }
    missing = sorted(name for name, key in resolved.items() if key is None)
    if missing:
        raise MissingMultibandAssetError(
            "Missing multiband assets: " + ", ".join(missing)
        )
    scl_key = _asset_key(item, {"scl"}, ("SCL", "scl"))
    if scl_key is None:
        raise MissingMultibandAssetError("Missing multiband asset: SCL")
    return {**{name: str(key) for name, key in resolved.items()}, "scl": scl_key}


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan)
    np.divide(
        numerator,
        denominator,
        out=result,
        where=np.isfinite(denominator) & (np.abs(denominator) > 1e-12),
    )
    return result


def calculate_ndre(narrow_nir: np.ndarray, red_edge_1: np.ndarray) -> np.ndarray:
    """NDRE = (B8A - B05) / (B8A + B05)."""
    return _safe_ratio(narrow_nir - red_edge_1, narrow_nir + red_edge_1)


def calculate_ndii(narrow_nir: np.ndarray, swir1: np.ndarray) -> np.ndarray:
    """NDII = (B8A - B11) / (B8A + B11)."""
    return _safe_ratio(narrow_nir - swir1, narrow_nir + swir1)


def _source_resolution(dataset: Any) -> float:
    return max(abs(float(dataset.transform.a)), abs(float(dataset.transform.e)))


def read_multiband_height_features(
    item: Any,
    aoi_geojson: dict[str, Any],
    *,
    height_mask_config: HeightMaskConfig = HeightMaskConfig(),
) -> MultibandObservation:
    """Resume reflectancias em grade B11 de 20 m; SCL usa nearest."""
    keys = resolve_multiband_asset_keys(item)
    reference_asset = item.assets[keys["swir1"]]
    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    try:
        with rasterio.Env(**env_options), ExitStack() as stack:
            reference = stack.enter_context(rasterio.open(reference_asset.href))
            aoi = transform_geom("EPSG:4326", reference.crs, aoi_geojson, precision=15)
            window = geometry_window(reference, [aoi])
            output_transform = reference.window_transform(window)
            output_shape = (int(window.height), int(window.width))
            inside_aoi = geometry_mask(
                [aoi], out_shape=output_shape, transform=output_transform, invert=True
            )
            if not np.any(inside_aoi):
                raise RasterProcessingError("AOI has no pixels in the 20 m grid.")

            physical: dict[str, np.ndarray] = {}
            band_valid: dict[str, np.ndarray] = {}
            source_resolutions: dict[str, float] = {}
            scale_sources: dict[str, str] = {}
            for name in MULTIBAND_ASSET_SPECS:
                key = keys[name]
                source = stack.enter_context(rasterio.open(item.assets[key].href))
                source_resolutions[name] = _source_resolution(source)
                scaling = resolve_physical_reflectance_scaling(
                    item,
                    asset_key=key,
                    dataset_scale=float(source.scales[0]),
                    dataset_offset=float(source.offsets[0]),
                )
                scale_sources[name] = scaling.source
                if name == "swir1":
                    raw = reference.read(1, window=window, masked=True)
                else:
                    vrt = stack.enter_context(
                        WarpedVRT(
                            source,
                            crs=reference.crs,
                            transform=reference.transform,
                            width=reference.width,
                            height=reference.height,
                            resampling=Resampling.bilinear,
                        )
                    )
                    raw = vrt.read(1, window=window, masked=True)
                raw_values = np.asarray(raw.data, dtype=float)
                values = apply_reflectance_scaling(
                    raw_values, scale=scaling.scale, offset=scaling.offset
                )
                valid = ~np.ma.getmaskarray(raw)
                valid &= raw_values != 0
                valid &= np.isfinite(values)
                valid &= values >= REFLECTANCE_SANITY_MIN
                valid &= values <= REFLECTANCE_SANITY_MAX
                physical[name] = values
                band_valid[name] = valid

            scl_source = stack.enter_context(rasterio.open(item.assets[keys["scl"]].href))
            source_resolutions["scl"] = _source_resolution(scl_source)
            scl_vrt = stack.enter_context(
                WarpedVRT(
                    scl_source,
                    crs=reference.crs,
                    transform=reference.transform,
                    width=reference.width,
                    height=reference.height,
                    resampling=Resampling.nearest,
                )
            )
            scl = scl_vrt.read(1, window=window, masked=True)
            scl_values = np.asarray(scl.data)
            scl_valid = ~np.ma.getmaskarray(scl)
            quality_valid = inside_aoi & band_valid["red"] & band_valid["nir"]
            quality_valid &= scl_valid
            quality_valid &= ~np.isin(scl_values, list(SCL_EXCLUDED_CLASSES))
            mask = build_height_valid_mask(
                physical["red"],
                physical["nir"],
                quality_valid_mask=quality_valid,
                inside_aoi_mask=inside_aoi,
                scl_values=scl_values,
                scl_valid_mask=scl_valid,
                config=height_mask_config,
            )
            if not mask.purity_gate_passed:
                raise HeightMaskRejectedError(
                    "Height mask rejected scene: " + ", ".join(mask.purity_gate_reasons),
                    diagnostics={
                        "height_valid_pixel_count": mask.height_valid_pixel_count,
                        "height_total_pixel_count": mask.height_total_pixel_count,
                        "vegetation_fraction": mask.vegetation_fraction,
                        "mixed_pixel_risk": mask.mixed_pixel_risk,
                        "height_purity_gate_passed": mask.purity_gate_passed,
                        "height_purity_gate_reasons": list(mask.purity_gate_reasons),
                    },
                )
            common_valid = mask.valid_mask.copy()
            for name in MULTIBAND_ASSET_SPECS:
                common_valid &= band_valid[name]
            if not np.any(common_valid):
                raise RasterProcessingError(
                    "No common height-valid pixels across multiband assets."
                )

            indices = {
                "ndvi": calculate_ndvi(
                    physical["red"], physical["nir"], common_valid
                )[0],
                "ndre": calculate_ndre(physical["narrow_nir"], physical["red_edge_1"]),
                "ndii": calculate_ndii(physical["narrow_nir"], physical["swir1"]),
            }
            values = {
                f"{name}_reflectance": float(np.median(physical[name][common_valid]))
                for name in (
                    "red",
                    "nir",
                    "red_edge_1",
                    "red_edge_2",
                    "red_edge_3",
                    "narrow_nir",
                    "swir1",
                    "swir2",
                )
            }
            for name, array in indices.items():
                valid = common_valid & np.isfinite(array)
                if not np.any(valid):
                    raise RasterProcessingError(f"No finite values for {name}.")
                values[name] = float(np.median(array[valid]))
            if not all(math.isfinite(value) for value in values.values()):
                raise RasterProcessingError("Multiband features contain non-finite values.")
            return MultibandObservation(
                values=values,
                height_valid_pixel_count=mask.height_valid_pixel_count,
                height_total_pixel_count=mask.height_total_pixel_count,
                vegetation_fraction=mask.vegetation_fraction,
                mixed_pixel_risk=mask.mixed_pixel_risk,
                source_resolution=source_resolutions,
                analysis_resolution=ANALYSIS_RESOLUTION_M,
                continuous_resampling_method=CONTINUOUS_RESAMPLING,
                categorical_resampling_method=CATEGORICAL_RESAMPLING,
                asset_keys=keys,
                reflectance_scale_sources=scale_sources,
            )
    except (RasterProcessingError, HeightMaskRejectedError, MissingMultibandAssetError):
        raise
    except Exception as exc:
        raise RasterProcessingError(
            f"Failed isolated 20 m multiband read: {type(exc).__name__}: {exc}"
        ) from exc


def read_height_mask_pixel_diagnostics(
    item: Any,
    aoi_geojson: dict[str, Any],
    *,
    height_mask_config: HeightMaskConfig = HeightMaskConfig(),
) -> dict[str, Any]:
    """Lê RED/NIR/SCL na mesma grade e explica a máscara pixel a pixel."""
    keys = resolve_multiband_asset_keys(item)
    reference_asset = item.assets[keys["swir1"]]
    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    }
    with rasterio.Env(**env_options), ExitStack() as stack:
        reference = stack.enter_context(rasterio.open(reference_asset.href))
        aoi = transform_geom("EPSG:4326", reference.crs, aoi_geojson, precision=15)
        aoi_shape = shape(aoi)
        window = geometry_window(reference, [aoi])
        output_transform = reference.window_transform(window)
        output_shape = (int(window.height), int(window.width))
        inside_aoi = geometry_mask(
            [aoi], out_shape=output_shape, transform=output_transform, invert=True
        )
        physical: dict[str, np.ndarray] = {}
        band_valid: dict[str, np.ndarray] = {}
        for name in ("red", "nir"):
            key = keys[name]
            source = stack.enter_context(rasterio.open(item.assets[key].href))
            scaling = resolve_physical_reflectance_scaling(
                item,
                asset_key=key,
                dataset_scale=float(source.scales[0]),
                dataset_offset=float(source.offsets[0]),
            )
            vrt = stack.enter_context(
                WarpedVRT(
                    source,
                    crs=reference.crs,
                    transform=reference.transform,
                    width=reference.width,
                    height=reference.height,
                    resampling=Resampling.bilinear,
                )
            )
            raw = vrt.read(1, window=window, masked=True)
            raw_values = np.asarray(raw.data, dtype=float)
            values = apply_reflectance_scaling(
                raw_values, scale=scaling.scale, offset=scaling.offset
            )
            valid = ~np.ma.getmaskarray(raw)
            valid &= raw_values != 0
            valid &= np.isfinite(values)
            valid &= values >= REFLECTANCE_SANITY_MIN
            valid &= values <= REFLECTANCE_SANITY_MAX
            physical[name] = values
            band_valid[name] = valid
        scl_source = stack.enter_context(rasterio.open(item.assets[keys["scl"]].href))
        scl_vrt = stack.enter_context(
            WarpedVRT(
                scl_source,
                crs=reference.crs,
                transform=reference.transform,
                width=reference.width,
                height=reference.height,
                resampling=Resampling.nearest,
            )
        )
        scl = scl_vrt.read(1, window=window, masked=True)
        scl_values = np.asarray(scl.data)
        scl_valid = ~np.ma.getmaskarray(scl)
        radiometric_valid = band_valid["red"] & band_valid["nir"]
        quality_valid = inside_aoi & radiometric_valid & scl_valid
        quality_valid &= ~np.isin(scl_values, list(SCL_EXCLUDED_CLASSES))
        fractions = np.zeros(output_shape, dtype=float)
        cell_area = abs(
            output_transform.a * output_transform.e
            - output_transform.b * output_transform.d
        )
        for row_index, column_index in np.argwhere(inside_aoi):
            corners = [
                output_transform * (column_index, row_index),
                output_transform * (column_index + 1, row_index),
                output_transform * (column_index + 1, row_index + 1),
                output_transform * (column_index, row_index + 1),
            ]
            cell = Polygon(corners)
            fractions[row_index, column_index] = (
                float(aoi_shape.intersection(cell).area / cell.area)
                if cell.area > 0
                else 0.0
            )
        pixels = diagnose_height_mask_pixels(
            physical["red"],
            physical["nir"],
            quality_valid_mask=quality_valid,
            radiometric_valid_mask=radiometric_valid,
            inside_aoi_mask=inside_aoi,
            scl_values=scl_values,
            scl_valid_mask=scl_valid,
            inside_aoi_fraction=fractions,
            config=height_mask_config,
        )
        for pixel in pixels:
            x, y = output_transform * (
                float(pixel["column"]) + 0.5,
                float(pixel["row"]) + 0.5,
            )
            pixel["center_x"] = float(x)
            pixel["center_y"] = float(y)
        result = build_height_valid_mask(
            physical["red"],
            physical["nir"],
            quality_valid_mask=quality_valid,
            inside_aoi_mask=inside_aoi,
            scl_values=scl_values,
            scl_valid_mask=scl_valid,
            config=height_mask_config,
        )
        rejection_counts: dict[str, int] = {}
        for pixel in pixels:
            for reason in pixel["rejection_reason"]:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        scl_counts: dict[str, int] = {}
        for pixel in pixels:
            key = str(pixel["SCL"])
            scl_counts[key] = scl_counts.get(key, 0) + 1
        min_x, min_y, max_x, max_y = aoi_shape.bounds
        return {
            "pixels": pixels,
            "summary": {
                "total_pixels": result.height_total_pixel_count,
                "valid_height_pixels": result.height_valid_pixel_count,
                "invalid_height_pixels": (
                    result.height_total_pixel_count - result.height_valid_pixel_count
                ),
                "vegetation_fraction": result.vegetation_fraction,
                "mixed_pixel_risk": result.mixed_pixel_risk,
                "purity_gate_passed": result.purity_gate_passed,
                "purity_gate_reasons": list(result.purity_gate_reasons),
                "rejection_reason_counts": rejection_counts,
                "scl_counts": scl_counts,
                "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
                "single_pixel_area_m2": float(cell_area),
                "effective_sentinel_pixel_area_m2": float(
                    result.height_total_pixel_count * cell_area
                ),
                "aoi_projected_area_m2": float(aoi_shape.area),
                "aoi_area_within_effective_pixels_m2": float(
                    sum(pixel["inside_aoi_fraction"] for pixel in pixels) * cell_area
                ),
                "aoi_bbox_width_m": float(max_x - min_x),
                "aoi_bbox_height_m": float(max_y - min_y),
                "reference_crs": str(reference.crs),
            },
        }


def common_sample_ids(*experiments: Sequence[Mapping[str, Any]]) -> list[str]:
    if not experiments:
        return []
    populations = [
        {str(row["sample_id"]) for row in rows if row.get("valid", True)}
        for rows in experiments
    ]
    return sorted(set.intersection(*populations)) if populations else []


def shared_group_fold_assignment(
    rows: Sequence[Mapping[str, Any]], *, max_folds: int = 5
) -> tuple[dict[str, int], int]:
    """Cria uma unica atribuicao grupo→fold, auditavel e sem leakage."""
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    y = np.asarray([int(row["target"]) for row in ordered], dtype=int)
    groups = np.asarray([str(row["group_id"]) for row in ordered], dtype=object)
    placeholder = np.zeros((len(ordered), 1), dtype=float)
    for fold_count in range(min(max_folds, len(set(groups.tolist()))), 1, -1):
        splitter = StratifiedGroupKFold(
            n_splits=fold_count, shuffle=True, random_state=ABLATION_RANDOM_SEED
        )
        assignment: dict[str, int] = {}
        valid = True
        for fold, (train, test) in enumerate(splitter.split(placeholder, y, groups)):
            if set(y[train].tolist()) != {0, 1} or set(y[test].tolist()) != {0, 1}:
                valid = False
                break
            for group in set(groups[test].tolist()):
                if group in assignment:
                    raise ValueError("Spatial group leaked into multiple folds.")
                assignment[str(group)] = fold
        if valid and len(assignment) == len(set(groups.tolist())):
            return assignment, fold_count
    raise ValueError("No shared group-aware fold assignment contains both classes per fold.")


def classification_metrics(y: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
    predicted = (scores >= 0.5).astype(int)
    matrix = confusion_matrix(y, predicted, labels=[0, 1])
    tn, fp, fn, tp = (int(value) for value in matrix.ravel())
    both_classes = set(y.tolist()) == {0, 1}
    return {
        "roc_auc": float(roc_auc_score(y, scores)) if both_classes else None,
        "pr_auc": float(average_precision_score(y, scores))
        if np.any(y == 1)
        else None,
        "balanced_accuracy": float(balanced_accuracy_score(y, predicted))
        if both_classes
        else None,
        "precision_gt_30": float(precision_score(y, predicted, zero_division=0)),
        "recall_gt_30": float(recall_score(y, predicted, zero_division=0)),
        "f1_gt_30": float(f1_score(y, predicted, zero_division=0)),
        "true_positive_count": tp,
        "false_positive_count": fp,
        "true_negative_count": tn,
        "false_negative_count": fn,
        "confusion_matrix": matrix.tolist(),
    }


def fixed_fold_oof_scores(
    rows: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    fold_assignment: Mapping[str, int],
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Prediz OOF usando exatamente a mesma atribuicao de grupos para variantes."""
    ordered = sorted(rows, key=lambda row: str(row["sample_id"]))
    x = np.asarray(
        [[float(row[name]) for name in feature_names] for row in ordered], dtype=float
    )
    y = np.asarray([int(row["target"]) for row in ordered], dtype=int)
    row_folds = np.asarray(
        [int(fold_assignment[str(row["group_id"])]) for row in ordered], dtype=int
    )
    if not np.all(np.isfinite(x)):
        raise ValueError("Ablation features contain non-finite values.")
    scores = np.full(len(ordered), np.nan, dtype=float)
    fold_rows: list[dict[str, Any]] = []
    for fold in sorted(set(row_folds.tolist())):
        test = np.flatnonzero(row_folds == fold)
        train = np.flatnonzero(row_folds != fold)
        if not len(test) or set(y[train].tolist()) != {0, 1}:
            continue
        model = build_training_pipeline()
        model.fit(x[train], y[train])
        scores[test] = model.predict_proba(x[test])[:, 1]
        metrics = classification_metrics(y[test], scores[test])
        fold_rows.append(
            {
                "fold": fold,
                "train_n": int(len(train)),
                "test_n": int(len(test)),
                "train_groups": sorted(
                    {str(ordered[index]["group_id"]) for index in train}
                ),
                "test_groups": sorted(
                    {str(ordered[index]["group_id"]) for index in test}
                ),
                **metrics,
            }
        )
    valid = np.isfinite(scores)
    if not np.any(valid):
        raise ValueError("No OOF scores were produced for the experiment.")
    return y[valid], scores[valid], fold_rows
