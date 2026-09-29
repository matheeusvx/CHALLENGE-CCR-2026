import {
  analysisHistoryDetailSchema,
  analysisHistoryPageSchema,
  analysisResponseSchema,
  automaticAnalysisResponseSchema,
  geometryValidationSchema,
  healthSchema,
  type AutomaticAnalysisRequest,
  type AutomaticAnalysisResponse,
  type GeometryDocument,
} from "@/lib/schemas/analyses";
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

/** Historico de analises gravado no servidor, da mais recente para a mais antiga. */
export const listAnalyses = (options: { limit?: number; offset?: number; decision?: string } = {}) => {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  if (options.decision) params.set("decision", options.decision);
  const query = params.toString();
  return apiRequest(`/api/analyses${query ? `?${query}` : ""}`, analysisHistoryPageSchema);
};

/** Analise completa gravada, incluindo a geometria da AOI. */
export const getAnalysisDetail = (analysisId: string) =>
  apiRequest(`/api/analyses/${analysisId}`, analysisHistoryDetailSchema);
export const runAutomaticAnalysis = (
  payload: AutomaticAnalysisRequest,
  signal?: AbortSignal,
): Promise<AutomaticAnalysisResponse> =>
  apiRequest("/api/analyses/automatic", automaticAnalysisResponseSchema, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
