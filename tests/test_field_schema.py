"""Compatibilidade do schema futuro com registros historicos minimos."""

from src.satellite_monitoring.datasets.field_schema import (
    FUTURE_PRIMARY_TARGET,
    FUTURE_UNCERTAINTY_ZONE,
    FieldObservation,
)


def test_historical_record_remains_compatible() -> None:
    observation = FieldObservation.from_mapping({"sample_id": "legacy-001"})
    assert observation.sample_id == "legacy-001"
    assert observation.height_p90_cm is None
    assert observation.training_eligible is False
    assert observation.external_validation is True
    assert FUTURE_PRIMARY_TARGET == "height_p90_cm > 30"
    assert FUTURE_UNCERTAINTY_ZONE["uncertainty_zone"] == (
        "25 < height_p90_cm < 35"
    )
