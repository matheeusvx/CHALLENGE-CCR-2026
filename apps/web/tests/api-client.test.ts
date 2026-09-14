import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth, hideAnalysisFromHistory } from "@/lib/api/analyses";

describe("cliente da API", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("traduz falhas de rede para uma mensagem operacional", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(getHealth()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      message: "Não foi possível conectar ao serviço de análise. Tente novamente em alguns instantes.",
      status: 0,
    });
  });

  it("aceita resposta 204 de DELETE sem tentar interpretar JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(hideAnalysisFromHistory("analysis-204")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/analyses/analysis-204",
      expect.objectContaining({ method: "DELETE" }),
    );
    const firstHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    const firstScope = firstHeaders.get("X-Operator-Scope");
    expect(firstScope).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );

    await hideAnalysisFromHistory("analysis-204");
    const secondHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Headers;
    expect(secondHeaders.get("X-Operator-Scope")).toBe(firstScope);
    expect(window.localStorage.getItem("motiva.operator-scope-id")).toBe(firstScope);
  });
});
