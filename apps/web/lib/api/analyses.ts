import { analysisResponseSchema, geometryValidationSchema, healthSchema, type GeometryDocument } from "@/lib/schemas/analyses";
import { apiRequest } from "./client";
import type { components } from "./generated";

type GeneratedAnalysisRunRequest = components["schemas"]["AnalysisRunRequest"];

export type AnalysisRunValues = Pick<GeneratedAnalysisRunRequest, "geometry"> & {
  geometry: GeometryDocument;
};

export const getHealth = () => apiRequest("/api/health", healthSchema);

export const validateGeometry = (geometry: GeometryDocument) =>
  apiRequest("/api/analyses/validate-geometry", geometryValidationSchema, {
    method: "POST",
    body: JSON.stringify({ geometry }),
  });

export const runAnalysis = (values: AnalysisRunValues) =>
  apiRequest("/api/analyses/run", analysisResponseSchema, {
    method: "POST",
    body: JSON.stringify(values),
  });
