import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runAnalysis, type AnalysisRunValues } from "@/lib/api/analyses";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const id = "11111111-1111-4111-8111-111111111111";
const values: AnalysisRunValues = { geometry: { type: "Polygon" as const, coordinates: [[[-47, -23], [-46, -23], [-46, -22], [-47, -23]]] } };
const result: AnalysisResponse = {
  analysis_id: id, status: "completed", analysis_trigger: "manual",
  analysis_period: { start_date: "2026-06-11", end_date: "2026-08-10", timezone: "America/Sao_Paulo", strategy: "lookback_days" },
  recommendation: { decision: "inconclusivo", confidence: "low", experimental: true, summary: "Observações insuficientes.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
  aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
};
const fetchMock = vi.fn();
function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("execução manual com polling", () => {
  it.each(["completed", "insufficient_observations"])("aceita resultado %s e consulta até terminal", async (status) => {
    fetchMock.mockResolvedValueOnce(respond({ analysis_id: id, status: "in_progress" }, 202))
      .mockResolvedValueOnce(respond({ analysis_id: id, status: "in_progress" }))
      .mockResolvedValueOnce(respond({ analysis_id: id, status: "completed", result: { ...result, status } }));
    const pending = runAnalysis(values);
    await vi.advanceTimersByTimeAsync(4000);
    expect(await pending).toMatchObject({ status, recommendation: { decision: "inconclusivo" } });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0]).toEqual(["/api/analyses/jobs", expect.objectContaining({ method: "POST", body: JSON.stringify(values) })]);
    expect(fetchMock.mock.calls[1]).toEqual([`/api/analyses/jobs/${id}`, expect.objectContaining({ cache: "no-store" })]);
  });

  it.each([
    { analysis_id: id, status: "failed", error_code: "SATELLITE_PROVIDER_ERROR" },
    { analysis_id: id, status: "completed", result: { ...result, status: "failed" } },
    { analysis_id: id, status: "completed", result: null },
  ])("rejeita falha ou resposta terminal inválida", async (terminal) => {
    fetchMock.mockResolvedValueOnce(respond({ analysis_id: id, status: "in_progress" }, 202))
      .mockResolvedValueOnce(respond(terminal));
    const assertion = expect(runAnalysis(values)).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(2000);
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("preserva erros HTTP do polling", async () => {
    fetchMock.mockResolvedValueOnce(respond({ analysis_id: id, status: "in_progress" }, 202))
      .mockResolvedValueOnce(respond({ error: { code: "ANALYSIS_NOT_FOUND", message: "Análise não encontrada." } }, 404));
    const assertion = expect(runAnalysis(values)).rejects.toMatchObject({ code: "ANALYSIS_NOT_FOUND", status: 404 });
    await vi.advanceTimersByTimeAsync(2000);
    await assertion;
  });

  it("interrompe polling quando a área muda ou o componente desmonta", async () => {
    fetchMock.mockResolvedValueOnce(respond({ analysis_id: id, status: "in_progress" }, 202));
    const controller = new AbortController();
    const assertion = expect(runAnalysis(values, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await assertion;
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
