import { z } from "zod";

export const toApiUrl = (path: string) => path;

const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.array(z.unknown()).default([]),
  }),
});

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    response = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch (cause) {
    console.error("Falha de rede ao acessar o serviço de análise.", cause);
    throw new ApiError(
      "NETWORK_ERROR",
      "Não foi possível conectar ao serviço de análise. Tente novamente em alguns instantes.",
      0,
    );
  }
  const body: unknown = response.status === 204
    ? undefined
    : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.location.replace("/login");
    }
    const parsed = errorEnvelopeSchema.safeParse(body);
    throw new ApiError(
      parsed.success ? parsed.data.error.code : "NETWORK_ERROR",
      parsed.success ? parsed.data.error.message : "A API retornou uma resposta inesperada.",
      response.status,
    );
  }
  return schema.parse(body);
}
