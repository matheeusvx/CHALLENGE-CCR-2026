import type { StyleSpecification } from "maplibre-gl";

export const OPERATIONAL_RASTER_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    openStreetMap: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "openstreetmap-base",
      type: "raster",
      source: "openStreetMap",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};

export const MAP_STYLES = {
  operational: {
    id: "operational",
    label: "Operacional",
    available: true,
  },
  terrain: {
    id: "terrain",
    label: "Terreno",
    available: false,
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
