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
  workerUrl: "/vendor/maplibre/maplibre-gl-worker.mjs",
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
    padding: { top: 80, right: 180, bottom: 80, left: 80 },
    maxZoom: 16,
    duration: 700,
  },
  uploadMaxBytes: 2 * 1024 * 1024,
} as const;

export const AUTO_ANALYSIS_CONFIG = {
  debounceMs: 1000,
  pollingIntervalMs: 2500,
  minZoom: 13,
  maxZoom: 22,
  storageKey: "motiva.auto_analysis_enabled",
} as const;

export type MapViewport = {
  longitude: number;
  latitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
};

export function computeTileKey(lng: number, lat: number, z = 17): string {
  const x = Math.floor(((lng + 180) / 360) * Math.pow(2, z));
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * Math.pow(2, z)
  );
  return `tile:${z}/${x}/${y}`;
}
