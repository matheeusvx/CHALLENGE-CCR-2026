import {
  analysisHistoryDetailSchema,
  analysisHistoryClearResponseSchema,
  analysisHistoryHideResponseSchema,
  analysisHistoryPageSchema,
  manualAnalysisJobResponseSchema,
  automaticAnalysisResponseSchema,
  geometryValidationSchema,
  healthSchema,
  type AutomaticAnalysisRequest,
  type AutomaticAnalysisResponse,
  type GeometryDocument,
} from "@/lib/schemas/analyses";
import { ApiError, apiRequest } from "./client";
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

/** Start a short request, then poll independently of the scientific worker. */
export async function runAnalysis(values: AnalysisRunValues, signal?: AbortSignal) {
  const job = await apiRequest("/api/analyses/jobs", manualAnalysisJobResponseSchema, {
    method: "POST", body: JSON.stringify(values), signal,
  });
  let status = job;
  while (true) {
    signal?.throwIfAborted();
    if (status.status === "failed" || status.result?.status === "failed") {
      throw new ApiError(status.error_code ?? "PROCESSING_ERROR", "O pipeline não conseguiu concluir a análise.", 500);
    }
    if (status.status === "completed") {
      if (!status.result) throw new ApiError("INVALID_TERMINAL_RESPONSE", "A API retornou uma resposta inesperada.", 502);
      return status.result;
    }
    await waitForManualPoll(signal);
    status = await apiRequest(`/api/analyses/jobs/${job.analysis_id}`, manualAnalysisJobResponseSchema, {
      signal, cache: "no-store",
    });
  }
}

function waitForManualPoll(signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, 2000);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

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

/** Oculta uma analise somente da visualizacao operacional do Historico. */
export const hideAnalysisFromHistory = (analysisId: string) =>
  apiRequest(`/api/analyses/${analysisId}`, analysisHistoryHideResponseSchema, {
    method: "DELETE",
  });

/** Oculta todas as analises atualmente visiveis, sem apagar dados cientificos. */
export const clearAnalysisHistory = () =>
  apiRequest("/api/analyses", analysisHistoryClearResponseSchema, {
    method: "DELETE",
  });
export const runAutomaticAnalysis = (
  payload: AutomaticAnalysisRequest,
  signal?: AbortSignal,
): Promise<AutomaticAnalysisResponse> =>
  apiRequest("/api/analyses/automatic", automaticAnalysisResponseSchema, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
