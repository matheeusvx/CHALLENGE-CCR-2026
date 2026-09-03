"""Contratos isolados para futuras fontes auxiliares de evidencia."""

from .models import (
    CollectionPeriod,
    EvidenceObservation,
    EvidenceStatus,
    SourceEvidence,
)
from .orchestrator import MultisourceOrchestrator
from .providers.base import EvidenceProvider
from .providers.sentinel1 import Sentinel1Provider
from .runtime import collect_multisource_evidence, write_multisource_evidence

__all__ = [
    "CollectionPeriod",
    "EvidenceObservation",
    "EvidenceProvider",
    "EvidenceStatus",
    "MultisourceOrchestrator",
    "SourceEvidence",
    "Sentinel1Provider",
    "collect_multisource_evidence",
    "write_multisource_evidence",
]
