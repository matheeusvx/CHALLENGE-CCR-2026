import type { Feature, FeatureCollection, Geometry, LineString, MultiPolygon, Polygon } from "geojson";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import turfBbox from "@turf/bbox";

export const ROADSIDE_LAYER_IDS = {
  source: "roadside-aoi-source",
  fill: "roadside-aoi-fill",
  outline: "roadside-aoi-outline",
  centerlineSource: "roadside-centerline-source",
  centerline: "roadside-centerline-line",
} as const;

export type RoadsideVisualState = {
  color: string;
  fillOpacity: number;
  lineWidth: number;
  dashed: boolean;
};

export function getRoadsideVisualState(
  status: "analyzing" | "completed" | "cache_hit" | "idle",
  decision?: "cortar" | "nao_cortar" | "inconclusivo" | null,
): RoadsideVisualState {
  if (status === "analyzing") {
    return {
      color: "#2563eb",
      fillOpacity: 0.12,
      lineWidth: 2.0,
      dashed: true,
    };
  }

  if (decision === "cortar") {
    return {
      color: "#d44d2f",
      fillOpacity: 0.2,
      lineWidth: 2.5,
      dashed: false,
    };
  }

  if (decision === "inconclusivo") {
    return {
      color: "#a77919",
      fillOpacity: 0.18,
      lineWidth: 2.5,
      dashed: true,
    };
  }

  return {
    color: "#27865b",
    fillOpacity: 0.2,
    lineWidth: 2.5,
    dashed: false,
  };
}

function normalizeToFeatureCollection<G extends Geometry>(raw: unknown): FeatureCollection<G> | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  if (obj.type === "FeatureCollection" && Array.isArray(obj.features)) {
    return obj as unknown as FeatureCollection<G>;
  }

  if (obj.type === "Feature" && obj.geometry) {
    return {
      type: "FeatureCollection",
      features: [obj as unknown as Feature<G>],
    };
  }

  if (obj.type === "Polygon" || obj.type === "MultiPolygon" || obj.type === "LineString") {
    return {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: obj as unknown as G,
        },
      ],
    };
  }

  return null;
}

export type RoadsideLayerData = {
  analyzedGeometry?: unknown;
  centerline?: unknown;
  sideAGeometry?: unknown;
  sideBGeometry?: unknown;
};

export function fitMapToRoadsideGeometry(
  map: Pick<MapLibreMap, "fitBounds">,
  analyzedGeometry: unknown,
): boolean {
  const featureCollection = normalizeToFeatureCollection<Polygon | MultiPolygon>(analyzedGeometry);
  if (!featureCollection || featureCollection.features.length === 0) return false;

  try {
    const [west, south, east, north] = turfBbox(featureCollection);
    if (
      ![west, south, east, north].every(Number.isFinite) ||
      west >= east ||
      south >= north ||
      south < -90 ||
      north > 90
    ) {
      return false;
    }
    map.fitBounds(
      [[west, south], [east, north]],
      {
        padding: { top: 72, right: 72, bottom: 72, left: 72 },
        maxZoom: 16,
        duration: 600,
      },
    );
    return true;
  } catch {
    return false;
  }
}

export function installOrUpdateRoadsideLayer(
  map: MapLibreMap,
  data: RoadsideLayerData,
  status: "analyzing" | "completed" | "cache_hit" | "idle",
  decision?: "cortar" | "nao_cortar" | "inconclusivo" | null,
): void {
  let aoiFc = normalizeToFeatureCollection<Polygon | MultiPolygon>(data.analyzedGeometry);
  if (!aoiFc && (data.sideAGeometry || data.sideBGeometry)) {
    const features: Feature<Polygon | MultiPolygon>[] = [];
    const sideAFc = normalizeToFeatureCollection<Polygon>(data.sideAGeometry);
    const sideBFc = normalizeToFeatureCollection<Polygon>(data.sideBGeometry);
    if (sideAFc?.features) features.push(...sideAFc.features);
    if (sideBFc?.features) features.push(...sideBFc.features);
    if (features.length > 0) {
      aoiFc = { type: "FeatureCollection", features };
    }
  }

  if (!aoiFc || aoiFc.features.length === 0) {
    removeRoadsideLayer(map);
    return;
  }

  const visual = getRoadsideVisualState(status, decision);

  const existingSource = map.getSource(ROADSIDE_LAYER_IDS.source) as GeoJSONSource | undefined;
  if (existingSource) {
    existingSource.setData(aoiFc);
  } else {
    map.addSource(ROADSIDE_LAYER_IDS.source, { type: "geojson", data: aoiFc });
  }

  if (!map.getLayer(ROADSIDE_LAYER_IDS.fill)) {
    map.addLayer({
      id: ROADSIDE_LAYER_IDS.fill,
      type: "fill",
      source: ROADSIDE_LAYER_IDS.source,
      paint: {
        "fill-color": visual.color,
        "fill-opacity": visual.fillOpacity,
      },
    });
  } else {
    map.setPaintProperty(ROADSIDE_LAYER_IDS.fill, "fill-color", visual.color);
    map.setPaintProperty(ROADSIDE_LAYER_IDS.fill, "fill-opacity", visual.fillOpacity);
  }

  if (!map.getLayer(ROADSIDE_LAYER_IDS.outline)) {
    map.addLayer({
      id: ROADSIDE_LAYER_IDS.outline,
      type: "line",
      source: ROADSIDE_LAYER_IDS.source,
      paint: {
        "line-color": visual.color,
        "line-width": visual.lineWidth,
        "line-dasharray": visual.dashed ? [3, 2] : [1, 0.01],
      },
    });
  } else {
    map.setPaintProperty(ROADSIDE_LAYER_IDS.outline, "line-color", visual.color);
    map.setPaintProperty(ROADSIDE_LAYER_IDS.outline, "line-width", visual.lineWidth);
    map.setPaintProperty(
      ROADSIDE_LAYER_IDS.outline,
      "line-dasharray",
      visual.dashed ? [3, 2] : [1, 0.01],
    );
  }

  const centerlineFc = normalizeToFeatureCollection<LineString>(data.centerline);
  if (centerlineFc && centerlineFc.features.length > 0) {
    const clSource = map.getSource(ROADSIDE_LAYER_IDS.centerlineSource) as GeoJSONSource | undefined;
    if (clSource) {
      clSource.setData(centerlineFc);
    } else {
      map.addSource(ROADSIDE_LAYER_IDS.centerlineSource, { type: "geojson", data: centerlineFc });
    }

    if (!map.getLayer(ROADSIDE_LAYER_IDS.centerline)) {
      map.addLayer({
        id: ROADSIDE_LAYER_IDS.centerline,
        type: "line",
        source: ROADSIDE_LAYER_IDS.centerlineSource,
        paint: {
          "line-color": "#94a3b8",
          "line-width": 1.5,
          "line-dasharray": [3, 3],
          "line-opacity": 0.7,
        },
      });
    }
  } else {
    if (map.getLayer(ROADSIDE_LAYER_IDS.centerline)) {
      map.removeLayer(ROADSIDE_LAYER_IDS.centerline);
    }
    if (map.getSource(ROADSIDE_LAYER_IDS.centerlineSource)) {
      map.removeSource(ROADSIDE_LAYER_IDS.centerlineSource);
    }
  }
}

export function removeRoadsideLayer(map: MapLibreMap): void {
  if (map.getLayer(ROADSIDE_LAYER_IDS.outline)) {
    map.removeLayer(ROADSIDE_LAYER_IDS.outline);
  }
  if (map.getLayer(ROADSIDE_LAYER_IDS.fill)) {
    map.removeLayer(ROADSIDE_LAYER_IDS.fill);
  }
  if (map.getSource(ROADSIDE_LAYER_IDS.source)) {
    map.removeSource(ROADSIDE_LAYER_IDS.source);
  }
  if (map.getLayer(ROADSIDE_LAYER_IDS.centerline)) {
    map.removeLayer(ROADSIDE_LAYER_IDS.centerline);
  }
  if (map.getSource(ROADSIDE_LAYER_IDS.centerlineSource)) {
    map.removeSource(ROADSIDE_LAYER_IDS.centerlineSource);
  }
}

export function bringRoadsideLayersToFront(map: MapLibreMap): void {
  if (map.getLayer(ROADSIDE_LAYER_IDS.fill)) {
    map.moveLayer(ROADSIDE_LAYER_IDS.fill);
  }
  if (map.getLayer(ROADSIDE_LAYER_IDS.outline)) {
    map.moveLayer(ROADSIDE_LAYER_IDS.outline);
  }
  if (map.getLayer(ROADSIDE_LAYER_IDS.centerline)) {
    map.moveLayer(ROADSIDE_LAYER_IDS.centerline);
  }
}
