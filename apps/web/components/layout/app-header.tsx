"use client";

import { Database, Satellite } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api/analyses";

export function AppHeader() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 60_000 });
  const online = health.data?.status === "ok";
  return (
    <header className="app-header">
      <div className="app-header-status">
        <span className={online ? "status-dot online" : "status-dot"} aria-hidden="true" />
        <span data-testid="api-status">{health.isLoading ? "Verificando API" : online ? "API operacional" : "API indisponível"}</span>
      </div>
      <div className="header-data-source" aria-label="Fonte de dados">
        <span><Satellite size={15} aria-hidden="true" />Sentinel-2 L2A</span>
        <span><Database size={15} aria-hidden="true" />Planetary Computer</span>
      </div>
    </header>
  );
}
