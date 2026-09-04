"""Contratos isolados para futuras fontes auxiliares de evidencia."""

from .models import (
    CollectionPeriod,
    EvidenceObservation,
    EvidenceStatus,
    SourceEvidence,
)
from .orchestrator import MultisourceOrchestrator
from .providers.base import EvidenceProvider
from .providers.sentinel1 import (
    Sentinel1Provider,
    group_observations_by_relative_orbit,
    select_canonical_orbit_observations,
)
from .runtime import collect_multisource_evidence, write_multisource_evidence

__all__ = [
    "CollectionPeriod",
    "EvidenceObservation",
    "EvidenceProvider",
    "EvidenceStatus",
    "MultisourceOrchestrator",
    "SourceEvidence",
    "Sentinel1Provider",
    "group_observations_by_relative_orbit",
    "select_canonical_orbit_observations",
    "collect_multisource_evidence",
    "write_multisource_evidence",
]
