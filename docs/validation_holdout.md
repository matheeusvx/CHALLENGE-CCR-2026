# Independent validation holdout for Sentinel-1 rule B

This infrastructure collects and evaluates a future independent holdout without
changing the operational recommendation or the current shadow-review runtime.
It never promotes development samples automatically and never creates ground
truth.

## Cohorts and identity

Every validation sample has exactly one immutable `cohort`:

- `development` is the conservative default and is assigned to all legacy rows
  by database migration;
- `holdout` must be selected explicitly when a new validation sample is created.

The database already enforces unique `analysis_id`. It also stores a SHA-256 of
the exact canonical GeoJSON and rejects a duplicate geometry whenever either
record is a holdout sample. Development-only historical repetitions remain
compatible, but can never increase holdout N. A database trigger prevents a
sample's cohort from being changed after insertion.

## Preregistration lifecycle

The versioned preregistration has states `draft`, `collecting`, `frozen`, and
`evaluated`. It contains only rule B, the engineering gates, inclusion/exclusion
criteria, non-personal methodology notes, and frozen fingerprints.

```powershell
# 1. Create a new version; refuses to overwrite an existing file.
.\.venv\Scripts\python.exe scripts/manage_validation_holdout.py init `
  --preregistration outputs/validation-holdout/preregistration-v1.json `
  --methodology-note "Independent prospective validation cohort"

# 2. Explicitly record that prospective collection started.
.\.venv\Scripts\python.exe scripts/manage_validation_holdout.py collecting `
  --preregistration outputs/validation-holdout/preregistration-v1.json

# 3. Freeze rule, gates, membership and sample identities.
.\.venv\Scripts\python.exe scripts/manage_validation_holdout.py freeze `
  --validation-db data/validation/validation.sqlite3 `
  --preregistration outputs/validation-holdout/preregistration-v1.json

# 4. Evaluate only from frozen local inputs; no STAC request is made.
.\.venv\Scripts\python.exe scripts/manage_validation_holdout.py evaluate `
  --validation-db data/validation/validation.sqlite3 `
  --preregistration outputs/validation-holdout/preregistration-v1.json `
  --temporal-benchmark-json outputs/validation-holdout/temporal-holdout.json `
  --soak-runs outputs/sentinel1-soak/validation-2026-08-r3/sentinel1_soak_runs.json `
  --output outputs/validation-holdout/validation_holdout_benchmark_v1.json
```

Freeze fails on duplicate identities or a holdout without an AOI fingerprint.
After freeze, any change to rule B, gates, criteria, notes, timestamps, membership
or frozen sample identity invalidates the fingerprints. Evaluation refuses an
unfrozen or already evaluated preregistration, and output artifacts are never
overwritten silently. A changed design requires a new preregistration file.

## Artifact and gate

`validation_holdout_benchmark_v1.json` uses schema `1.0` and contains:

- sanitized preregistration and input SHA-256 fingerprints;
- holdout counts and distributions, with `uncertain` reported but excluded from
  supervised metrics;
- Sentinel-2 baseline;
- rule B intention-to-review and S1-available-only metrics;
- TP/FP/FN/TN, Wilson 95% intervals, specificity and balanced accuracy;
- leave-one-sample-out ranges and performance-gate invariance;
- an explicit gate status, warnings, and methodology.

The artifact contains no raw geometry, personal notes, or raster payload. Soak
runs have `scientific_weight=0` and are counted only as technical provenance.

Possible statuses are:

- `INSUFFICIENT_HOLDOUT_DATA` when minimum N or S2 error count is missing;
- `SHADOW_REVIEW_CANDIDATE` when the holdout is large enough but any performance
  or robustness gate fails;
- `REVIEW_MODE_CANDIDATE` only when every preregistered requirement passes.

Missing evidence is never approval. This artifact does not activate operational
fusion. The HTTP endpoint `GET /api/validation-holdout-benchmark` only reads the
configured `VALIDATION_HOLDOUT_BENCHMARK_PATH`; missing and invalid artifacts
return audit-friendly 404 and 503 responses respectively.
