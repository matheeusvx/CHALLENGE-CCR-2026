"""Contrato minimo implementado por futuras fontes auxiliares."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..models import CollectionPeriod, SourceEvidence


class EvidenceProvider(Protocol):
    """Provider opcional capaz de coletar evidencia para uma AOI e periodo."""

    source: str

    def collect_evidence(
        self,
        geometry: Mapping[str, object],
        analysis_period: CollectionPeriod,
    ) -> SourceEvidence:
        """Coleta evidencia sem alterar o resultado Sentinel-2."""
        ...
