export const MAP_STYLES = {
  operational: {
    id: "operational",
    label: "Operacional",
    styleUrl:
      process.env.NEXT_PUBLIC_MAP_OPERATIONAL_STYLE_URL ??
      "https://tiles.openfreemap.org/styles/bright",
  },
  terrain: {
    id: "terrain",
    label: "Terreno",
    styleUrl:
      process.env.NEXT_PUBLIC_MAP_TERRAIN_STYLE_URL ??
      process.env.NEXT_PUBLIC_MAP_STYLE_URL ??
      "https://demotiles.maplibre.org/style.json",
  },
} as const;

export type MapStyleId = keyof typeof MAP_STYLES;

export const DEFAULT_MAP_STYLE_ID: MapStyleId = "operational";

export const MAP_CONFIG = {
  initialViewport: {
    longitude: -46.955,
    latitude: -23.121,
    zoom: 12,
    bearing: 0,
    pitch: 0,
  },
  minZoom: 5,
  maxZoom: 19,
  fit: {
    padding: { top: 80, right: 120, bottom: 80, left: 80 },
    maxZoom: 16,
    duration: 700,
  },
  uploadMaxBytes: 2 * 1024 * 1024,
} as const;

export type MapViewport = {
  longitude: number;
  latitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
};
