# AUTO-01 — automatic analysis by viewport

AUTO-01 adds only backend infrastructure. The manual polygon endpoint remains
available and no map listener, debounce, overlay or frontend behavior is part of
this checkpoint.

## Endpoint

`POST /api/analyses/automatic`

```json
{
  "bounds": {"west": -46.9625, "south": -23.1095, "east": -46.9595, "north": -23.1065},
  "zoom": 17,
  "center": {"lng": -46.961, "lat": -23.108},
  "force_refresh": false
}
```

The response distinguishes `disabled`, `zoom_required`, `invalid_viewport`,
`analysis_started`, `in_progress`, `cache_hit` and `failed`. A cache hit embeds
the existing `AnalysisResponse` contract with
`analysis_trigger="automatic_viewport"`. The manual endpoint emits
`analysis_trigger="manual"`.

## Canonical AOI and spatial key

The default `tile_v1` strategy maps the validated viewport center to a Web Mercator
tile at zoom 17. The tile polygon becomes the AOI and the key is
`tile:17/{x}/{y}`. Small pan/zoom changes inside the same tile reuse the same
key.

## AUTO-03C roadside AOI

`AUTO_ANALYSIS_SPATIAL_STRATEGY=roadside_v1` explicitly enables the first
road-aware checkpoint. It loads the local ARTESP-derived GeoJSON once, builds a
Shapely spatial index once, and accepts only features whose
`geometry_status=valid` and `road_id` is non-null. No road data is downloaded at
runtime.

The viewport supplies only a point of interest. Zoom 13 and above is accepted;
viewport area and dimensions do not define or reject the scientific AOI. The
backend snaps the center within 50 m, rejects competing axes within 10 m, and
resolves a deterministic 300 m section using fixed chainage boundaries.

For the canonical section, the backend creates two axis-relative strips. Each
starts 25 m from the centerline and extends another 40 m. The central roadway
region is therefore intentionally absent. Because the source has no trustworthy
direction, the strips are named `side_a` and `side_b`, never left/right. Their
union is the `both` AOI sent to the existing Sentinel pipeline. The defaults
require at least 5,000 m² total and an effective width of at least 20 m on each
side. Short terminal sections pass only when these same gates are satisfied.
The published audit includes the roadway exclusion polygon, total and per-side
areas, effective widths, minimum distance to the selected centerline, and the
measured exclusion overlap. The inner exclusion uses round endpoint caps to
avoid zero-area overlay spikes touching the line on diagonal real-world
segments; the scientific strips themselves remain fixed-length axis-relative
buffers.

Other indexed road axes intersecting the tentative AOI are handled
conservatively. A reliable axis has its configured roadway buffer subtracted. An
intersecting axis with unreliable identity rejects the AOI instead of silently
including roadway. Curves and projection round-trips are topology-repaired, and
invalid or empty output is rejected before any Sentinel call.

Resolution is fail-closed: missing/corrupt data, ambiguous identity, multiple
plausible axes, or no road inside the snap distance produce an auditable status
and no Sentinel call. The current data has no trustworthy direction or
carriageway model, so `orientation_basis=unavailable`, `axis_model=unknown`, and
no left/right claim is made.

Roadside identities use
`roadside:v1:{provider}:{dataset_version}:{axis_id}:section:{index}:side:both:{profile_hash}`.
The profile fingerprint includes strategy/version, section length, roadway
exclusion, lateral width and the geometric gates. Changing those inputs cannot
reuse an older cache entry. Roadside and `tile_v1` keys cannot collide;
`tile_v1` remains unchanged and is still the default during migration.

The 23 existing validation AOIs range from approximately 0.0022 to 0.0679 km²,
with a mean near 0.0164 km². Around the project's operating latitude, a z17 tile
is of the same order of magnitude (~0.09 km²).

### Viewport do Operador != AOI Científica Analisada

The operator's viewport provides situational context. With `roadside_v1`, zoom
13–15 can show a useful road corridor while the scientific AOI remains the fixed
canonical section and its lateral strips. Broad viewport bounds are validated
for coordinate integrity, but do not become the AOI and are not rejected by the
tile area gate. With legacy `tile_v1`, the center still maps to the canonical z17
tile and the existing viewport guards continue to apply.

## Cache, staleness, and concurrency

SQLite stores compact run metadata and the JSON analysis response, but no raster
payload. Cache identity combines `spatial_key` and the resolved analysis period,
so a calendar-period change cannot silently reuse the prior period.

- completed results are fresh for 6 hours by default;
- pipeline failures are held for 5 minutes to prevent immediate retry storms;
- an in-progress lease expires after 30 minutes, above observed S1 runtimes;
- `force_refresh` bypasses a fresh result only after a 5-minute cooldown;
- at most two automatic analyses run concurrently by default.

A SQLite `BEGIN IMMEDIATE` claim, a partial unique index and a process lock make
same-key requests race-safe for both tile and roadside strategies. Only the
winner submits the existing scientific pipeline; followers receive
`in_progress`. `force_refresh`, TTLs and concurrency limits are shared. Expired
cache entries cause a new claim. Artifact URLs are not persisted because their
authorization registry is process-local.

## Scientific and multisource behavior

The automatic route builds `MonitoringConfig` through the same helper as the
manual route and calls the same `run_monitoring_analysis` service. Under
`roadside_v1`, `MonitoringConfig.geometry` is exactly the returned
`analyzed_geometry`, not the viewport or a tile. It does not duplicate
Sentinel-2, Sentinel-1, temporal analysis or fusion logic. With
`MULTISOURCE_FUSION_MODE=experimental`, the cached `AnalysisResponse` retains
the Sentinel-2 primary/final recommendation plus the additive Sentinel-1
disagreement and review audit.

Sentinel-1 remains fail-soft. A provider failure does not become a separate
automatic analyzer failure when the shared pipeline can still complete S2.

## Runtime diagnostics

Structured backend events distinguish the failure stage without placing stack
traces or provider details in the public response:

- road resolution or roadside construction: `gis_failure` plus an auditable
  roadside status/reason;
- a failed Sentinel-2 scientific pipeline: `sentinel2_pipeline_failure`;
- a fatal satellite-provider/STAC failure: `remote_stac_failure`;
- response validation, cache persistence, executor, or unexpected application
  failures retain separate categories.

A scientifically completed result keeps its actual pipeline status, including
`insufficient_observations`, in `pipeline_result_status`; it is not relabeled as
an API/runtime exception. Cached failed responses expose only the stable failure
category. Internal logs record the sanitized exception type/detail and no AOI
geometry, ground truth, or raster payload.

## Configuration

The feature defaults off. See `.env.example` for all `AUTO_ANALYSIS_*` values.
The server is authoritative for zoom, area, dimensions, snap, section length,
roadway exclusion, lateral width, geometric gates, TTLs, refresh cooldown and
concurrency.

## AUTO-02 frontend guidance

- debounce viewport changes and submit only after the map is stable;
- treat `spatial_key` as the stable UI identity;
- poll by repeating the same request while status is `in_progress`;
- render cached and newly completed results identically;
- expose force refresh as an explicit operator action, never on every pan;
- display canonical bounds so the operator can see the analyzed tile;
- render the Sentinel-2/final recommendation and treat
  `multisource.experimental_fusion.review_recommended` as an audit/review cue.
