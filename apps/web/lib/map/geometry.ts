import turfArea from "@turf/area";
import turfBbox from "@turf/bbox";
import booleanValid from "@turf/boolean-valid";
import turfCentroid from "@turf/centroid";
import { feature } from "@turf/helpers";
import { z } from "zod";
import type { Feature, Polygon } from "geojson";

const positionSchema = z.tuple([
  z.number().finite().min(-180).max(180),
  z.number().finite().min(-90).max(90),
]);

const ringSchema = z
  .array(positionSchema)
  .min(4)
  .refine(
    (ring) => {
      const first = ring[0];
      const last = ring.at(-1);
      return first[0] === last?.[0] && first[1] === last?.[1];
    },
    "Os anéis do polígono devem estar fechados.",
  );

export const polygonSchema = z.object({
  type: z.literal("Polygon"),
  coordinates: z.array(ringSchema).min(1),
});

const featureSchema = z.object({
  type: z.literal("Feature"),
  geometry: polygonSchema,
  properties: z.record(z.string(), z.unknown()).nullable().optional(),
});

const featureCollectionSchema = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(featureSchema).length(1),
});

export type PolygonGeometry = z.infer<typeof polygonSchema>;

export type GeometryPreview = {
  areaSquareMeters: number;
  areaHectares: number;
  centroid: { longitude: number; latitude: number };
  boundingBox: [number, number, number, number];
  estimatedSentinelPixels: number;
};

export function geometryFeature(geometry: PolygonGeometry): Feature<Polygon> {
  return feature(geometry as Polygon);
}

export function normalizeGeoJson(input: unknown): PolygonGeometry {
  const direct = polygonSchema.safeParse(input);
  if (direct.success) {
    assertTopologicallyValid(direct.data);
    return direct.data;
  }

  const wrapped = featureSchema.safeParse(input);
  if (wrapped.success) {
    assertTopologicallyValid(wrapped.data.geometry);
    return wrapped.data.geometry;
  }

  const collection = featureCollectionSchema.safeParse(input);
  if (collection.success) {
    const geometry = collection.data.features[0].geometry;
    assertTopologicallyValid(geometry);
    return geometry;
  }

  throw new Error(
    "Informe um Polygon, uma Feature Polygon ou uma FeatureCollection com um único Polygon.",
  );
}

export function parseGeoJsonText(value: string): PolygonGeometry {
  let document: unknown;
  try {
    document = JSON.parse(value);
  } catch {
    throw new Error("O GeoJSON não contém JSON válido.");
  }
  return normalizeGeoJson(document);
}

export function calculateGeometryPreview(geometry: PolygonGeometry): GeometryPreview {
  const polygon = geometryFeature(geometry);
  const areaSquareMeters = turfArea(polygon);
  const center = turfCentroid(polygon).geometry.coordinates;
  const bounds = turfBbox(polygon);

  return {
    areaSquareMeters,
    areaHectares: areaSquareMeters / 10_000,
    centroid: { longitude: center[0], latitude: center[1] },
    boundingBox: [bounds[0], bounds[1], bounds[2], bounds[3]],
    estimatedSentinelPixels: Math.max(1, Math.ceil(areaSquareMeters / 100)),
  };
}

export function formatGeometry(geometry: PolygonGeometry): string {
  return JSON.stringify(geometryFeature(geometry), null, 2);
}

function assertTopologicallyValid(geometry: PolygonGeometry) {
  if (!booleanValid(geometryFeature(geometry))) {
    throw new Error("O polígono possui uma geometria inválida ou autointersectada.");
  }
}
