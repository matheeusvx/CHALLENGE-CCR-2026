# Sentinel-1 operational soak

The soak runner processes each AOI independently using the hardened Sentinel-1
provider and current temporal analysis. It never runs fusion and never reads or
changes an operational recommendation.

## External AOI dataset

Preferred GeoJSON form:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "aoi-001",
      "properties": {},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[-47.0, -23.0], [-46.9, -23.0], [-46.9, -22.9], [-47.0, -23.0]]]
      }
    }
  ]
}
```

`properties.aoi_id` may replace Feature `id`. A JSON list of objects with
`aoi_id` and `geometry` is also accepted. IDs must be non-empty and unique;
geometry must be Polygon or MultiPolygon. Empty datasets are valid and produce
zero-valued summaries.

Validation geometries can be loaded with `--validation-db`. The SQLite file is
opened with `mode=ro`; the query selects only `sample_id`, `analysis_id`, and
`snapshot_json`, then extracts the AOI geometry locally. Dedicated ground-truth,
vegetation-class, notes, and recommendation columns are not selected. Only the
extracted geometry and requested period are passed to the S1 provider/STAC.
Legacy snapshots without geometry are recovered read-only from matching
`summary.json`/`aoi.geojson` artifacts under `--analysis-output-root`.

## Execution

```powershell
.\.venv\Scripts\python.exe scripts\run_sentinel1_soak.py `
  --aois data\sentinel1_soak_aois.geojson `
  --start-date 2026-08-01 `
  --end-date 2026-08-31 `
  --repetitions 3 `
  --output-dir outputs\sentinel1-soak\2026-08
```

For persisted validation geometries:

```powershell
.\.venv\Scripts\python.exe scripts\run_sentinel1_soak.py `
  --validation-db data\validation\validation.sqlite3 `
  --start-date 2026-08-01 `
  --end-date 2026-08-31 `
  --repetitions 3 `
  --resume `
  --output-dir outputs\sentinel1-soak\validation-2026-08
```

The supplied period is inclusive. Repetitions run in deterministic AOI order
and are intended to reveal service and latency variability, not to optimize a
scientific threshold.

Progress is enabled by default. Each run prints its planned index, AOI,
repetition, completion status, duration, availability and temporal status,
followed by elapsed time, observed mean duration and a descriptive ETA. Use
`--quiet` to suppress progress output.

After every completed run, JSON and CSV are written to temporary files in the
output directory and atomically replaced. The partial summary is written last
with `"complete": false`; the final write changes it to `true`. Ctrl+C repeats
the partial flush, reports the number of preserved runs, and exits with code
130.

Use `--resume` to load a compatible `sentinel1_soak_runs.json`. Completed runs
are identified by AOI ID, repetition and analysis-period dates. Duplicate,
incomplete, differently versioned or out-of-plan records are rejected. Without
`--resume`, existing soak artifacts cause a safe error instead of overwrite.

## Artifacts and metrics

- `sentinel1_soak_runs.json`: complete record per AOI/repetition.
- `sentinel1_soak_runs.csv`: one flat row per AOI/repetition; warnings and
  failure counts are deterministic compact JSON strings.
- `sentinel1_soak_summary.json`: availability/calibration/temporal rates,
  successful/partial/failed counts, orbit/status distributions, failure
  categories, and min/median/p90/p95/max latency.

`successful` means S1 is available, calibration has no recorded failure, and
temporal status is usable. `partial` means S1 is available but calibration is
partial or temporal support is unavailable. `failed` means S1 is not available
for that run. Availability remains separately recorded.

Performance is measured in the existing single pass: STAC discovery, raster
processing, calibration, temporal analysis, and total duration. Measurement
assets are not reopened for timing. Percentiles use linear interpolation
(type 7), and rates use percent units.

Gap reporting is descriptive. It records the largest VV/VH gaps, the number of
channel series rejected by `excessive_gap`, and runs that would otherwise meet
temporal support. The configured 24-day limit is not changed or optimized.
