import type { Feature, FeatureCollection, Polygon } from "geojson";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import type { ViewportBounds } from "@/lib/schemas/analyses";
import type { AutoAnalysisUIStatus } from "@/stores/auto-analysis-store";

export const CANONICAL_AOI_LAYER_IDS = {
  source: "canonical-aoi-source",
  fill: "canonical-aoi-fill",
  outline: "canonical-aoi-outline",
} as const;

export type CanonicalVisualState = {
  color: string;
  fillOpacity: number;
  lineWidth: number;
  dashed: boolean;
};

export function getCanonicalVisualState(
  status: AutoAnalysisUIStatus,
  decision?: "cortar" | "nao_cortar" | "inconclusivo",
): CanonicalVisualState {
  if (status === "analyzing" || status === "stabilizing") {
    return {
      color: "#2563eb",
      fillOpacity: 0.1,
      lineWidth: 2.5,
      dashed: true,
    };
  }
  if (status === "failed") {
    return {
      color: "#dc2626",
      fillOpacity: 0.08,
      lineWidth: 2,
      dashed: true,
    };
  }
  if (status === "cache_hit" || status === "completed") {
    if (decision === "cortar") {
      return { color: "#d44d2f", fillOpacity: 0.16, lineWidth: 2.5, dashed: false };
    }
    if (decision === "inconclusivo") {
      return { color: "#a77919", fillOpacity: 0.14, lineWidth: 2.5, dashed: true };
    }
    return { color: "#27865b", fillOpacity: 0.16, lineWidth: 2.5, dashed: false };
  }
  return {
    color: "#6b7280",
    fillOpacity: 0.08,
    lineWidth: 1.5,
    dashed: true,
  };
}

export function boundsToPolygonCoordinates(bounds: ViewportBounds): [number, number][][] {
  const { west, south, east, north } = bounds;
  return [
    [
      [west, south],
      [east, south],
      [east, north],
      [west, north],
      [west, south],
    ],
  ];
}

export function createCanonicalAoiFeatureCollection(
  bounds: ViewportBounds,
): FeatureCollection<Polygon> {
  const feature: Feature<Polygon> = {
    type: "Feature",
    id: "canonical-tile-aoi",
    geometry: {
      type: "Polygon",
      coordinates: boundsToPolygonCoordinates(bounds),
    },
    properties: {
      tile: true,
    },
  };
  return {
    type: "FeatureCollection",
    features: [feature],
  };
}

export function installOrUpdateCanonicalAoiLayer(
  map: MapLibreMap,
  bounds: ViewportBounds | null,
  status: AutoAnalysisUIStatus,
  decision?: "cortar" | "nao_cortar" | "inconclusivo",
): void {
  if (
    !bounds ||
    status === "disabled" ||
    status === "zoom_required" ||
    status === "invalid_viewport" ||
    status === "completed" ||
    status === "cache_hit" ||
    status === "idle"
  ) {
    removeCanonicalAoiLayer(map);
    return;
  }

  const visualState = getCanonicalVisualState(status, decision);
  const data = createCanonicalAoiFeatureCollection(bounds);
  const source = map.getSource(CANONICAL_AOI_LAYER_IDS.source);

  if (source) {
    (source as GeoJSONSource).setData(data);
  } else {
    map.addSource(CANONICAL_AOI_LAYER_IDS.source, { type: "geojson", data });
  }

  if (!map.getLayer(CANONICAL_AOI_LAYER_IDS.fill)) {
    map.addLayer({
      id: CANONICAL_AOI_LAYER_IDS.fill,
      type: "fill",
      source: CANONICAL_AOI_LAYER_IDS.source,
      paint: {
        "fill-color": visualState.color,
        "fill-opacity": visualState.fillOpacity,
      },
    });
  } else {
    map.setPaintProperty(CANONICAL_AOI_LAYER_IDS.fill, "fill-color", visualState.color);
    map.setPaintProperty(CANONICAL_AOI_LAYER_IDS.fill, "fill-opacity", visualState.fillOpacity);
  }

  if (!map.getLayer(CANONICAL_AOI_LAYER_IDS.outline)) {
    map.addLayer({
      id: CANONICAL_AOI_LAYER_IDS.outline,
      type: "line",
      source: CANONICAL_AOI_LAYER_IDS.source,
      paint: {
        "line-color": visualState.color,
        "line-width": visualState.lineWidth,
        "line-dasharray": visualState.dashed ? [2, 2] : [1, 0.01],
      },
    });
  } else {
    map.setPaintProperty(CANONICAL_AOI_LAYER_IDS.outline, "line-color", visualState.color);
    map.setPaintProperty(CANONICAL_AOI_LAYER_IDS.outline, "line-width", visualState.lineWidth);
    map.setPaintProperty(
      CANONICAL_AOI_LAYER_IDS.outline,
      "line-dasharray",
      visualState.dashed ? [2, 2] : [1, 0.01],
    );
  }
}

export function removeCanonicalAoiLayer(map: MapLibreMap): void {
  if (map.getLayer(CANONICAL_AOI_LAYER_IDS.outline)) {
    map.removeLayer(CANONICAL_AOI_LAYER_IDS.outline);
  }
  if (map.getLayer(CANONICAL_AOI_LAYER_IDS.fill)) {
    map.removeLayer(CANONICAL_AOI_LAYER_IDS.fill);
  }
  if (map.getSource(CANONICAL_AOI_LAYER_IDS.source)) {
    map.removeSource(CANONICAL_AOI_LAYER_IDS.source);
  }
}
