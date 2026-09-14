import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth, hideAnalysisFromHistory } from "@/lib/api/analyses";

describe("cliente da API", () => {
  afterEach(() => {
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
      "/api/analyses/analysis-204",
      expect.objectContaining({ method: "DELETE", credentials: "same-origin" }),
    );
    const firstHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(firstHeaders.get("X-Operator-Scope")).toBeNull();

    await hideAnalysisFromHistory("analysis-204");
    const secondHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Headers;
    expect(secondHeaders.get("X-Operator-Scope")).toBeNull();
    expect(window.localStorage.getItem("motiva.operator-scope-id")).toBeNull();
  });
});
