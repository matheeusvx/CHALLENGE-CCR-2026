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


def test_new_observation_is_holdout_even_if_input_requests_training() -> None:
    observation = FieldObservation.from_mapping(
        {
            "sample_id": "field-v1",
            "height_measurements_cm": [12, 14, 16],
            "photo_references": ["photo-01.jpg"],
            "training_eligible": True,
            "external_validation": False,
        }
    )
    assert observation.height_measurements_cm == (12, 14, 16)
    assert observation.measurements_cm == (12, 14, 16)
    assert observation.photo_references == ("photo-01.jpg",)
    assert observation.training_eligible is False
    assert observation.external_validation is True
