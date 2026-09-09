# Sentinel-1 shadow review

Sentinel-1 is operationally available as complementary evidence using calibrated
sigma0 in dB, canonical relative-orbit normalization and temporal analysis. It
does not change the official Sentinel-2 recommendation.

## Runtime rule

The runtime contains exactly one review hypothesis, benchmark rule B:

```text
official Sentinel-2 recommendation = cortar
AND Sentinel-1 evidence = available
AND Sentinel-1 temporal analysis = completed
AND Sentinel-1 combined temporal status = mixed
=> review signal B
```

`mixed` is a radiometric relationship between supported VV and VH temporal
statuses. It is not interpreted as growth, biomass, height or cutting need.

The feature remains conservative by default. `MULTISOURCE_ENABLED=false`,
`SENTINEL1_ENABLED=false`, `SENTINEL1_TEMPORAL_ENABLED=false`, or
`MULTISOURCE_FUSION_MODE=disabled` prevents review evaluation. No operational
fusion/override mode is accepted.

## Additive contract

`multisource.review` has schema version `1.0`:

| Field | Meaning |
|---|---|
| `review_evaluable` | Required S1 evidence and valid temporal support exist. |
| `review_evaluated` | Rule B was evaluated. |
| `review_recommended` | Rule B triggered a review signal. |
| `review_rule` | `B` only when triggered; otherwise null. |
| `review_reason` | Trigger reason or `rule_b_conditions_not_met`; null when not evaluable. |
| `review_source` | Always `sentinel1`. |
| `sentinel1_temporal_status` | Audited combined radiometric status, or null. |
| `review_not_evaluable_reason` | Stable fail-soft reason, or null. |
| `official_recommendation_changed` | Always `false`. |

Structured logs contain only `review_evaluated`, `review_triggered`,
`review_rule`, `review_not_evaluable_reason`, and
`official_recommendation_changed`. They do not contain observations, raster
payloads, stack traces, geometry or ground truth.
