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
from .shadow_review import attach_shadow_review, evaluate_shadow_review
from .operational_fusion import (
    OperationalFusionAuthorization,
    OperationalFusionPolicy,
    attach_operational_fusion_audit,
    authorize_operational_fusion,
)
from .experimental_fusion import (
    ExperimentalFusionPolicyV1,
    attach_experimental_fusion,
)

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
    "attach_shadow_review",
    "evaluate_shadow_review",
    "OperationalFusionAuthorization",
    "OperationalFusionPolicy",
    "attach_operational_fusion_audit",
    "authorize_operational_fusion",
    "ExperimentalFusionPolicyV1",
    "attach_experimental_fusion",
    "write_multisource_evidence",
]
