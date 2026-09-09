# Sentinel-1 operational fusion engineering

Engineering readiness is not scientific authorization. The runtime supports
four explicit `MULTISOURCE_FUSION_MODE` values, with `disabled` as the default:

- `disabled`: no Sentinel-1 collection and no review or fusion effect;
- `shadow`: rule B can emit a review-only signal and never changes Sentinel-2;
- `experimental`: a separate, explicitly non-operational V1 policy may publish
  an additive multisource recommendation; it never supplies operational authority;
- `operational`: collects the same evidence, but an independent holdout artifact
  must pass the hard authorization gate before any future policy may be evaluated.

Operational mode additionally requires `MULTISOURCE_ENABLED=true`,
`SENTINEL1_ENABLED=true`, and `SENTINEL1_TEMPORAL_ENABLED=true`. These flags are
necessary but not sufficient. `VALIDATION_HOLDOUT_BENCHMARK_PATH` must point to
a compatible artifact with a frozen/evaluated preregistration, unchanged rule B
and engineering gates, matching dataset/preregistration fingerprints, internally
consistent sample-level metrics, no invalidating integrity warning, and
`recommendation_gate.status=REVIEW_MODE_CANDIDATE`.

Authorization is fail-closed: missing, corrupt, incompatible, insufficient or
shadow-only artifacts deny operational evaluation. The Sentinel-2 pipeline is
separately fail-soft and continues normally if Sentinel-1 or authorization fails.

## Audit contract

`multisource.operational_fusion` is additive and uses schema `1.0`:

```json
{
  "schema_version": "1.0",
  "requested": true,
  "authorized": false,
  "policy_available": false,
  "authorization_status": "denied",
  "authorization_reason": "holdout_gate_not_satisfied",
  "holdout_schema_version": "1.0",
  "holdout_gate_status": "SHADOW_REVIEW_CANDIDATE",
  "candidate_rule": "B",
  "policy_version": null,
  "original_recommendation": "cortar",
  "final_recommendation": "cortar",
  "official_recommendation_changed": false
}
```

`OperationalFusionAuthorization` only validates authorization.
`OperationalFusionPolicy` is a protocol for a future separately preregistered
policy. No implementation of that policy exists in this stage. Consequently,
even a synthetic test artifact that authorizes the layer reports
`policy_available=false` and cannot change the recommendation. Rule B remains a
shadow review signal, not an override rule.

`ExperimentalFusionPolicyV1` is a separate type and is never selected by the
operational path. Its contract is documented in
`docs/sentinel1_experimental_fusion.md`.

Current scientific state: the development dataset has 23 samples and the
independent holdout has zero. Therefore operational fusion is blocked even
though the engineering path is ready.
