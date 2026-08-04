"""Testes dos argumentos da interface de linha de comando."""

import json
from datetime import date
from pathlib import Path

import pytest

from src.satellite_monitoring import cli
from src.satellite_monitoring.cli import parse_args, parse_config
from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.stac_client import SceneSearchResult


BASE_ARGUMENTS = [
    "--latitude",
    "-23.10821",
    "--longitude",
    "-46.96109",
    "--radius-meters",
    "200",
    "--start-date",
    "2026-05-01",
    "--end-date",
    "2026-08-04",
]


def test_quality_and_selection_defaults() -> None:
    args = parse_args(BASE_ARGUMENTS)
    assert args.max_scenes == 12
    assert args.scene_order == "newest"
    assert args.min_valid_pixel_percentage == 70.0
    assert args.min_observations == 4
    assert args.include_low_quality_scenes is False


def test_accepts_oldest_and_low_quality_inclusion() -> None:
    args = parse_args(
        BASE_ARGUMENTS
        + [
            "--scene-order",
            "oldest",
            "--max-scenes",
            "6",
            "--min-valid-pixel-percentage",
            "60",
            "--min-observations",
            "3",
            "--include-low-quality-scenes",
        ]
    )
    assert args.scene_order == "oldest"
    assert args.max_scenes == 6
    assert args.min_valid_pixel_percentage == 60.0
    assert args.min_observations == 3
    assert args.include_low_quality_scenes is True


def test_preserves_circle_mode_configuration() -> None:
    config = parse_config(BASE_ARGUMENTS)

    assert config.geometry_file is None
    assert config.latitude == -23.10821
    assert config.longitude == -46.96109
    assert config.radius_meters == 200


def test_accepts_geometry_file_without_circle_arguments() -> None:
    config = parse_config(
        [
            "--geometry-file",
            "data/aoi/louveira_lateral.geojson",
            "--start-date",
            "2026-05-01",
            "--end-date",
            "2026-08-04",
        ]
    )

    assert config.geometry_file == Path("data/aoi/louveira_lateral.geojson")
    assert config.latitude is None
    assert config.longitude is None
    assert config.radius_meters is None


def test_rejects_geometry_file_with_circle_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_config(
            BASE_ARGUMENTS
            + ["--geometry-file", "data/aoi/louveira_lateral.geojson"]
        )

    assert exc_info.value.code == 2


def test_rejects_missing_geometry_mode() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_config(
            [
                "--start-date",
                "2026-05-01",
                "--end-date",
                "2026-08-04",
            ]
        )

    assert exc_info.value.code == 2


def test_run_writes_geojson_aoi_metadata_without_calling_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry_document = {
        "type": "Feature",
        "properties": {"name": "faixa lateral"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-47.0, -23.0],
                    [-46.999, -23.0],
                    [-46.999, -22.999],
                    [-47.0, -22.999],
                    [-47.0, -23.0],
                ]
            ],
        },
    }
    geometry_path = tmp_path / "aoi.geojson"
    geometry_path.write_text(json.dumps(geometry_document), encoding="utf-8")
    config = MonitoringConfig(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 8, 4),
        geometry_file=geometry_path,
        output_root=tmp_path / "outputs",
    )
    monkeypatch.setattr(
        cli,
        "search_scenes",
        lambda *_: SceneSearchResult(scenes=[], discarded_scenes=[], total_matches=0),
    )

    assert cli.run(config) == 1

    run_directory = next(config.output_root.iterdir())
    summary = json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))
    saved_aoi = json.loads((run_directory / "aoi.geojson").read_text(encoding="utf-8"))
    assert summary["aoi"]["source"] == "geojson_file"
    assert summary["aoi"]["geometry_type"] == "Polygon"
    assert summary["aoi"]["feature_count"] == 1
    assert summary["aoi"]["area_square_meters"] > 0
    assert saved_aoi["geometry"] == geometry_document["geometry"]
    assert saved_aoi["source_geojson"] == geometry_document
