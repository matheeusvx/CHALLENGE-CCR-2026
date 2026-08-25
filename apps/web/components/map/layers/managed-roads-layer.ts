import type { DataDrivenPropertyValueSpecification, ExpressionSpecification, FilterSpecification, Map as MapLibreMap } from "maplibre-gl";
import { FALLBACK_ROAD_COLOR } from "@/stores/road-color-store";

export const MANAGED_ROADS_LAYER_IDS = {
  source: "motiva-managed-roads",
  casing: "motiva-roads-casing",
  line: "motiva-roads-line",
} as const;

// ---------------------------------------------------------------------------
// Road identity expression – normalizes road_ref / road_id to a string key.
// MapLibre "coalesce" returns the first non-null value; "to-string" ensures
// the result is always a string even if the underlying property were numeric.
// ---------------------------------------------------------------------------

const ROAD_KEY_EXPR: ExpressionSpecification = [
  "to-string",
  ["coalesce", ["get", "road_ref"], ["get", "road_id"], ""],
];

// ---------------------------------------------------------------------------
// Filter builder – exported so it can be unit-tested independently.
// ---------------------------------------------------------------------------

/**
 * Build a valid MapLibre **expression filter** for road visibility.
 *
 * Returns:
 * - `null`                 when all roads are visible (no filter needed)
 * - `["==", true, false]`  when zero roads are visible (constant-false)
 * - `["match", roadKeyExpr, [...keys], true, false]`  for a subset
 *
 * Using "match" avoids the ambiguity with MapLibre's legacy filter
 * interpretation of "in" / "==" where the second element is expected to
 * be a property-name string.
 */
export function buildRoadVisibilityFilter(
  visibleKeys: string[],
  totalRoads: number,
): FilterSpecification | null {
  // All visible → no filter at all
  if (visibleKeys.length >= totalRoads) return null;

  // None visible → constant false expression
  // ["==", ["to-string", ...], ""] would match features with empty keys; instead
  // use a safe boolean literal that cannot be mis-parsed as legacy filter.
  if (visibleKeys.length === 0) {
    // This is an expression-form filter: the first element is an operator that
    // takes expression-typed arguments. "all" with an impossible child works,
    // but the simplest valid constant-false expression is:
    //   ["==", ["literal", true], false]
    // However MapLibre may still trip. The safest approach is a match with an
    // empty labels array:
    //   ["match", roadKeyExpr, "__IMPOSSIBLE__", true, false]
    // which will always fall through to the default (false).
    return [
      "match",
      ROAD_KEY_EXPR,
      "__$$MATCH_NOTHING$$__",
      true,
      false,
    ] as unknown as FilterSpecification;
  }

  // Subset visible → use "match"
  // MapLibre "match" syntax: ["match", input, label1, output1, label2, output2, ..., default]
  // Labels can be a single value or an array of values that share the same output.
  // Using an array of labels: ["match", input, [labels...], true, false]
  return [
    "match",
    ROAD_KEY_EXPR,
    visibleKeys,   // array of labels → all map to the next output (true)
    true,
    false,         // default output
  ] as unknown as FilterSpecification;
}

// ---------------------------------------------------------------------------
// Main install / update entry-point
// ---------------------------------------------------------------------------

/**
 * Installs the GeoJSON source and road layers on first call, then efficiently
 * updates paint properties and the visibility filter on subsequent calls.
 *
 * Does NOT recreate the source or layers when toggling visibility or colors.
 */
export function installOrUpdateManagedRoadsLayer(
  map: MapLibreMap,
  activeColors: Record<string, string>,
  activeVisibility: Record<string, boolean>,
): void {
  // ── Color expression ────────────────────────────────────────────────────
  const colorMatch = [
    "match",
    ROAD_KEY_EXPR,
    ...Object.entries(activeColors).flatMap(([key, color]) => [key, color]),
    FALLBACK_ROAD_COLOR,
  ] as unknown as DataDrivenPropertyValueSpecification<string>;

  // ── Visibility filter ───────────────────────────────────────────────────
  const visibleKeys = Object.entries(activeVisibility)
    .filter(([, v]) => v)
    .map(([k]) => String(k)); // ensure string keys

  const totalRoads = Object.keys(activeVisibility).length;
  const visibilityFilter = buildRoadVisibilityFilter(visibleKeys, totalRoads);

  // ── Source ──────────────────────────────────────────────────────────────
  if (!map.getSource(MANAGED_ROADS_LAYER_IDS.source)) {
    map.addSource(MANAGED_ROADS_LAYER_IDS.source, {
      type: "geojson",
      data: "/data/motiva-sp-roads.geojson",
      tolerance: 0.5,
    });
  }

  // ── Casing layer ───────────────────────────────────────────────────────
  if (!map.getLayer(MANAGED_ROADS_LAYER_IDS.casing)) {
    map.addLayer({
      id: MANAGED_ROADS_LAYER_IDS.casing,
      type: "line",
      source: MANAGED_ROADS_LAYER_IDS.source,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 3, 10, 6, 15, 12] as ExpressionSpecification,
        "line-opacity": 0.8,
      },
    });
  }
  // Apply / clear filter after layer exists
  map.setFilter(MANAGED_ROADS_LAYER_IDS.casing, visibilityFilter);

  // ── Colored line layer ─────────────────────────────────────────────────
  if (!map.getLayer(MANAGED_ROADS_LAYER_IDS.line)) {
    map.addLayer({
      id: MANAGED_ROADS_LAYER_IDS.line,
      type: "line",
      source: MANAGED_ROADS_LAYER_IDS.source,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": colorMatch,
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.5, 10, 3, 15, 6] as ExpressionSpecification,
        "line-opacity": 0.9,
      },
    });
  } else {
    map.setPaintProperty(MANAGED_ROADS_LAYER_IDS.line, "line-color", colorMatch);
  }
  // Apply / clear filter after layer exists
  map.setFilter(MANAGED_ROADS_LAYER_IDS.line, visibilityFilter);
}
