import turfBbox from "@turf/bbox";
import type { Map as MapLibreMap, PaddingOptions } from "maplibre-gl";
import { geometryFeature, type PolygonGeometry } from "./geometry";
import { MAP_CONFIG } from "./config";

type FitGeometryOptions = {
  padding?: number | PaddingOptions;
  maxZoom?: number;
  duration?: number;
};

export function fitMapToGeometry(
  map: Pick<MapLibreMap, "fitBounds">,
  geometry: PolygonGeometry,
  options: FitGeometryOptions = {},
): boolean {
  const coordinates = geometry.coordinates.flat();
  if (!coordinates.length || coordinates.some(([longitude, latitude]) => !Number.isFinite(longitude) || !Number.isFinite(latitude))) {
    return false;
  }

  const [, south, , north] = turfBbox(geometryFeature(geometry));
  if (![south, north].every(Number.isFinite) || south < -90 || north > 90) return false;

  const [west, east] = longitudeBounds(coordinates.map(([longitude]) => longitude));
  if (!Number.isFinite(west) || !Number.isFinite(east)) return false;

  const minimumSpan = 0.0001;
  const longitudePadding = Math.max(0, minimumSpan - (east - west)) / 2;
  const latitudePadding = Math.max(0, minimumSpan - (north - south)) / 2;

  map.fitBounds(
    [
      [west - longitudePadding, south - latitudePadding],
      [east + longitudePadding, north + latitudePadding],
    ],
    {
      padding: options.padding ?? MAP_CONFIG.fit.padding,
      maxZoom: options.maxZoom ?? MAP_CONFIG.fit.maxZoom,
      duration: options.duration ?? MAP_CONFIG.fit.duration,
    },
  );
  return true;
}

function longitudeBounds(longitudes: number[]): [number, number] {
  const normalized = longitudes.map((longitude) => ((longitude + 180) % 360 + 360) % 360).sort((a, b) => a - b);
  if (normalized.some((longitude) => longitude < 0 || longitude >= 360)) return [Number.NaN, Number.NaN];
  if (normalized.length === 1) return [normalized[0] - 180, normalized[0] - 180];

  let largestGap = -1;
  let gapIndex = 0;
  for (let index = 0; index < normalized.length; index += 1) {
    const next = index === normalized.length - 1 ? normalized[0] + 360 : normalized[index + 1];
    const gap = next - normalized[index];
    if (gap > largestGap) {
      largestGap = gap;
      gapIndex = index;
    }
  }

  const start = normalized[(gapIndex + 1) % normalized.length];
  const end = normalized[gapIndex] < start ? normalized[gapIndex] + 360 : normalized[gapIndex];
  const west = start - 180;
  return [west, west + (end - start)];
}
