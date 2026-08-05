"use client";

import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api/analyses";

export function AppHeader() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 60_000 });
  const online = health.data?.status === "ok";
  return (
    <header className="app-header">
      <div>
        <span className={online ? "status-dot online" : "status-dot"} aria-hidden="true" />
        <span data-testid="api-status">{health.isLoading ? "Verificando API" : online ? "API operacional" : "API indisponivel"}</span>
      </div>
      <span className="header-context">Monitoramento Sentinel-2</span>
    </header>
  );
}
