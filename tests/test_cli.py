"""Testes dos novos argumentos da interface de linha de comando."""

from src.satellite_monitoring.cli import parse_args


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
