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


def test_confirmed_lower_bound_is_strong_for_classification_only() -> None:
    observation = FieldObservation.from_mapping(
        {
            "sample_id": "GT35-01",
            "measurement_type": "threshold_lower_bound",
            "measured_height_cm": None,
            "height_lower_bound_cm": 35,
            "confirmed_above_lower_bound": True,
            "training_eligible": True,
            "external_validation": False,
        }
    )
    assert observation.measured_height_cm is None
    assert observation.height_mean_cm is None
    assert observation.height_p50_cm is None
    assert observation.height_p90_cm is None
    assert observation.regulatory_gt30 is True
    assert observation.experimental_certainty_zone == "CLEAR_POSITIVE"
    assert observation.classification_ground_truth_strength == "strong"
    assert observation.metric_regression_eligible is False
    assert observation.training_eligible is False
    assert observation.external_validation is True
