import { z } from "zod";

const positionSchema = z.tuple([z.number().min(-180).max(180), z.number().min(-90).max(90)]);
const ringSchema = z.array(positionSchema).min(4);

export const geometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: z.array(ringSchema).min(1) }),
  z.object({ type: z.literal("MultiPolygon"), coordinates: z.array(z.array(ringSchema).min(1)).min(1) }),
  z.object({ type: z.literal("Feature"), geometry: z.record(z.string(), z.unknown()), properties: z.record(z.string(), z.unknown()).optional() }),
  z.object({ type: z.literal("FeatureCollection"), features: z.array(z.record(z.string(), z.unknown())).min(1) }),
]);

export const geometryValidationSchema = z.object({
  valid: z.literal(true),
  geometry_type: z.string(),
  area_square_meters: z.number(),
  centroid: z.object({ longitude: z.number(), latitude: z.number() }),
  bounding_box: z.array(z.number()).length(4),
  estimated_sentinel_pixels: z.number().int().nonnegative(),
  warnings: z.array(z.string()),
});

const recordSchema = z.record(z.string(), z.unknown());

export const heightEstimationSchema = z.object({
  status: z.enum(["experimental", "unavailable", "disabled"]),
  estimated_class: z.enum(["le_30_cm", "gt_30_cm", "inconclusive"]).nullable(),
  score_gt_30_cm: z.number().min(0).max(1).nullable().optional(),
  probability_gt_30_cm: z.number().min(0).max(1).nullable().optional(),
  calibration_status: z.literal("uncalibrated").nullable().optional(),
  vegetation_fraction: z.number().min(0).max(1).nullable().optional(),
  height_valid_pixel_count: z.number().int().nonnegative().nullable().optional(),
  height_total_pixel_count: z.number().int().nonnegative().nullable().optional(),
  mixed_pixel_risk: z.enum(["low", "medium", "high"]).nullable().optional(),
  confidence: z.enum(["low", "medium"]).nullable(),
  reference_threshold_cm: z.literal(30),
  model_version: z.string().nullable(),
  provenance: z.record(z.string(), z.unknown()).nullable().optional(),
});

export const spatialZoneSchema = z.object({
  zone_id: z.string(),
  recommendation: z.enum(["cortar", "nao_cortar", "inconclusivo"]),
  geometry: z.record(z.string(), z.unknown()),
  area_m2: z.number().nonnegative(),
  confidence: z.enum(["high", "medium", "low"]),
  analysis_quality: z.enum(["high", "medium", "low"]),
  reasons: z.array(z.string()),
  start_distance_m: z.number().nonnegative(),
  end_distance_m: z.number().nonnegative(),
  road_ref: z.string(),
});

export const spatialSegmentationSchema = z.object({
  status: z.enum(["available", "not_applicable", "unavailable"]),
  experimental: z.boolean(),
  section_length_m: z.number().positive(),
  effective_coverage_pct: z.number().nonnegative().max(100).nullable(),
  zones: z.array(spatialZoneSchema),
});

export const analysisResponseSchema = z.object({
  analysis_id: z.string().uuid(),
  status: z.string(),
  analysis_period: z.object({
    start_date: z.iso.date(),
    end_date: z.iso.date(),
    timezone: z.string(),
    strategy: z.enum(["previous_calendar_month", "explicit"]),
  }),
  recommendation: z.object({
    decision: z.enum(["cortar", "nao_cortar", "inconclusivo"]),
    confidence: z.enum(["high", "medium", "low"]),
    experimental: z.boolean(),
    summary: z.string(),
    reasons: z.array(z.string()),
    blocking_reasons: z.array(z.string()),
    limitations: z.array(z.string()),
    metrics: recordSchema,
  }),
  height_estimation: heightEstimationSchema.optional(),
  selected_area_m2: z.number().positive().nullish(),
  effective_analysis_area_m2: z.number().nonnegative().nullish(),
  effective_analysis_pct: z.number().nonnegative().nullish(),
  spatial_segmentation: spatialSegmentationSchema.optional(),
  aoi: recordSchema,
  summary: recordSchema,
  timeseries: z.array(recordSchema),
  scenes: z.array(recordSchema),
  artifacts: z.record(z.string(), z.string()),
  warnings: z.array(recordSchema),
  errors: z.array(recordSchema),
});

export const analysisHistoryItemSchema = z.object({
  analysis_id: z.string(),
  created_at: z.string(),
  status: z.string(),
  decision: z.enum(["cortar", "nao_cortar", "inconclusivo"]).nullable().optional(),
  confidence: z.enum(["high", "medium", "low"]).nullable().optional(),
  summary: z.string().nullable().optional(),
  period_start: z.string().nullable().optional(),
  period_end: z.string().nullable().optional(),
  selected_area_m2: z.number().nullable().optional(),
  analysis_quality_status: z.enum(["high", "medium", "low"]).nullable().optional(),
  observation_count: z.number().int().nullable().optional(),
  nearest_km: z.number().int().nullable().optional(),
  centroid: z.object({ longitude: z.number(), latitude: z.number() }).nullable().optional(),
});

export const analysisHistoryPageSchema = z.object({
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  items: z.array(analysisHistoryItemSchema),
});

export const analysisHistoryDetailSchema = z.object({
  analysis_id: z.string(),
  created_at: z.string(),
  geometry: z.record(z.string(), z.unknown()).nullable().optional(),
  result: analysisResponseSchema,
});

export const healthSchema = z.object({
  status: z.literal("ok"),
  service: z.string(),
  version: z.string(),
});

export type GeometryDocument = z.infer<typeof geometrySchema>;
export type GeometryValidation = z.infer<typeof geometryValidationSchema>;
export type AnalysisResponse = z.infer<typeof analysisResponseSchema>;
export type AnalysisHistoryItem = z.infer<typeof analysisHistoryItemSchema>;
export type AnalysisHistoryPage = z.infer<typeof analysisHistoryPageSchema>;
export type AnalysisHistoryDetail = z.infer<typeof analysisHistoryDetailSchema>;
export type SpatialZone = z.infer<typeof spatialZoneSchema>;
export type SpatialSegmentation = z.infer<typeof spatialSegmentationSchema>;

export function parseGeometryText(value: string): GeometryDocument {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("O GeoJSON não contém JSON válido.");
  }
  const result = geometrySchema.safeParse(parsed);
  if (!result.success) {
    throw new Error("Informe um Polygon, MultiPolygon, Feature ou FeatureCollection válido.");
  }
  return result.data;
}
