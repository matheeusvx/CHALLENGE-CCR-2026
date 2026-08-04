"""Testes da priorizacao temporal sem chamadas externas."""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.satellite_monitoring.stac_client import Scene, select_scenes


def _scene(day: int) -> Scene:
    observed_at = datetime(2026, 8, day, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=f"scene-{day}",
        datetime=observed_at,
        properties={"datetime": observed_at.isoformat()},
        assets={},
    )
    return Scene(item)


def test_newest_applies_limit_after_descending_priority() -> None:
    selected, discarded = select_scenes(
        [_scene(1), _scene(4), _scene(2), _scene(3)],
        max_scenes=2,
        scene_order="newest",
    )
    assert [scene.item_id for scene in selected] == ["scene-3", "scene-4"]
    assert [scene.item_id for scene in discarded] == ["scene-1", "scene-2"]


def test_oldest_applies_limit_after_ascending_priority() -> None:
    selected, discarded = select_scenes(
        [_scene(4), _scene(2), _scene(1), _scene(3)],
        max_scenes=2,
        scene_order="oldest",
    )
    assert [scene.item_id for scene in selected] == ["scene-1", "scene-2"]
    assert [scene.item_id for scene in discarded] == ["scene-3", "scene-4"]


def test_selected_scenes_are_always_returned_chronologically() -> None:
    selected, _ = select_scenes(
        [_scene(1), _scene(2), _scene(3)],
        max_scenes=3,
        scene_order="newest",
    )
    assert [scene.item_id for scene in selected] == ["scene-1", "scene-2", "scene-3"]
