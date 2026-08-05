import { analysisResponseSchema, geometryValidationSchema, healthSchema, type GeometryDocument } from "@/lib/schemas/analyses";
import { apiRequest } from "./client";

export type AnalysisFormValues = {
  geometry: GeometryDocument;
  start_date: string;
  end_date: string;
  max_cloud_cover: number;
  max_scenes: number;
  scene_order: "newest" | "oldest";
  min_valid_pixel_percentage: number;
  min_observations: number;
  daily_aggregation: "best" | "median" | "none";
};

export const getHealth = () => apiRequest("/api/health", healthSchema);

export const validateGeometry = (geometry: GeometryDocument) =>
  apiRequest("/api/analyses/validate-geometry", geometryValidationSchema, {
    method: "POST",
    body: JSON.stringify({ geometry }),
  });

export const runAnalysis = (values: AnalysisFormValues) =>
  apiRequest("/api/analyses/run", analysisResponseSchema, {
    method: "POST",
    body: JSON.stringify({ ...values, decision: {} }),
  });
