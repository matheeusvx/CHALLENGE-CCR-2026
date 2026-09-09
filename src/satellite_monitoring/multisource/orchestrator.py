"""Execucao fail-soft de providers auxiliares ainda desconectada do pipeline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import CollectionPeriod, EvidenceStatus, SourceEvidence
from .providers.base import EvidenceProvider


class MultisourceOrchestrator:
    """Coleta providers independentemente e representa falhas sem propaga-las."""

    def __init__(self, providers: Iterable[EvidenceProvider]) -> None:
        self._providers = tuple(providers)

    def collect(
        self,
        geometry: Mapping[str, object],
        analysis_period: CollectionPeriod,
    ) -> tuple[SourceEvidence, ...]:
        evidence: list[SourceEvidence] = []
        for provider in self._providers:
            try:
                evidence.append(provider.collect_evidence(geometry, analysis_period))
            except Exception as exc:
                evidence.append(
                    SourceEvidence(
                        source=provider.source,
                        status=EvidenceStatus.ERROR,
                        provenance={
                            "provider": provider.source,
                            "error_type": type(exc).__name__,
                        },
                        warnings=("Auxiliary provider failed while collecting evidence.",),
                    )
                )
        return tuple(evidence)
