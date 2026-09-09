import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth } from "@/lib/api/analyses";

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
});
