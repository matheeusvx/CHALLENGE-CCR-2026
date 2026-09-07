# Sentinel-1 evidence summary contract

The additive Sentinel-1 `summary` object is versioned independently from the
detailed evidence payload. Its current `schema_version` is `"1.0"`. Existing
detailed metrics and observations remain the scientific audit source.

| Field | Type / nullability | Unit | Meaning |
|---|---|---|---|
| `schema_version` | string, required | none | Summary contract version; currently `1.0`. |
| `availability` | string, required | none | Provider status: `available`, `no_coverage`, `unavailable`, `error`, or `disabled`. |
| `quality_score` | number or null | percent, 0–100 | Existing S1 evidence quality score; null when it cannot be calculated. |
| `canonical_relative_orbit` | integer or null | orbit number | Canonical `sat:relative_orbit`; null when no valid orbit is available. |
| `observation_count` | integer | observations | Accepted S1 observations before canonical-orbit temporal filtering. |
| `calibrated_observation_count` | integer | observations | Accepted observations with complete VV/VH calibration for the available polarizations. |
| `temporal_usable_observation_count` | integer | UTC days | Minimum number of calibrated canonical-orbit daily observations usable jointly by VV and VH. |
| `temporal_status` | string | none | `increasing`, `decreasing`, `stable`, `mixed`, `insufficient_data`, or `disabled`. |
| `vv_change_db` | number or null | dB | Robust modeled VV change over the analyzed span; null without safe support. |
| `vh_change_db` | number or null | dB | Robust modeled VH change over the analyzed span; null without safe support. |
| `processing_duration_ms` | number | milliseconds | Total provider duration, including discovery and raster/calibration work. |
| `warnings` | array of strings | none | Deduplicated provider and temporal audit warnings. |
| `limitations` | array of strings | none | Stable machine-readable limitations applicable to this evidence. |

## Interpretation

Temporal statuses describe calibrated sigma0 radiometric behavior in dB from
the canonical relative orbit only. They do not represent vegetation growth,
biomass, height, maintenance need, or a cutting decision. `mixed` means VV and
VH have different supported statuses. `insufficient_data` means the minimum
observation count, span, finite calibrated values, or gap safety criteria were
not satisfied.

The summary is evidence-only and shadow-only. It cannot change
`cortar`, `nao_cortar`, `inconclusivo`, confidence, or the official
recommendation. Terrain correction/RTC and physical attribution are not
performed. Consumers needing calibration details, per-observation provenance,
or support diagnostics must use the unchanged detailed payload.

## Compatibility

Fields in schema `1.0` are required in the summary object, while the explicitly
nullable values above may be JSON `null`. Additive detailed metrics do not
change this contract. Removing or changing the meaning/type/unit of a summary
field requires a new schema version.
