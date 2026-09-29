import { describe, expect, it } from "vitest";
import { getResultPopupPresentation } from "@/lib/map/result-popup";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

function result(
  decision: AnalysisResponse["recommendation"]["decision"],
  confidence: AnalysisResponse["recommendation"]["confidence"],
): AnalysisResponse {
  return {
    analysis_id: "6d7ba572-321d-4a27-9f0f-9fcbd5ecab62",
    status: "completed",
    analysis_period: {
      start_date: "2026-07-11",
      end_date: "2026-08-11",
      timezone: "America/Sao_Paulo",
      strategy: "previous_calendar_month",
    },
    recommendation: {
      decision,
      confidence,
      experimental: true,
      summary: "",
      reasons: [],
      blocking_reasons: [],
      limitations: [],
      metrics: {},
    },
    aoi: {},
    summary: {},
    timeseries: [],
    scenes: [],
    artifacts: {},
    warnings: [],
    errors: [],
  };
}

describe("apresentação do popup do resultado", () => {
  it.each([
    ["cortar", "high", "CORTAR", "Alta"],
    ["nao_cortar", "medium", "NÃO CORTAR", "Média"],
    ["inconclusivo", "low", "INCONCLUSIVO", "Baixa"],
  ] as const)("formata %s com confiança em português", (decision, confidence, label, level) => {
    expect(getResultPopupPresentation(result(decision, confidence))).toEqual({
      decision: label,
      confidence: level,
      period: "11/07/2026 a 11/08/2026",
      state: decision,
    });
  });
});
