import { Map, Mountain } from "lucide-react";
import { MAP_STYLES, type MapStyleId } from "@/lib/map/config";

const styleIcons = { operational: Map, terrain: Mountain } as const;

export function MapStyleSelector({ active, onChange }: { active: MapStyleId; onChange: (style: MapStyleId) => void }) {
  return (
    <div className="map-style-selector" role="group" aria-label="Selecionar mapa-base">
      {(Object.keys(MAP_STYLES) as MapStyleId[]).map((styleId) => {
        const style = MAP_STYLES[styleId];
        const Icon = styleIcons[styleId];
        return (
          <button
            key={style.id}
            type="button"
            className={active === styleId ? "active" : ""}
            aria-pressed={active === styleId}
            title={`Mapa-base ${style.label}`}
            onClick={() => onChange(styleId)}
          >
            <Icon size={15} />
            {style.label}
          </button>
        );
      })}
    </div>
  );
}
