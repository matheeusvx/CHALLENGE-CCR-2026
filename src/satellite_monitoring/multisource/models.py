"""Modelos comuns para evidencias de sensores auxiliares opcionais."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, TypeAlias

MetricScalar: TypeAlias = str | int | float | bool | None
MetricValue: TypeAlias = (
    MetricScalar | list["MetricValue"] | dict[str, "MetricValue"]
)


class EvidenceStatus(str, Enum):
    """Disponibilidade da evidencia sem confundir falha com falta de cobertura."""

    AVAILABLE = "available"
    NO_COVERAGE = "no_coverage"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True)
class CollectionPeriod:
    """Intervalo solicitado a um provider, inclusivo nas duas extremidades."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("Collection period start cannot be after end.")


@dataclass(frozen=True)
class EvidenceObservation:
    """Observacao individual preservada por uma fonte auxiliar."""

    observed_at: datetime
    metrics: dict[str, MetricValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class SourceEvidence:
    """Envelope auditavel e independente de sensor para evidencia auxiliar."""

    source: str
    status: EvidenceStatus
    observations: tuple[EvidenceObservation, ...] = ()
    quality: float | None = None
    coverage: float | None = None
    observed_at: datetime | None = None
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Evidence source cannot be empty.")
        for field_name, value in (("quality", self.quality), ("coverage", self.coverage)):
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{field_name} must be between 0 and 100.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "quality": self.quality,
            "coverage": self.coverage,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "observations": [observation.to_dict() for observation in self.observations],
            "metrics": dict(self.metrics),
            "provenance": dict(self.provenance),
            "warnings": list(self.warnings),
        }
