import { AlertTriangle, CheckCircle2, CircleDot, Clock3, Scissors, ShieldQuestion } from "lucide-react";
import type { AoiVisualState } from "@/lib/map/aoi-visual-state";
import { MAP_STYLES, type MapStyleId } from "@/lib/map/config";

const stateIcons = {
  editing: CircleDot,
  pending_validation: Clock3,
  valid: CheckCircle2,
  invalid: AlertTriangle,
  cut: Scissors,
  no_cut: CheckCircle2,
  inconclusive: ShieldQuestion,
} as const;

export function MapLegend({ mapStyle, aoiState, hasGeometry }: { mapStyle: MapStyleId; aoiState: AoiVisualState; hasGeometry: boolean }) {
  const Icon = stateIcons[aoiState.id];
  return (
    <div className="map-legend" aria-label="Legenda do mapa">
      <span className="map-legend-base">Base: <strong>{MAP_STYLES[mapStyle].label}</strong></span>
      {hasGeometry ? (
        <span><i className="legend-swatch" style={{ borderColor: aoiState.color, backgroundColor: `${aoiState.color}2e` }} /><Icon size={14} />{aoiState.label}</span>
      ) : null}
    </div>
  );
}
