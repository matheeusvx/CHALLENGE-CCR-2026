"""Composicao dos providers auxiliares habilitados no pipeline principal."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import MonitoringConfig
from ..outputs import to_json_compatible
from .models import CollectionPeriod
from .orchestrator import MultisourceOrchestrator
from .providers.sentinel1 import Sentinel1Provider
from ..sentinel1_temporal import analyze_sentinel1_temporal
from ..sentinel1_evidence import build_sentinel1_evidence_summary


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
    if (
        config.multisource_fusion_mode in {"shadow", "experimental", "operational"}
        and config.sentinel1_enabled
    ):
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
    enriched = []
    for item in evidence:
        if item.source == "sentinel-1":
            try:
                temporal = analyze_sentinel1_temporal(item, config.sentinel1_temporal)
            except Exception as exc:
                # An auxiliary temporal failure must preserve the collected source.
                temporal = {
                    "enabled": config.sentinel1_temporal.enabled,
                    "status": "error",
                    "combined_status": "insufficient_data",
                    "relative_orbit": item.metrics.get("canonical_relative_orbit"),
                    "interpretation_scope": "radiometric_change_only",
                    "failure_category": "unexpected_error",
                    "warnings": [
                        "sentinel1_failure:unexpected_error",
                        f"temporal_analysis_failed:{type(exc).__name__}",
                    ],
                }
            failure_counts = dict(item.metrics.get("failure_counts") or {})
            if temporal.get("combined_status") == "insufficient_data":
                failure_counts["insufficient_temporal_support"] = 1
            item = replace(
                item,
                metrics={
                    **item.metrics,
                    "temporal_analysis": temporal,
                    "temporal_usable_observation_count": temporal.get(
                        "usable_observation_count", 0
                    ),
                    "failure_counts": failure_counts,
                },
            )
        enriched.append(item)
    sources = []
    for item in enriched:
        payload = item.to_dict()
        if item.source == "sentinel-1":
            payload["summary"] = build_sentinel1_evidence_summary(item)
        sources.append(payload)
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
        "sources": sources,
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
