import { Activity, Database, FileClock, Leaf, Settings } from "lucide-react";

const navigation = [
  { label: "Nova análise", icon: Activity, active: true },
  { label: "Histórico", icon: FileClock },
  { label: "Fontes de dados", icon: Database },
  { label: "Configurações", icon: Settings },
];

export function AppSidebar() {
  return (
    <aside className="sidebar" aria-label="Navegação principal">
      <div className="brand-mark">
        <span className="brand-icon"><Leaf size={20} aria-hidden="true" /></span>
        <span className="brand-copy"><strong>Faixa Verde</strong><small>Motiva</small></span>
      </div>
      <nav>
        {navigation.map(({ label, icon: Icon, active }) => (
          <button key={label} className={active ? "nav-item active" : "nav-item"} type="button" disabled={!active}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-foot"><span>Operação rodoviária</span><strong>Monitoramento Sentinel-2</strong></div>
    </aside>
  );
}
