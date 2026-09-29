import { AlertTriangle, Crosshair, RotateCcw } from "lucide-react";
import type { MapTool } from "@/stores/analysis-store";

export type MapLoadStatus = "loading" | "ready" | "error";

export function MapStatus({ status, tool, onRetry }: { status: MapLoadStatus; tool: MapTool; onRetry: () => void }) {
  if (status === "error") {
    return (
      <div className="map-message error" role="alert">
        <AlertTriangle size={16} />
        <span>Não foi possível carregar a base cartográfica.</span>
        <button type="button" className="map-retry-button" onClick={onRetry}>
          <RotateCcw size={14} />
          Tentar novamente
        </button>
      </div>
    );
  }
  if (status === "loading") {
    return <div className="map-message" role="status"><span className="map-loader" />Carregando base cartográfica...</div>;
  }
  if (tool === "draw") {
    return <div className="map-message instruction"><Crosshair size={16} />Clique para adicionar pontos. Clique no primeiro ponto para concluir.</div>;
  }
  if (tool === "edit") {
    return <div className="map-message instruction"><Crosshair size={16} />Arraste os vértices para ajustar a área.</div>;
  }
  return null;
}
