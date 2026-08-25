"""Testes offline do pipeline reprodutivel de ground truth de campo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.satellite_monitoring.experiments.field_calibration import (
    STATIC_FEATURE_COLUMNS,
    WAITING_FOR_FIELD_AOI_GEOJSON,
    build_field_calibration_rows,
    load_field_data,
    model_feature_payload,
    select_latest_causal_scene,
    write_field_calibration_outputs,
)
from src.satellite_monitoring.experiments.sentinel2_ablation import (
    HeightMaskRejectedError,
)


POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-46.8450, -23.2925],
            [-46.8440, -23.2925],
            [-46.8440, -23.2915],
            [-46.8450, -23.2915],
            [-46.8450, -23.2925],
        ]
    ],
}


def _write_json(path: Path, samples: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"samples": samples}), encoding="utf-8")
    return path


def _sample(**overrides: Any) -> dict[str, Any]:
    value = {
        "sample_id": "FIELD_001",
        "observed_at": "2026-08-21",
        "geometry": POLYGON,
        "measured_height_cm": 14,
        "measurement_quality": "confirmed_single_measurement",
    }
    value.update(overrides)
    return value


@dataclass
class _Payload:
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.data


def _feature_payload() -> _Payload:
    values = {
        "red_reflectance": 0.2,
        "nir_reflectance": 0.5,
        "ndvi": 0.43,
        "red_edge_1_reflectance": 0.3,
        "red_edge_2_reflectance": 0.35,
        "red_edge_3_reflectance": 0.4,
        "narrow_nir_reflectance": 0.48,
        "swir1_reflectance": 0.25,
        "swir2_reflectance": 0.2,
        "ndre": 0.23,
        "ndii": 0.31,
    }
    return _Payload(
        {
            "values": values,
            "height_valid_pixel_count": 8,
            "height_total_pixel_count": 10,
            "vegetation_fraction": 0.8,
            "mixed_pixel_risk": "low",
        }
    )


def _scene(item_id: str, scene_date: str, *, accepted: bool = True) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "datetime": f"{scene_date}T13:00:00Z",
        "accepted_for_timeseries": accepted,
        "scene_quality_score": 90.0,
        "valid_pixel_percentage": 95.0,
        "valid_pixel_count": 20,
        "aoi_coverage_percentage": 100.0,
        "cloud_cover": 1.0,
        "_scene_item": object(),
    }


def test_reads_json_and_csv_field_data(tmp_path: Path) -> None:
    json_path = _write_json(tmp_path / "field.json", [_sample()])
    assert load_field_data(json_path)[0].sample_id == "FIELD_001"
    csv_path = tmp_path / "field.csv"
    csv_path.write_text(
        "sample_id,observed_at,measured_height_cm,measurement_quality,geometry\n"
        + 'FIELD_002,2026-08-21,14,confirmed_single_measurement,"'
        + json.dumps(POLYGON).replace('"', '""')
        + '"\n',
        encoding="utf-8",
    )
    assert load_field_data(csv_path)[0].sample_id == "FIELD_002"


def test_valid_polygon_is_preserved_and_invalid_geometry_is_rejected(
    tmp_path: Path,
) -> None:
    valid = load_field_data(_write_json(tmp_path / "valid.json", [_sample()]))[0]
    assert valid.geometry is not None
    assert valid.geometry["type"] == "Polygon"
    invalid = _sample(
        sample_id="BAD",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
        },
    )
    with pytest.raises(ValueError, match="invalida|invalid"):
        load_field_data(_write_json(tmp_path / "invalid.json", [invalid]))


def test_single_measurement_does_not_invent_percentiles(tmp_path: Path) -> None:
    observation = load_field_data(_write_json(tmp_path / "single.json", [_sample()]))[0]
    assert observation.measured_height_cm == 14
    assert observation.height_p50_cm is None
    assert observation.height_p90_cm is None
    assert observation.height_max_cm is None


def test_multiple_measurements_calculate_explicit_summary(tmp_path: Path) -> None:
    observation = load_field_data(
        _write_json(
            tmp_path / "multiple.json",
            [
                _sample(
                    measured_height_cm=None,
                    measurements_cm=[10, 20, 40],
                    measurement_quality="confirmed_multiple_measurements",
                )
            ],
        )
    )[0]
    assert observation.measured_height_cm is None
    assert observation.height_p50_cm == pytest.approx(20)
    assert observation.height_p90_cm == pytest.approx(36)
    assert observation.height_max_cm == 40
    assert observation.real_class == "gt_30_cm"


def test_boundary_30_cm_is_le_30_and_holdout_is_default(tmp_path: Path) -> None:
    observation = load_field_data(
        _write_json(tmp_path / "boundary.json", [_sample(measured_height_cm=30)])
    )[0]
    assert observation.real_class == "le_30_cm"
    assert observation.boundary_case is True
    assert observation.training_eligible is False
    assert observation.external_validation is True


def test_point_without_polygon_waits_and_does_not_query_sentinel(tmp_path: Path) -> None:
    observation = load_field_data(
        _write_json(tmp_path / "point.json", [_sample(geometry=None)])
    )[0]
    called = False

    def query(*_args: Any) -> tuple[list[dict[str, Any]], str | None]:
        nonlocal called
        called = True
        return [], None

    row = build_field_calibration_rows([observation], scene_query=query)[0]
    assert called is False
    assert row["spectral_extraction_status"] == WAITING_FOR_FIELD_AOI_GEOJSON
    assert row["field_measurement_status"] == "valid_ground_truth"


def test_causal_selection_never_uses_future_scene() -> None:
    selected = select_latest_causal_scene(
        [
            _scene("past", "2026-08-18"),
            _scene("same", "2026-08-21"),
            _scene("future", "2026-08-23"),
        ],
        date(2026, 8, 21),
    )
    assert selected is not None
    assert selected["item_id"] == "same"


def test_height_mask_rejection_keeps_ground_truth_valid(tmp_path: Path) -> None:
    observation = load_field_data(_write_json(tmp_path / "mask.json", [_sample()]))[0]

    def query(*_args: Any) -> tuple[list[dict[str, Any]], str | None]:
        return [_scene("anchor", "2026-08-18")], None

    def reject(*_args: Any) -> Any:
        raise HeightMaskRejectedError(
            "height mask rejected",
            diagnostics={
                "vegetation_fraction": 0.1,
                "mixed_pixel_risk": "high",
                "height_valid_pixel_count": 1,
                "height_total_pixel_count": 10,
            },
        )

    row = build_field_calibration_rows(
        [observation], scene_query=query, feature_reader=reject
    )[0]
    assert row["field_measurement_status"] == "valid_ground_truth"
    assert row["spectral_extraction_status"] == "rejected_by_height_mask"
    assert row["vegetation_fraction"] == pytest.approx(0.1)


def test_temporal_history_is_optional_and_fails_soft(tmp_path: Path) -> None:
    observation = load_field_data(_write_json(tmp_path / "temporal.json", [_sample()]))[0]

    def query(*_args: Any) -> tuple[list[dict[str, Any]], str | None]:
        return [_scene("anchor", "2026-08-18")], None

    row = build_field_calibration_rows(
        [observation], scene_query=query, feature_reader=lambda *_: _feature_payload()
    )[0]
    assert row["spectral_extraction_status"] == "available"
    assert row["temporal_status"] == "insufficient_history"
    assert row["historical_scene_count"] == 0


def test_recommendation_context_never_enters_model_features() -> None:
    row = {name: 0.1 for name in STATIC_FEATURE_COLUMNS}
    row["recommendation_context"] = "cortar"
    row["real_class_30cm"] = "gt_30_cm"
    features = model_feature_payload(row)
    assert "recommendation_context" not in features
    assert "real_class_30cm" not in features
    assert set(features) == set(STATIC_FEATURE_COLUMNS)


def test_output_is_deterministic(tmp_path: Path) -> None:
    observation = load_field_data(
        _write_json(tmp_path / "deterministic.json", [_sample(geometry=None)])
    )[0]
    rows = build_field_calibration_rows([observation])
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_field_calibration_outputs(rows, first)
    write_field_calibration_outputs(rows, second)
    assert (first / "field_ground_truth_enriched.csv").read_bytes() == (
        second / "field_ground_truth_enriched.csv"
    ).read_bytes()
    assert (first / "field_ground_truth_quality.json").read_bytes() == (
        second / "field_ground_truth_quality.json"
    ).read_bytes()
