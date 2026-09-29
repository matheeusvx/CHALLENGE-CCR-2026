# Experimental Fusion V1

Experimental Fusion V1 lets Sentinel-1 influence an explicitly separate
multisource recommendation for research and demonstration. It is not an
operationally authorized recommendation.

Enable it with all four settings:

```text
MULTISOURCE_ENABLED=true
SENTINEL1_ENABLED=true
SENTINEL1_TEMPORAL_ENABLED=true
MULTISOURCE_FUSION_MODE=experimental
```

The existing API `recommendation` keeps its Sentinel-2 meaning for backward
compatibility. Consumers that intentionally opt into the experiment may read
`multisource.experimental_fusion.multisource_recommendation`.

## Policy `experimental_v1`

There is one rule:

```text
Sentinel-2 = cortar
and calibrated Sentinel-1 temporal combined_status = mixed
-> experimental multisource recommendation = inconclusivo
```

The original Sentinel-2 recommendation remains `cortar`. The policy does not
convert this case to `nao_cortar`; it only increases uncertainty. Every other
combination preserves the Sentinel-2 result. Rules A, C and A+B are not present.

`mixed` means VV and VH exhibit divergent radiometric temporal behavior under
the existing calibrated sigma0, canonical-relative-orbit analysis. It does not
mean low vegetation, high vegetation, growth, biomass, height or a physical need
to cut.

Unavailable Sentinel-1, remote failures, incomplete calibration, invalid
temporal support and `insufficient_data` are fail-soft: the multisource result
falls back to Sentinel-2 and the analysis continues.

The two benchmark error patterns illustrate the intentional limitation:

- `S2=cortar, S1=mixed` becomes experimental `inconclusivo`, never `no_cut`;
- `S2=nao_cortar, S1=increasing` remains `nao_cortar`, because rule A was not
  approved for this policy.

## Separation from operational fusion

- Experimental fusion: available, always marked `experimental=true` and
  `operationally_authorized=false`.
- Operational fusion engineering: ready.
- Operational authorization: blocked pending a valid independent holdout and a
  separately preregistered operational policy.

`ExperimentalFusionPolicyV1` does not implement or inherit
`OperationalFusionPolicy`. Selecting `operational` never activates the
experimental policy.

