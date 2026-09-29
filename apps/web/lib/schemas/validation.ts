import { z } from "zod";

export const vegetationClassSchema = z.enum([
  "low_grass",
  "tall_dense_grass",
  "shrub",
  "tree",
  "mixed",
]);

export type VegetationClass = z.infer<typeof vegetationClassSchema>;

export const maintenanceTruthSchema = z.enum(["cut", "no_cut", "uncertain"]);

export type MaintenanceTruth = z.infer<typeof maintenanceTruthSchema>;

export const validationSourceSchema = z.enum([
  "visual_inspection",
  "aerial_imagery",
  "field_inspection",
  "maintenance_record",
  "other",
]);

export type ValidationSource = z.infer<typeof validationSourceSchema>;

export const validationSampleCreateSchema = z.object({
  analysis_id: z.string().uuid(),
  vegetation_class: vegetationClassSchema,
  maintenance_truth: maintenanceTruthSchema,
  validation_source: validationSourceSchema,
  reference_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Data deve estar no formato AAAA-MM-DD"),
  notes: z.string().max(1000).nullable().optional(),
});

export type ValidationSampleCreate = z.infer<typeof validationSampleCreateSchema>;

export const validationSampleRowSchema = z.object({
  sample_id: z.string().uuid(),
  analysis_id: z.string().uuid(),
  schema_version: z.number().int(),
  created_at: z.string(),
  vegetation_class: vegetationClassSchema,
  maintenance_truth: maintenanceTruthSchema,
  validation_source: validationSourceSchema,
  reference_date: z.string(),
  notes: z.string().nullable().optional(),
  selected_area_m2: z.number().nullable().optional(),
  s2_decision: z.string().nullable().optional(),
  s2_confidence: z.string().nullable().optional(),
  s2_ndvi_mean: z.number().nullable().optional(),
  s2_ndvi_median: z.number().nullable().optional(),
  s2_current_percentile: z.number().nullable().optional(),
  s1_status: z.string().nullable().optional(),
  s1_quality: z.number().nullable().optional(),
  s1_coverage: z.number().nullable().optional(),
  s1_canonical_relative_orbit: z.number().int().nullable().optional(),
  s1_canonical_observation_count: z.number().int().nullable().optional(),
  s1_vv_sigma0_linear: z.number().nullable().optional(),
  s1_vh_sigma0_linear: z.number().nullable().optional(),
  s1_vv_sigma0_db: z.number().nullable().optional(),
  s1_vh_sigma0_db: z.number().nullable().optional(),
  s1_vh_minus_vv_db: z.number().nullable().optional(),
  s1_vh_vv_sigma0_ratio: z.number().nullable().optional(),
});

export type ValidationSampleRow = z.infer<typeof validationSampleRowSchema>;

export const validationSampleDetailSchema = validationSampleRowSchema.extend({
  snapshot: z.record(z.string(), z.unknown()),
});

export type ValidationSampleDetail = z.infer<typeof validationSampleDetailSchema>;

export const validationSampleListSchema = z.object({
  items: z.array(validationSampleRowSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export type ValidationSampleList = z.infer<typeof validationSampleListSchema>;

export const metricStatisticSchema = z.object({
  count: z.number(),
  median: z.number(),
  mean: z.number(),
  min: z.number(),
  max: z.number(),
  q25: z.number().optional(),
  q75: z.number().optional(),
});

export type MetricStatistic = z.infer<typeof metricStatisticSchema>;

export const classSummarySchema = z.object({
  count: z.number().int(),
  target: z.number().int(),
  remaining: z.number().int(),
  statistics: z.record(z.string(), z.record(z.string(), metricStatisticSchema.optional())).optional(),
});

export type ClassSummary = z.infer<typeof classSummarySchema>;

export const validationSummarySchema = z.object({
  total_samples: z.number().int(),
  target_total: z.number().int(),
  remaining_total: z.number().int(),
  target_per_class: z.number().int(),
  counts_by_vegetation_class: z.record(z.string(), z.number()),
  by_vegetation_class: z.record(z.string(), classSummarySchema),
  counts_by_maintenance_truth: z.record(z.string(), z.number()),
});

export type ValidationSummary = z.infer<typeof validationSummarySchema>;
