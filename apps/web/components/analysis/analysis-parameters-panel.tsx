import { CalendarRange, CloudSun, Layers3, ScanLine } from "lucide-react";
import { useAnalysisStore } from "@/stores/analysis-store";

export function AnalysisParametersPanel() {
  const state = useAnalysisStore();
  return (
    <div className="workspace-panel-content parameter-panel">
      <div className="panel-section-heading"><CalendarRange size={18} /><div><strong>Periodo de observacao</strong><span>Datas inclusivas da consulta STAC.</span></div></div>
      <div className="compact-form two-columns">
        <label>Data inicial<input aria-label="Data inicial" type="date" value={state.startDate} onChange={(event) => state.setField("startDate", event.target.value)} /></label>
        <label>Data final<input aria-label="Data final" type="date" value={state.endDate} onChange={(event) => state.setField("endDate", event.target.value)} /></label>
      </div>
      <div className="panel-section-heading"><CloudSun size={18} /><div><strong>Qualidade e selecao</strong><span>Filtros aplicados antes da recomendacao.</span></div></div>
      <div className="compact-form">
        <label>Cobertura maxima de nuvens (%)<input aria-label="Cobertura maxima de nuvens" type="number" min="0" max="100" value={state.maxCloudCover} onChange={(event) => state.setField("maxCloudCover", Number(event.target.value))} /></label>
        <label>Quantidade maxima de cenas<input aria-label="Quantidade maxima de cenas" type="number" min="1" max="100" value={state.maxScenes} onChange={(event) => state.setField("maxScenes", Number(event.target.value))} /></label>
        <label>Pixels validos minimos (%)<input aria-label="Pixels validos minimos" type="number" min="0" max="100" value={state.minValidPixelPercentage} onChange={(event) => state.setField("minValidPixelPercentage", Number(event.target.value))} /></label>
        <label>Agregacao diaria<select aria-label="Agregacao diaria" value={state.dailyAggregation} onChange={(event) => state.setField("dailyAggregation", event.target.value as typeof state.dailyAggregation)}><option value="best">Melhor cena</option><option value="median">Mediana diaria</option><option value="none">Sem consolidacao</option></select></label>
      </div>
      <div className="parameter-note"><Layers3 size={16} /><p>As cenas sao selecionadas da mais recente para a mais antiga e apresentadas em ordem cronologica.</p></div>
      <div className="parameter-note"><ScanLine size={16} /><p>A recomendacao permanece experimental e nao representa medicao da altura da vegetacao.</p></div>
    </div>
  );
}

