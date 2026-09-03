"""Composicao dos providers auxiliares habilitados no pipeline principal."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import MonitoringConfig
from ..outputs import to_json_compatible
from .models import CollectionPeriod
from .orchestrator import MultisourceOrchestrator
from .providers.sentinel1 import Sentinel1Provider


def collect_multisource_evidence(
    config: MonitoringConfig,
    geometry: Mapping[str, object],
    *,
    provider_factory: Callable[..., Any] = Sentinel1Provider,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any] | None:
    """Coleta fontes habilitadas sem permitir que alterem a recomendacao oficial."""
    if not config.multisource_enabled:
        return None
    providers = []
    if config.multisource_fusion_mode == "shadow" and config.sentinel1_enabled:
        providers.append(
            provider_factory(
                endpoint=config.endpoint,
                collection=config.sentinel1_collection,
                max_scenes=config.sentinel1_max_scenes,
            )
        )
    assert config.start_date is not None and config.end_date is not None
    evidence = MultisourceOrchestrator(providers).collect(
        geometry,
        CollectionPeriod(config.start_date, config.end_date),
    )
    return {
        "enabled": True,
        "fusion_mode": config.multisource_fusion_mode,
        "official_recommendation_changed": False,
        "generated_at": now().isoformat(),
        "configuration": {
            "sentinel1_enabled": config.sentinel1_enabled,
            "sentinel1_collection": config.sentinel1_collection,
            "sentinel1_max_scenes": config.sentinel1_max_scenes,
            "analysis_period": config.datetime_range,
        },
        "sources": [item.to_dict() for item in evidence],
    }


def write_multisource_evidence(
    run_directory: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    """Persiste evidencia auditavel sem incluir caminhos internos no documento."""
    path = Path(run_directory) / "multisource_evidence.json"
    path.write_text(
        json.dumps(
            to_json_compatible(dict(payload)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
