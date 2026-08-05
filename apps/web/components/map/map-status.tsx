import { AlertTriangle, Crosshair } from "lucide-react";
import type { MapTool } from "@/stores/analysis-store";

export function MapStatus({ loading, error, tool }: { loading: boolean; error?: string; tool: MapTool }) {
  if (error) {
    return <div className="map-message error" role="alert"><AlertTriangle size={16} />{error}</div>;
  }
  if (loading) {
    return <div className="map-message" role="status"><span className="map-loader" />Carregando mapa</div>;
  }
  if (tool === "draw") {
    return <div className="map-message instruction"><Crosshair size={16} />Clique para adicionar pontos. Clique no primeiro ponto para concluir.</div>;
  }
  if (tool === "edit") {
    return <div className="map-message instruction"><Crosshair size={16} />Arraste os vertices para ajustar a area.</div>;
  }
  return null;
}
