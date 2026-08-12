import { ArrowLeft, Database, FileClock, Settings, ShieldCheck } from "lucide-react";
import type { AppView } from "@/components/layout/app-sidebar";

const viewContent = {
  history: {
    icon: FileClock,
    eyebrow: "Acompanhamento",
    title: "Histórico",
    description: "Consulte aqui as análises registradas quando a persistência operacional estiver habilitada.",
    detail: "Nesta versão, os resultados permanecem disponíveis durante a sessão de análise atual.",
  },
  sources: {
    icon: Database,
    eyebrow: "Dados de monitoramento",
    title: "Fontes de dados",
    description: "A análise utiliza cenas Sentinel-2 L2A consultadas no Microsoft Planetary Computer.",
    detail: "Bandas RED, NIR e SCL são processadas somente para a área delimitada pelo operador.",
  },
  settings: {
    icon: Settings,
    eyebrow: "Preferências operacionais",
    title: "Configurações",
    description: "O perfil de análise é administrado internamente para manter consistência entre as execuções.",
    detail: "Não há configurações operacionais disponíveis para alteração nesta versão.",
  },
} as const;

export function SecondaryView({ view, onBack }: { view: Exclude<AppView, "analysis">; onBack: () => void }) {
  const content = viewContent[view];
  const Icon = content.icon;

  return (
    <section className="secondary-view" aria-labelledby={`secondary-view-${view}`}>
      <header>
        <span>{content.eyebrow}</span>
        <h1 id={`secondary-view-${view}`}>{content.title}</h1>
        <p>{content.description}</p>
      </header>
      <div className="secondary-view-panel">
        <span className="secondary-view-icon"><Icon size={24} aria-hidden="true" /></span>
        <div>
          <strong>{content.title}</strong>
          <p>{content.detail}</p>
        </div>
        <span className="secondary-view-status"><ShieldCheck size={15} aria-hidden="true" />Ambiente operacional</span>
      </div>
      <button type="button" className="secondary-button secondary-view-back" onClick={onBack}><ArrowLeft size={16} aria-hidden="true" />Voltar para Nova análise</button>
    </section>
  );
}
