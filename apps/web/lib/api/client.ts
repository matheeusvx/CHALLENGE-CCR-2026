import { z } from "zod";

const apiUrlSchema = z.string().url();
const API_URL = apiUrlSchema.parse(
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
);

export const toApiUrl = (path: string) => new URL(path, API_URL).toString();

const OPERATOR_SCOPE_STORAGE_KEY = "motiva.operator-scope-id";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let memoryOperatorScopeId: string | undefined;

export function getOperatorScopeId(): string {
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage.getItem(OPERATOR_SCOPE_STORAGE_KEY);
      if (stored && UUID_PATTERN.test(stored)) return stored;
      const generated = globalThis.crypto.randomUUID();
      window.localStorage.setItem(OPERATOR_SCOPE_STORAGE_KEY, generated);
      return generated;
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }
  memoryOperatorScopeId ??= globalThis.crypto.randomUUID();
  return memoryOperatorScopeId;
}

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
    headers.set("X-Operator-Scope", getOperatorScopeId());
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers,
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
