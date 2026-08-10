import { AlertCircle, CalendarRange, Crosshair, Gauge, Leaf, RotateCcw } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { recommendationReasonLabel } from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

export function AnalysisResultSidebar({ result, onRetry }: { result?: AnalysisResponse; onRetry: () => void }) {
  const requestFit = useAnalysisStore((state) => state.requestGeometryFit);
  if (!result) {
    return <div className="result-panel-empty"><Leaf size={28} /><strong>Nenhum resultado disponivel</strong><p>Valide a area e execute a analise para preencher esta etapa.</p></div>;
  }
  const metrics = result.recommendation.metrics;
  const range = result.analysis_period;
  return (
    <div className="workspace-panel-content result-sidebar">
      <div className={`decision-block ${result.recommendation.decision}`}><span>RECOMENDACAO EXPERIMENTAL</span><strong>{decisionLabel(result.recommendation.decision)}</strong><p>{result.recommendation.summary}</p></div>
      <dl className="result-quick-metrics">
        <div><Gauge size={16} /><dt>Confianca</dt><dd>{confidenceLabel(result.recommendation.confidence)}</dd></div>
        <div><Leaf size={16} /><dt>NDVI atual</dt><dd>{formatMetric(metrics.current_ndvi_mean)}</dd></div>
        <div><CalendarRange size={16} /><dt>Periodo</dt><dd>{formatDate(range.start_date)} a {formatDate(range.end_date)}</dd></div>
      </dl>
      <section className="result-reasons"><h3>Motivos</h3>{result.recommendation.reasons.length ? <ul>{result.recommendation.reasons.map((reason) => <li key={reason}>{recommendationReasonLabel(reason)}</li>)}</ul> : <p>Nenhum motivo adicional registrado.</p>}</section>
      {result.recommendation.blocking_reasons.length ? <section className="result-reasons blocking"><h3><AlertCircle size={15} />Bloqueios</h3><ul>{result.recommendation.blocking_reasons.map((reason) => <li key={reason}>{recommendationReasonLabel(reason)}</li>)}</ul></section> : null}
      <div className="panel-actions"><button type="button" className="secondary-button" onClick={requestFit}><Crosshair size={16} />Enquadrar area analisada</button><button type="button" className="quiet-action" onClick={onRetry}><RotateCcw size={15} />Executar novamente</button></div>
    </div>
  );
}

function decisionLabel(value: AnalysisResponse["recommendation"]["decision"]) {
  return value === "cortar" ? "CORTAR" : value === "nao_cortar" ? "NAO CORTAR" : "INCONCLUSIVO";
}
function confidenceLabel(value: AnalysisResponse["recommendation"]["confidence"]) {
  return value === "high" ? "Alta" : value === "medium" ? "Media" : "Baixa";
}
function formatMetric(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}
function formatDate(value?: string) {
  return value ? value.slice(0, 10) : "-";
}
