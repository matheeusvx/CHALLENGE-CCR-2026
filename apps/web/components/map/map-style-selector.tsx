import { Map, Mountain } from "lucide-react";
import type { MapStyleId } from "@/lib/map/config";

export function MapStyleSelector({ active }: { active: MapStyleId }) {
  return (
    <div className="map-style-selector" role="group" aria-label="Selecionar mapa-base">
      <button type="button" className={active === "operational" ? "active" : ""} aria-pressed={active === "operational"}>
        <Map size={15} />
        Operacional
      </button>
      <button type="button" disabled title="Terreno temporariamente indisponivel" aria-label="Terreno temporariamente indisponivel">
        <Mountain size={15} />
        Terreno
      </button>
      <span className="map-style-unavailable">Terreno temporariamente indisponivel</span>
    </div>
  );
}
