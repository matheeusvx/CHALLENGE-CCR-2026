# Sentinel-1 exploratory temporal analysis

The runtime adds `sources[].metrics.temporal_analysis` to Sentinel-1 evidence.
The provider, calibration, canonical selection and official recommendation are
unchanged. API and artifact serialization use the existing evidence envelope.

`MonitoringConfig.sentinel1_temporal` contains a `Sentinel1TemporalConfig`.
Its default factory reads environment variables when each analysis configuration
is created (including API executions). Programmatic callers may supply it explicitly.

| Environment variable | Default |
| --- | --- |
| SENTINEL1_TEMPORAL_ENABLED | false |
| SENTINEL1_TEMPORAL_MIN_OBSERVATIONS | 4 |
| SENTINEL1_TEMPORAL_MIN_SPAN_DAYS | 18 |
| SENTINEL1_TEMPORAL_MIN_TOTAL_CHANGE_DB | 1.0 |
| SENTINEL1_TEMPORAL_RESIDUAL_MAD_MULTIPLIER | 2.0 |

Only observations matching the already selected canonical relative orbit enter
the module. Each channel requires its own `*_radiometric_calibration_status` to
equal `calibrated`, and a finite `*_sigma0_median_db`. Overall `partial` status
does not invalidate a separately calibrated channel. Missing channel status is
not inferred from raw amplitude or the overall status. Unknown orbits and naive
timestamps are excluded. No replacement canonical orbit is selected.

Timestamps are converted to UTC and grouped by date. Daily channel values are
medians of valid items, not medians across polarizations. `item_count` counts all
canonical items on that day, including items invalid for one or both channels;
`vv_item_count` and `vh_item_count` identify channel support. Dates with no valid
channel value remain auditable in `series`, but do not count toward channel
support. Channel `observation_count` counts valid days and `span_days` spans its
first to last valid day. The existing acquisition period and scene limit apply.

For each channel, time is elapsed integer UTC days. The Theil-Sen slope is the
median of all distinct-day pair slopes. The intercept is `median(y - slope*x)`.
Residual MAD is `median(abs(residual - median(residual)))`, without scaling.
Modeled change is slope times span, while endpoint delta is the last observed
daily dB minus the first. Direction agreement is the fraction of all pair slopes
having the same sign as the fitted slope; zero slopes agree only with zero.

Below minimum day count or span, status is `insufficient_data`, fitted metrics
are null. Otherwise the effective threshold is the maximum of the configured
minimum total change and the MAD multiplier times residual MAD. Absolute modeled
change strictly below threshold is `stable`; at or above threshold its sign
determines `increasing` or `decreasing`. This threshold is exploratory and has no
operational role.

Matching channel classifications yield that combined status; differing valid
classifications yield `mixed`. Either insufficient channel makes the combination
`insufficient_data`. Top-level status is `disabled`, `insufficient_data`, or
`completed` (an unexpected runtime failure is `error` and preserves source evidence).

Interpretation is `radiometric_change_only`, with physical attribution `none`.
No growth, biomass, height, density, maintenance need, or causal interpretation
is produced. Calibration is not RTC and no extra noise or orbit correction is
applied. Sparse observations, changing conditions and differing VV/VH valid
dates remain limitations. These exploratory settings are not scientific
vegetation thresholds.
