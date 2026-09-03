"use client";

import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api/analyses";

export function AppHeader() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 60_000, retry: 1 });
  const online = health.data?.status === "ok";
  return (
    <header className="app-header">
      <div className="app-header-status" aria-live="polite">
        <span className={online ? "status-dot online" : "status-dot"} aria-hidden="true" />
        <span data-testid="api-status">{health.isLoading ? "Verificando API" : online ? "API operacional" : "API indisponível"}</span>
      </div>
    </header>
  );
}
