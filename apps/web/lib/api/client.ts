import { z } from "zod";

const apiUrlSchema = z.string().url();
const API_URL = apiUrlSchema.parse(
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
);

export const toApiUrl = (path: string) => new URL(path, API_URL).toString();

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
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
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
    const parsed = errorEnvelopeSchema.safeParse(body);
    throw new ApiError(
      parsed.success ? parsed.data.error.code : "NETWORK_ERROR",
      parsed.success ? parsed.data.error.message : "A API retornou uma resposta inesperada.",
      response.status,
    );
  }
  return schema.parse(body);
}
