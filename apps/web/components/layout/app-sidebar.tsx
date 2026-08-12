import { Activity, Database, FileClock, Leaf, Settings } from "lucide-react";

export type AppView = "analysis" | "history" | "sources" | "settings";

const navigation = [
  { id: "analysis", label: "Nova análise", icon: Activity },
  { id: "history", label: "Histórico", icon: FileClock },
  { id: "sources", label: "Fontes de dados", icon: Database },
  { id: "settings", label: "Configurações", icon: Settings },
] satisfies Array<{ id: AppView; label: string; icon: typeof Activity }>;

export function AppSidebar({ activeView, onNavigate }: { activeView: AppView; onNavigate: (view: AppView) => void }) {
  return (
    <aside className="sidebar" aria-label="Navegação principal">
      <div className="brand-mark">
        <span className="brand-icon"><Leaf size={20} aria-hidden="true" /></span>
        <span className="brand-copy"><strong>Motiva</strong><small>Faixa Verde</small></span>
      </div>
      <nav>
        {navigation.map(({ id, label, icon: Icon }) => (
          <button key={id} className={activeView === id ? "nav-item active" : "nav-item"} type="button" aria-current={activeView === id ? "page" : undefined} onClick={() => onNavigate(id)}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-foot"><span>Operação rodoviária</span><strong>Ambiente de análise</strong></div>
    </aside>
  );
}
