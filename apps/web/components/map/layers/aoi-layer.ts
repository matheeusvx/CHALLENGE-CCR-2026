import centroid from "@turf/centroid";
import type { Feature, FeatureCollection, Point, Polygon } from "geojson";
import type { GeoJSONSource, Map as MapLibreMap, MapLayerMouseEvent } from "maplibre-gl";
import type { AoiVisualState } from "@/lib/map/aoi-visual-state";
import { geometryFeature, type PolygonGeometry } from "@/lib/map/geometry";

export const AOI_LAYER_IDS = {
  source: "analysis-aoi-source",
  fill: "analysis-aoi-fill",
  halo: "analysis-aoi-outline-halo",
  outline: "analysis-aoi-outline",
  vertices: "analysis-aoi-vertices",
  label: "analysis-aoi-label",
} as const;

type AoiFeatureProperties = {
  feature_role: "area" | "vertex" | "label";
  label?: string;
};

export function installOrUpdateAoiLayer(
  map: MapLibreMap,
  geometry: PolygonGeometry | null,
  visualState: AoiVisualState,
): void {
  if (!geometry) {
    removeAoiLayer(map);
    return;
  }

  const data = createAoiFeatureCollection(geometry, visualState.label);
  const source = map.getSource(AOI_LAYER_IDS.source);
  if (source) {
    (source as GeoJSONSource).setData(data);
  } else {
    map.addSource(AOI_LAYER_IDS.source, { type: "geojson", data });
  }

  addLayerIfMissing(map, {
    id: AOI_LAYER_IDS.fill,
    type: "fill",
    source: AOI_LAYER_IDS.source,
    paint: { "fill-color": visualState.color, "fill-opacity": visualState.fillOpacity },
  });
  addLayerIfMissing(map, {
    id: AOI_LAYER_IDS.halo,
    type: "line",
    source: AOI_LAYER_IDS.source,
    paint: { "line-color": "#1d1b25", "line-width": 9, "line-opacity": 0.7 },
  });
  addLayerIfMissing(map, {
    id: AOI_LAYER_IDS.outline,
    type: "line",
    source: AOI_LAYER_IDS.source,
    paint: {
      "line-color": visualState.color,
      "line-width": 5,
      "line-opacity": 1,
      "line-dasharray": visualState.dashed ? [2, 1.5] : [1, 0.01],
    },
  });
  addLayerIfMissing(map, {
    id: AOI_LAYER_IDS.vertices,
    type: "circle",
    source: AOI_LAYER_IDS.source,
    filter: ["==", ["get", "feature_role"], "vertex"],
    paint: {
      "circle-color": "#ffffff",
      "circle-radius": ["case", ["boolean", ["feature-state", "hover"], false], 8, 6],
      "circle-stroke-color": visualState.color,
      "circle-stroke-width": 3,
    },
  });
  if (map.getStyle().glyphs) {
    addLayerIfMissing(map, {
      id: AOI_LAYER_IDS.label,
      type: "symbol",
      source: AOI_LAYER_IDS.source,
      filter: ["==", ["get", "feature_role"], "label"],
      layout: {
        "text-field": ["get", "label"],
        "text-font": ["Noto Sans Regular"],
        "text-size": 12,
        "text-offset": [0, 1.2],
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#26232e",
        "text-halo-color": "#ffffff",
        "text-halo-width": 2,
      },
    });
  }

  map.setPaintProperty(AOI_LAYER_IDS.fill, "fill-color", visualState.color);
  map.setPaintProperty(AOI_LAYER_IDS.fill, "fill-opacity", visualState.fillOpacity);
  map.setPaintProperty(AOI_LAYER_IDS.outline, "line-color", visualState.color);
  map.setPaintProperty(AOI_LAYER_IDS.outline, "line-dasharray", visualState.dashed ? [2, 1.5] : [1, 0.01]);
  map.setPaintProperty(AOI_LAYER_IDS.vertices, "circle-stroke-color", visualState.color);
  bringAoiLayersToFront(map);
}

export function bringAoiLayersToFront(map: MapLibreMap): void {
  if (typeof map.moveLayer !== "function") return;
  [AOI_LAYER_IDS.fill, AOI_LAYER_IDS.halo, AOI_LAYER_IDS.outline, AOI_LAYER_IDS.vertices, AOI_LAYER_IDS.label]
    .forEach((id) => {
      if (map.getLayer(id)) map.moveLayer(id);
    });
}

export function removeAoiLayer(map: MapLibreMap): void {
  [AOI_LAYER_IDS.label, AOI_LAYER_IDS.vertices, AOI_LAYER_IDS.outline, AOI_LAYER_IDS.halo, AOI_LAYER_IDS.fill]
    .forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
  if (map.getSource(AOI_LAYER_IDS.source)) map.removeSource(AOI_LAYER_IDS.source);
}

export function installAoiHoverInteractions(map: MapLibreMap): () => void {
  let hoveredId: string | number | undefined;
  const enter = () => { map.getCanvas().style.cursor = "pointer"; };
  const leave = () => {
    map.getCanvas().style.cursor = "";
    if (hoveredId !== undefined) map.setFeatureState({ source: AOI_LAYER_IDS.source, id: hoveredId }, { hover: false });
    hoveredId = undefined;
  };
  const move = (event: MapLayerMouseEvent) => {
    const nextId = event.features?.[0]?.id;
    if (nextId === undefined || nextId === hoveredId) return;
    if (hoveredId !== undefined) map.setFeatureState({ source: AOI_LAYER_IDS.source, id: hoveredId }, { hover: false });
    hoveredId = nextId;
    map.setFeatureState({ source: AOI_LAYER_IDS.source, id: hoveredId }, { hover: true });
  };
  map.on("mouseenter", AOI_LAYER_IDS.vertices, enter);
  map.on("mousemove", AOI_LAYER_IDS.vertices, move);
  map.on("mouseleave", AOI_LAYER_IDS.vertices, leave);
  return () => {
    map.off("mouseenter", AOI_LAYER_IDS.vertices, enter);
    map.off("mousemove", AOI_LAYER_IDS.vertices, move);
    map.off("mouseleave", AOI_LAYER_IDS.vertices, leave);
    leave();
  };
}

export function createAoiFeatureCollection(geometry: PolygonGeometry, label: string): FeatureCollection<Polygon | Point, AoiFeatureProperties> {
  const area = geometryFeature(geometry) as Feature<Polygon, AoiFeatureProperties>;
  area.id = "aoi-area";
  area.properties = { feature_role: "area" };
  const center = centroid(area);
  const labelFeature: Feature<Point, AoiFeatureProperties> = {
    ...center,
    id: "aoi-label",
    properties: { feature_role: "label", label },
  };
  const vertices: Array<Feature<Point, AoiFeatureProperties>> = geometry.coordinates.flatMap((ring, ringIndex) =>
    ring.slice(0, -1).map((coordinates, vertexIndex) => ({
      type: "Feature",
      id: `aoi-vertex-${ringIndex}-${vertexIndex}`,
      geometry: { type: "Point", coordinates },
      properties: { feature_role: "vertex" },
    })),
  );
  return { type: "FeatureCollection", features: [area, ...vertices, labelFeature] };
}

function addLayerIfMissing(map: MapLibreMap, layer: Parameters<MapLibreMap["addLayer"]>[0]): void {
  if (!map.getLayer(layer.id)) map.addLayer(layer);
}
