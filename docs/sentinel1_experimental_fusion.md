# Experimental Fusion V1

Experimental Fusion V1 evaluates Sentinel-1 as validation/review evidence while
keeping Sentinel-2 as the primary decision source. It is not an operationally
authorized fusion policy.

Enable it with all four settings:

```text
MULTISOURCE_ENABLED=true
SENTINEL1_ENABLED=true
SENTINEL1_TEMPORAL_ENABLED=true
MULTISOURCE_FUSION_MODE=experimental
```

The existing API `recommendation` keeps its Sentinel-2 meaning for backward
compatibility. Consumers may read the additive audit at
`multisource.experimental_fusion`. `final_recommendation` and the compatible
`multisource_recommendation` alias both preserve the Sentinel-2 result.

## Policy `experimental_v1`

There is one review rule:

```text
Sentinel-2 = cortar
and calibrated Sentinel-1 temporal combined_status = mixed
-> multisource_disagreement = true
-> review_recommended = true
-> final recommendation remains cortar
```

Sentinel-1 never converts an objective `cortar` or `nao_cortar` decision to
another class or to `inconclusivo`. `sentinel1_influenced_decision` therefore
remains false. Rule B, its reason and the temporal validation status remain
auditable. Every other combination preserves the Sentinel-2 result without a
review trigger. Rules A, C and A+B are not present.

`mixed` means VV and VH exhibit divergent radiometric temporal behavior under
the existing calibrated sigma0, canonical-relative-orbit analysis. It does not
mean low vegetation, high vegetation, growth, biomass, height or a physical need
to cut.

Unavailable Sentinel-1, remote failures, incomplete calibration, invalid
temporal support and `insufficient_data` are fail-soft: the multisource result
falls back to Sentinel-2 and the analysis continues.

The two benchmark error patterns illustrate the intentional limitation:

- `S2=cortar, S1=mixed` remains `cortar` and receives an internal review signal;
- `S2=nao_cortar, S1=increasing` remains `nao_cortar`, because rule A was not
  approved for this policy.

When Sentinel-2 is `inconclusivo`, V1 also remains `inconclusivo`: current
Sentinel-1 evidence does not support inventing a binary decision.

## Separation from operational fusion

- Experimental fusion: available, always marked `experimental=true` and
  `operationally_authorized=false`.
- Operational fusion engineering: ready.
- Operational authorization: blocked pending a valid independent holdout and a
  separately preregistered operational policy.

`ExperimentalFusionPolicyV1` does not implement or inherit
`OperationalFusionPolicy`. Selecting `operational` never activates the
experimental policy.
