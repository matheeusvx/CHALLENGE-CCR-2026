"""Shared immutable contract for the independent rule-B holdout gate."""

HOLDOUT_SCHEMA_VERSION = "1.0"
HOLDOUT_CANDIDATE_RULE = "B"
HOLDOUT_RULE_DEFINITION = {
    "s2_recommendation": "cortar",
    "sentinel1_evidence_required": "available",
    "sentinel1_temporal_required": "completed",
    "sentinel1_temporal_combined_status": "mixed",
    "output": "review_signal",
}
HOLDOUT_ENGINEERING_GATES = {
    "minimum_binary_truths": 50,
    "minimum_s2_errors": 10,
    "error_capture_wilson_lower_min": 0.50,
    "false_review_rate_wilson_upper_max": 0.20,
    "review_precision_wilson_lower_min": 0.33,
    "trigger_rate_max": 0.25,
    "leave_one_out_gate_invariant_required": True,
}
HOLDOUT_INCLUSION_CRITERIA = [
    "validation_sample_cohort_equals_holdout",
    "immutable_validation_snapshot_available",
    "unique_sample_id_analysis_id_and_aoi_fingerprint",
]
HOLDOUT_EXCLUSION_CRITERIA = [
    "development_cohort",
    "duplicate_analysis_or_aoi",
    "uncertain_truth_from_supervised_metrics",
    "soak_repetition_from_scientific_sample_count",
]

