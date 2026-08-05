import { Crosshair, Maximize, MousePointer2, Pencil, Redo2, Trash2, Undo2 } from "lucide-react";
import type { MapTool } from "@/stores/analysis-store";

type Props = {
  tool: MapTool;
  hasGeometry: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onTool: (tool: MapTool) => void;
  onDelete: () => void;
  onFit: () => void;
  onUndo: () => void;
  onRedo: () => void;
};

const ToolButton = ({ label, active, disabled, onClick, children }: { label: string; active?: boolean; disabled?: boolean; onClick: () => void; children: React.ReactNode }) => (
  <button type="button" className={active ? "map-tool active" : "map-tool"} aria-label={label} title={label} aria-pressed={active} disabled={disabled} onClick={onClick}>{children}</button>
);

export function DrawingControls(props: Props) {
  return (
    <div className="drawing-controls" aria-label="Ferramentas de geometria">
      <ToolButton label="Navegar no mapa" active={props.tool === "navigate"} onClick={() => props.onTool("navigate")}><MousePointer2 size={18} /></ToolButton>
      <ToolButton label="Desenhar poligono" active={props.tool === "draw"} onClick={() => props.onTool("draw")}><Pencil size={18} /></ToolButton>
      <ToolButton label="Editar vertices" active={props.tool === "edit"} disabled={!props.hasGeometry} onClick={() => props.onTool("edit")}><Crosshair size={18} /></ToolButton>
      <span className="map-tool-separator" />
      <ToolButton label="Desfazer alteracao" disabled={!props.canUndo} onClick={props.onUndo}><Undo2 size={18} /></ToolButton>
      <ToolButton label="Refazer alteracao" disabled={!props.canRedo} onClick={props.onRedo}><Redo2 size={18} /></ToolButton>
      <ToolButton label="Enquadrar geometria" disabled={!props.hasGeometry} onClick={props.onFit}><Maximize size={18} /></ToolButton>
      <ToolButton label="Excluir geometria" disabled={!props.hasGeometry} onClick={props.onDelete}><Trash2 size={18} /></ToolButton>
    </div>
  );
}
