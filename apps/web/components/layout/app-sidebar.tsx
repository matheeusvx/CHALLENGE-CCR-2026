import { Activity, Database, FileClock, Leaf, Settings } from "lucide-react";

const navigation = [
  { label: "Nova análise", icon: Activity, active: true },
  { label: "Execuções", icon: FileClock },
  { label: "Fontes", icon: Database },
  { label: "Configurações", icon: Settings },
];

export function AppSidebar() {
  return (
    <aside className="sidebar" aria-label="Navegação principal">
      <div className="brand-mark"><Leaf size={20} aria-hidden="true" /><span>MVI</span></div>
      <nav>
        {navigation.map(({ label, icon: Icon, active }) => (
          <button key={label} className={active ? "nav-item active" : "nav-item"} type="button" disabled={!active}>
            <Icon size={18} aria-hidden="true" /><span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-foot"><span>Sentinel-2 L2A</span><strong>Modo experimental</strong></div>
    </aside>
  );
}
