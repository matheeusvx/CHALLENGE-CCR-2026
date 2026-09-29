import { BarChart3, MapPinned } from "lucide-react";
import type { WorkspaceTab } from "@/stores/analysis-store";

const tabs: Array<{ id: WorkspaceTab; label: string; icon: typeof MapPinned }> = [
  { id: "area", label: "Área", icon: MapPinned },
  { id: "result", label: "Resultado", icon: BarChart3 },
];

export function WorkspaceTabs({ active, onChange, hasResult, "data-tour": dataTour }: { active: WorkspaceTab; onChange: (tab: WorkspaceTab) => void; hasResult: boolean; "data-tour"?: string }) {
  return <div className="workspace-tabs" role="tablist" aria-label="Etapas da análise" data-tour={dataTour}>{tabs.map(({ id, label, icon: Icon }) => <button key={id} type="button" role="tab" aria-selected={active === id} className={active === id ? "active" : ""} disabled={id === "result" && !hasResult} onClick={() => onChange(id)}><Icon size={16} />{label}{id === "result" && hasResult ? <i aria-label="Resultado disponível" /> : null}</button>)}</div>;
}
