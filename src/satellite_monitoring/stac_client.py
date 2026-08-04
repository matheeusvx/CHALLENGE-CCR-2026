"""Consulta de cenas Sentinel-2 L2A no Microsoft Planetary Computer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import planetary_computer
import pystac_client

from .config import MonitoringConfig


class NoScenesError(RuntimeError):
    """Indica que a consulta real nao retornou cenas compativeis."""


@dataclass(frozen=True)
class Scene:
    """Item STAC acompanhado dos metadados usados nas saidas."""

    item: Any

    @property
    def item_id(self) -> str:
        return self.item.id

    @property
    def datetime(self) -> datetime:
        item_datetime = self.item.datetime
        if item_datetime is not None:
            return item_datetime

        raw_datetime = self.item.properties.get("datetime")
        if not raw_datetime:
            return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))

    def to_record(self) -> dict[str, Any]:
        properties = self.item.properties
        return {
            "item_id": self.item_id,
            "datetime": self.datetime.isoformat(),
            "cloud_cover": properties.get("eo:cloud_cover"),
            "platform": properties.get("platform") or properties.get("constellation"),
            "tile": properties.get("s2:mgrs_tile"),
            "available_assets": ";".join(sorted(self.item.assets)),
        }


@dataclass(frozen=True)
class SceneSearchResult:
    """Cenas selecionadas e total de itens compativeis antes do limite."""

    scenes: list[Scene]
    discarded_scenes: list[Scene]
    total_matches: int


def open_stac_client(endpoint: str) -> pystac_client.Client:
    """Abre o catalogo e assina os assets retornados no proprio item."""
    return pystac_client.Client.open(
        endpoint,
        modifier=planetary_computer.sign_inplace,
    )


def select_scenes(
    scenes: list[Scene],
    max_scenes: int,
    scene_order: str,
) -> tuple[list[Scene], list[Scene]]:
    """Prioriza cenas, aplica o limite e devolve a selecao cronologica."""
    if max_scenes <= 0:
        raise ValueError("A quantidade maxima de cenas deve ser maior que zero.")
    if scene_order not in {"newest", "oldest"}:
        raise ValueError("A ordem das cenas deve ser 'newest' ou 'oldest'.")

    prioritized = sorted(
        scenes,
        key=lambda scene: scene.datetime,
        reverse=scene_order == "newest",
    )
    selected = prioritized[:max_scenes]
    discarded = prioritized[max_scenes:]

    # CSV e grafico sempre apresentam a serie da data mais antiga para a mais nova.
    chronological = lambda scene: scene.datetime
    return sorted(selected, key=chronological), sorted(discarded, key=chronological)


def search_scenes(config: MonitoringConfig, aoi_geojson: dict[str, Any]) -> SceneSearchResult:
    """Pesquisa todas as cenas e somente depois aplica estrategia e limite."""
    client = open_stac_client(config.endpoint)
    search = client.search(
        collections=[config.collection],
        intersects=aoi_geojson,
        datetime=config.datetime_range,
        query={"eo:cloud_cover": {"lte": config.max_cloud_cover}},
    )

    # item_collection() e a API atual; get_all_items() foi depreciada.
    items = list(search.item_collection())
    scenes = [Scene(item) for item in items]

    if not scenes:
        raise NoScenesError(
            "Nenhuma cena Sentinel-2 L2A foi encontrada para a area, datas "
            "e limite de nuvens informados."
        )

    selected, discarded = select_scenes(
        scenes,
        max_scenes=config.max_scenes,
        scene_order=config.scene_order,
    )
    return SceneSearchResult(
        scenes=selected,
        discarded_scenes=discarded,
        total_matches=len(scenes),
    )
