"""Contratos multissensor isolados, sem chamadas externas."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import ClassVar

from src.satellite_monitoring.multisource import (
    CollectionPeriod,
    EvidenceObservation,
    EvidenceStatus,
    MultisourceOrchestrator,
    SourceEvidence,
)

GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[-47.0, -23.0], [-46.99, -23.0], [-47.0, -22.99], [-47.0, -23.0]]],
}
PERIOD = CollectionPeriod(date(2026, 7, 1), date(2026, 7, 31))
OBSERVED_AT = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def test_source_evidence_available_preserves_quality_and_provenance() -> None:
    observation = EvidenceObservation(
        observed_at=OBSERVED_AT,
        metrics={"value": 12.5},
    )

    evidence = SourceEvidence(
        source="future_sensor",
        status=EvidenceStatus.AVAILABLE,
        observations=(observation,),
        quality=92.0,
        coverage=87.5,
        observed_at=OBSERVED_AT,
        metrics={"observation_count": 1},
        provenance={"provider": "future_provider", "product": "future_product"},
    )

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert evidence.observations == (observation,)
    assert evidence.quality == 92.0
    assert evidence.coverage == 87.5
    assert evidence.provenance["product"] == "future_product"


def test_source_evidence_distinguishes_no_coverage_from_error() -> None:
    evidence = SourceEvidence(
        source="future_sensor",
        status=EvidenceStatus.NO_COVERAGE,
        coverage=0.0,
        warnings=("No observations intersect the requested area.",),
    )

    assert evidence.status is EvidenceStatus.NO_COVERAGE
    assert evidence.observations == ()
    assert evidence.coverage == 0.0


class AvailableProvider:
    source: ClassVar[str] = "available_provider"

    def collect_evidence(self, geometry, analysis_period) -> SourceEvidence:
        assert geometry == GEOMETRY
        assert analysis_period == PERIOD
        return SourceEvidence(
            source=self.source,
            status=EvidenceStatus.AVAILABLE,
            observed_at=OBSERVED_AT,
            provenance={"provider": self.source},
        )


class FailingProvider:
    source: ClassVar[str] = "failing_provider"

    def collect_evidence(self, geometry, analysis_period) -> SourceEvidence:
        raise RuntimeError("sensitive provider detail")


def test_provider_contract_and_orchestrator_collect_available_evidence() -> None:
    result = MultisourceOrchestrator([AvailableProvider()]).collect(GEOMETRY, PERIOD)

    assert len(result) == 1
    assert result[0].source == "available_provider"
    assert result[0].status is EvidenceStatus.AVAILABLE


def test_orchestrator_is_fail_soft_and_preserves_other_providers() -> None:
    result = MultisourceOrchestrator(
        [AvailableProvider(), FailingProvider()]
    ).collect(GEOMETRY, PERIOD)

    assert [item.status for item in result] == [
        EvidenceStatus.AVAILABLE,
        EvidenceStatus.ERROR,
    ]
    failed = result[1]
    assert failed.source == "failing_provider"
    assert failed.provenance == {
        "provider": "failing_provider",
        "error_type": "RuntimeError",
    }
    assert failed.warnings == (
        "Auxiliary provider failed while collecting evidence.",
    )
    assert "sensitive provider detail" not in repr(failed)
