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
    total_matches: int


def open_stac_client(endpoint: str) -> pystac_client.Client:
    """Abre o catalogo e assina os assets retornados no proprio item."""
    return pystac_client.Client.open(
        endpoint,
        modifier=planetary_computer.sign_inplace,
    )


def search_scenes(config: MonitoringConfig, aoi_geojson: dict[str, Any]) -> SceneSearchResult:
    """Pesquisa, ordena cronologicamente e limita cenas Sentinel-2 reais."""
    client = open_stac_client(config.endpoint)
    search = client.search(
        collections=[config.collection],
        intersects=aoi_geojson,
        datetime=config.datetime_range,
        query={"eo:cloud_cover": {"lte": config.max_cloud_cover}},
    )

    # item_collection() e a API atual; get_all_items() foi depreciada.
    items = list(search.item_collection())
    scenes = sorted((Scene(item) for item in items), key=lambda scene: scene.datetime)

    if not scenes:
        raise NoScenesError(
            "Nenhuma cena Sentinel-2 L2A foi encontrada para a area, datas "
            "e limite de nuvens informados."
        )

    return SceneSearchResult(
        scenes=scenes[: config.max_scenes],
        total_matches=len(scenes),
    )
