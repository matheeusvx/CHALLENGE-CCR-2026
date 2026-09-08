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

export const experimentalFusionSchema = z.object({
  schema_version: z.literal("1.0"),
  fusion_mode: z.enum(["disabled", "shadow", "experimental", "operational"]),
  fusion_policy: z.literal("experimental_v1").nullable().optional(),
  // 1.0 remains accepted for persisted historical results; 1.1 is the
  // current Sentinel-2-primary policy returned by the API.
  experimental_policy_version: z.enum(["1.0", "1.1"]).nullable().optional(),
  sentinel2_recommendation: z.enum(["cortar", "nao_cortar", "inconclusivo"]),
  multisource_recommendation: z.enum(["cortar", "nao_cortar", "inconclusivo"]),
  final_recommendation: z.enum(["cortar", "nao_cortar", "inconclusivo"]).nullable().optional(),
  sentinel1_influenced_decision: z.boolean(),
  fusion_rule: z.literal("B").nullable().optional(),
  fusion_reason: z.string().nullable().optional(),
  sentinel1_temporal_status: z.enum([
    "increasing", "decreasing", "stable", "mixed", "insufficient_data", "disabled"
  ]).nullable().optional(),
  experimental: z.boolean(),
  operationally_authorized: z.literal(false).optional(),
  experimental_fusion_evaluated: z.boolean().optional(),
  experimental_fusion_evaluable: z.boolean().optional(),
  fusion_not_evaluable_reason: z.string().nullable().optional(),
});

export const multisourceResponseSchema = z.object({
  enabled: z.boolean(),
  fusion_mode: z.enum(["disabled", "shadow", "experimental", "operational"]),
  official_recommendation_changed: z.literal(false).optional(),
  generated_at: z.string(),
  configuration: recordSchema.optional(),
  sources: z.array(recordSchema).optional(),
  review: recordSchema.nullable().optional(),
  operational_fusion: recordSchema.nullable().optional(),
  experimental_fusion: experimentalFusionSchema.nullable().optional(),
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
  multisource: multisourceResponseSchema.nullable().optional(),
  aoi: recordSchema,
  summary: recordSchema,
  timeseries: z.array(recordSchema),
  scenes: z.array(recordSchema),
  artifacts: z.record(z.string(), z.string()),
  warnings: z.array(recordSchema),
  errors: z.array(recordSchema),
  analysis_trigger: z.enum(["manual", "automatic_viewport"]).optional(),
  road: z
    .object({
      id: z.string().optional(),
      ref: z.string().optional(),
      name: z.string().optional(),
    })
    .nullish(),
});

export const viewportBoundsSchema = z.object({
  west: z.number(),
  south: z.number(),
  east: z.number(),
  north: z.number(),
});

export const viewportCenterSchema = z.object({
  lng: z.number(),
  lat: z.number(),
});

export const automaticAnalysisRequestSchema = z.object({
  bounds: viewportBoundsSchema,
  zoom: z.number(),
  center: viewportCenterSchema,
  force_refresh: z.boolean().default(false),
});

export const automaticAnalysisResponseSchema = z.object({
  status: z.enum([
    "disabled",
    "zoom_required",
    "invalid_viewport",
    "cache_hit",
    "in_progress",
    "analysis_started",
    "completed",
    "failed",
    "skipped",
    "road_context_required",
    "road_geometry_unavailable",
    "road_not_found",
    "road_geometry_unreliable",
    "road_ambiguous",
    "road_section_resolved",
    "roadside_too_small",
    "roadside_geometry_invalid",
  ]),
  spatial_key: z.string().nullable().optional(),
  analysis_id: z.string().nullable().optional(),
  analysis_started: z.boolean().optional(),
  cache_hit: z.boolean().optional(),
  automatic: z.boolean().optional(),
  reason: z.string().nullable().optional(),
  canonical_bounds: viewportBoundsSchema.nullable().optional(),
  cache_state: z.enum(["fresh", "in_progress", "failed"]).nullable().optional(),
  expires_at: z.string().nullable().optional(),
  result: analysisResponseSchema.nullable().optional(),
  road: z
    .object({
      id: z.string().optional(),
      ref: z.string().optional(),
      name: z.string().optional(),
    })
    .passthrough()
    .nullable()
    .optional(),
  spatial_strategy: z.union([z.string(), z.record(z.string(), z.unknown())]).nullable().optional(),
  canonical_segment_geometry: z.record(z.string(), z.unknown()).nullable().optional(),
  roadway_exclusion_m: z.number().nullable().optional(),
  lateral_width_m: z.number().nullable().optional(),
  centerline: z.record(z.string(), z.unknown()).nullable().optional(),
  side_a_geometry: z.record(z.string(), z.unknown()).nullable().optional(),
  side_b_geometry: z.record(z.string(), z.unknown()).nullable().optional(),
  analyzed_geometry: z.record(z.string(), z.unknown()).nullable().optional(),
  roadside_metrics: z.record(z.string(), z.unknown()).nullable().optional(),
});

export const healthSchema = z.object({
  status: z.literal("ok"),
  service: z.string(),
  version: z.string(),
});

export type GeometryDocument = z.infer<typeof geometrySchema>;
export type GeometryValidation = z.infer<typeof geometryValidationSchema>;
export type AnalysisResponse = z.infer<typeof analysisResponseSchema>;
export type SpatialZone = z.infer<typeof spatialZoneSchema>;
export type SpatialSegmentation = z.infer<typeof spatialSegmentationSchema>;
export type ExperimentalFusion = z.infer<typeof experimentalFusionSchema>;
export type MultisourceResponse = z.infer<typeof multisourceResponseSchema>;
export type ViewportBounds = z.infer<typeof viewportBoundsSchema>;
export type ViewportCenter = z.infer<typeof viewportCenterSchema>;
export type AutomaticAnalysisRequest = z.infer<typeof automaticAnalysisRequestSchema>;
export type AutomaticAnalysisResponse = z.infer<typeof automaticAnalysisResponseSchema>;

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
