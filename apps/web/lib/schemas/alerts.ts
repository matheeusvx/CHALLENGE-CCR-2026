import { z } from "zod";

export const alertTypeValueSchema = z.enum([
  "RECOMMENDATION_CHANGED",
  "CUT_PENDING",
  "REOBSERVATION_REQUIRED",
  "STALE_MONITORING",
  "SUPPORT_DIVERGENCE",
]);
export type AlertTypeValue = z.infer<typeof alertTypeValueSchema>;

export const alertSeverityValueSchema = z.enum([
  "low",
  "medium",
  "high",
  "critical",
]);
export type AlertSeverityValue = z.infer<typeof alertSeverityValueSchema>;

export const alertStatusValueSchema = z.enum([
  "new",
  "seen",
  "monitoring",
  "resolved",
]);
export type AlertStatusValue = z.infer<typeof alertStatusValueSchema>;

export const alertRecommendationValueSchema = z.enum([
  "cortar",
  "nao_cortar",
  "inconclusivo",
]);
export type AlertRecommendationValue = z.infer<typeof alertRecommendationValueSchema>;

export const alertListItemSchema = z.object({
  id: z.string(),
  type: alertTypeValueSchema,
  severity: alertSeverityValueSchema,
  status: alertStatusValueSchema,
  subject_kind: z.string(),
  subject_key: z.string(),
  spatial_key: z.string().nullish(),
  road_id: z.string().nullish(),
  road_ref: z.string().nullish(),
  road_name: z.string().nullish(),
  axis_id: z.string().nullish(),
  section_id: z.string().nullish(),
  section_index: z.number().int().nullish(),
  analysis_id: z.string(),
  previous_analysis_id: z.string().nullish(),
  last_analysis_id: z.string().nullish(),
  current_recommendation: alertRecommendationValueSchema.nullish(),
  previous_recommendation: alertRecommendationValueSchema.nullish(),
  first_detected_at: z.string(),
  last_seen_at: z.string(),
  acknowledged_at: z.string().nullish(),
  resolved_at: z.string().nullish(),
  updated_at: z.string(),
  version: z.number().int().min(1),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
export type AlertListItem = z.infer<typeof alertListItemSchema>;

export const alertPageSchema = z.object({
  total: z.number().int().min(0),
  active_count: z.number().int().min(0),
  limit: z.number().int().min(1),
  offset: z.number().int().min(0),
  items: z.array(alertListItemSchema),
});
export type AlertPage = z.infer<typeof alertPageSchema>;

export const alertEventResponseSchema = z.object({
  id: z.number().int(),
  event_type: z.string(),
  occurred_at: z.string(),
  analysis_id: z.string().nullish(),
  previous_status: alertStatusValueSchema.nullish(),
  new_status: alertStatusValueSchema.nullish(),
  severity: alertSeverityValueSchema.nullish(),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
export type AlertEventResponse = z.infer<typeof alertEventResponseSchema>;

export const alertAnalysisReferenceSchema = z.object({
  analysis_id: z.string(),
  created_at: z.string(),
  status: z.string(),
  decision: alertRecommendationValueSchema.nullish(),
  confidence: z.string().nullish(),
  latest_valid_observation_on: z.string().nullish(),
});
export type AlertAnalysisReference = z.infer<typeof alertAnalysisReferenceSchema>;

export const alertRoadMetadataSchema = z.object({
  road_id: z.string().nullish(),
  road_ref: z.string().nullish(),
  road_name: z.string().nullish(),
  axis_id: z.string().nullish(),
  section_id: z.string().nullish(),
  section_index: z.number().int().nullish(),
  spatial_key: z.string().nullish(),
});
export type AlertRoadMetadata = z.infer<typeof alertRoadMetadataSchema>;

export const alertMapTargetSchema = z.object({
  geometry: z.record(z.string(), z.unknown()).nullish(),
  bounds: z
    .object({
      west: z.number(),
      south: z.number(),
      east: z.number(),
      north: z.number(),
    })
    .nullish(),
  centroid: z
    .object({
      longitude: z.number(),
      latitude: z.number(),
    })
    .nullish(),
  road_ref: z.string().nullish(),
  road_name: z.string().nullish(),
  section_id: z.string().nullish(),
});
export type AlertMapTarget = z.infer<typeof alertMapTargetSchema>;

export const alertDetailSchema = alertListItemSchema.extend({
  timeline: z.array(alertEventResponseSchema),
  origin_analysis: alertAnalysisReferenceSchema,
  previous_analysis: alertAnalysisReferenceSchema.nullish(),
  latest_analysis: alertAnalysisReferenceSchema.nullish(),
  road_metadata: alertRoadMetadataSchema,
  map_target: alertMapTargetSchema,
});
export type AlertDetail = z.infer<typeof alertDetailSchema>;

export const alertPatchRequestSchema = z.object({
  status: z.enum(["seen", "monitoring", "resolved"]),
  version: z.number().int().min(1),
});
export type AlertPatchRequest = z.infer<typeof alertPatchRequestSchema>;

export const alertPatchResponseSchema = alertListItemSchema;
export type AlertPatchResponse = z.infer<typeof alertPatchResponseSchema>;
