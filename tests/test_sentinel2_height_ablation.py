"""Testes offline da leitura multibanda e validacao compartilhada."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from src.satellite_monitoring.experiments.sentinel2_ablation import (
    HeightMaskRejectedError,
    MissingMultibandAssetError,
    calculate_ndii,
    calculate_ndre,
    common_sample_ids,
    fixed_fold_oof_scores,
    read_multiband_height_features,
    resolve_multiband_asset_keys,
    shared_group_fold_assignment,
)


def _asset(path, *, scale=0.0001, offset=0.0):
    return SimpleNamespace(
        href=str(path),
        extra_fields={"raster:bands": [{"scale": scale, "offset": offset}]},
    )


def _write_raster(path, values, transform, *, dtype="uint16") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype=dtype,
        crs="EPSG:31983",
        transform=transform,
    ) as dataset:
        dataset.write(values.astype(dtype), 1)


def _local_item(tmp_path, *, scl_class: int = 4):
    forward = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
    backward = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
    center_x, center_y = forward.transform(-47.0, -23.0)
    transform_20 = from_origin(center_x - 40, center_y + 40, 20, 20)
    transform_10 = from_origin(center_x - 40, center_y + 40, 10, 10)
    specifications = {
        "B04": (np.full((8, 8), 2000), transform_10),
        "B08": (np.full((8, 8), 5000), transform_10),
        "B05": (np.full((4, 4), 3000), transform_20),
        "B06": (np.full((4, 4), 3500), transform_20),
        "B07": (np.full((4, 4), 4000), transform_20),
        "B8A": (np.full((4, 4), 4800), transform_20),
        "B11": (np.full((4, 4), 2500), transform_20),
        "B12": (np.full((4, 4), 2000), transform_20),
        "SCL": (np.full((4, 4), scl_class), transform_20),
    }
    assets = {}
    for key, (values, transform) in specifications.items():
        path = tmp_path / f"{key}.tif"
        _write_raster(path, values, transform)
        assets[key] = _asset(path, scale=1.0, offset=0.0) if key == "SCL" else _asset(path)
    left, bottom = backward.transform(center_x - 35, center_y - 35)
    right, top = backward.transform(center_x + 35, center_y + 35)
    aoi = {
        "type": "Polygon",
        "coordinates": [
            [[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]
        ],
    }
    return SimpleNamespace(assets=assets), aoi


def test_reads_required_bands_on_20m_grid_with_shared_scaling(tmp_path) -> None:
    item, aoi = _local_item(tmp_path)
    observation = read_multiband_height_features(item, aoi)

    assert set(resolve_multiband_asset_keys(item)) == {
        "red", "nir", "red_edge_1", "red_edge_2", "red_edge_3",
        "narrow_nir", "swir1", "swir2", "scl",
    }
    assert observation.analysis_resolution == 20.0
    assert observation.source_resolution["red"] == pytest.approx(10.0)
    assert observation.source_resolution["swir1"] == pytest.approx(20.0)
    assert observation.continuous_resampling_method == "bilinear"
    assert observation.categorical_resampling_method == "nearest"
    assert observation.values["red_edge_1_reflectance"] == pytest.approx(0.3)
    assert observation.values["swir2_reflectance"] == pytest.approx(0.2)
    assert observation.values["red_reflectance"] == pytest.approx(0.2)
    assert observation.values["nir_reflectance"] == pytest.approx(0.5)
    assert observation.values["ndvi"] == pytest.approx((0.5 - 0.2) / (0.5 + 0.2))
    assert observation.values["ndre"] == pytest.approx((0.48 - 0.3) / (0.48 + 0.3))
    assert observation.values["ndii"] == pytest.approx((0.48 - 0.25) / (0.48 + 0.25))
    assert observation.vegetation_fraction == pytest.approx(1.0)


def test_multiband_reader_rejects_missing_band_and_failed_height_mask(tmp_path) -> None:
    item, aoi = _local_item(tmp_path / "valid")
    del item.assets["B12"]
    with pytest.raises(MissingMultibandAssetError):
        read_multiband_height_features(item, aoi)

    non_vegetated, aoi = _local_item(tmp_path / "nonveg", scl_class=5)
    with pytest.raises(HeightMaskRejectedError):
        read_multiband_height_features(non_vegetated, aoi)


def test_ndre_and_ndii_handle_ratio_arrays() -> None:
    narrow = np.asarray([0.5, 0.4])
    red_edge = np.asarray([0.3, 0.2])
    swir = np.asarray([0.25, 0.2])
    assert calculate_ndre(narrow, red_edge).tolist() == pytest.approx([0.25, 1 / 3])
    assert calculate_ndii(narrow, swir).tolist() == pytest.approx([1 / 3, 1 / 3])


def _fold_rows() -> list[dict[str, object]]:
    return [
        {
            "sample_id": f"sample-{index:02d}",
            "group_id": f"km-{index:02d}",
            "target": index % 2,
            "red_reflectance": 0.1 + 0.01 * (index % 2),
            "nir_reflectance": 0.4 - 0.02 * (index % 2),
            "ndvi": 0.5 - 0.1 * (index % 2),
        }
        for index in range(20)
    ]


def test_common_cohort_and_shared_folds_are_deterministic_without_leakage() -> None:
    rows = _fold_rows()
    assignment, fold_count = shared_group_fold_assignment(rows)
    repeated, repeated_count = shared_group_fold_assignment(list(reversed(rows)))
    assert assignment == repeated
    assert fold_count == repeated_count
    assert len(assignment) == len(rows)

    y1, scores1, folds1 = fixed_fold_oof_scores(
        rows, ("red_reflectance", "nir_reflectance", "ndvi"), assignment
    )
    y2, scores2, folds2 = fixed_fold_oof_scores(
        rows, ("red_reflectance", "nir_reflectance", "ndvi"), assignment
    )
    assert np.array_equal(y1, y2)
    assert np.allclose(scores1, scores2)
    assert folds1 == folds2
    for fold in folds1:
        assert set(fold["train_groups"]).isdisjoint(fold["test_groups"])

    first = [{"sample_id": "a"}, {"sample_id": "b"}]
    second = [{"sample_id": "b"}, {"sample_id": "c"}]
    third = [{"sample_id": "b"}]
    assert common_sample_ids(first, second, third) == ["b"]
