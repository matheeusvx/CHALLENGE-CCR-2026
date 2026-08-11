import { AlertTriangle, CalendarRange, Crosshair, Gauge, Leaf, MapPinned, RotateCcw } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  analyzedAreaSquareMeters,
  decisionLabels,
  formatOperationalArea,
  formatOperationalDate,
  levelLabels,
  recommendationReasonLabel,
} from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

export function AnalysisResultSidebar({ result, onRetry }: { result?: AnalysisResponse; onRetry: () => void }) {
  const requestFit = useAnalysisStore((state) => state.requestGeometryFit);
  if (!result) {
    return <div className="result-panel-empty"><Leaf size={28} /><strong>Nenhum resultado disponivel</strong><p>Valide a area e execute a analise para preencher esta etapa.</p></div>;
  }
  const range = result.analysis_period;
  const quality = analysisQualityStatus(result);
  const reasons = [...new Set([
    ...result.recommendation.reasons,
    ...result.recommendation.blocking_reasons,
  ])];
  return (
    <div className="workspace-panel-content result-sidebar">
      <div className={`decision-block ${result.recommendation.decision}`} data-decision={result.recommendation.decision}><span>RECOMENDACAO EXPERIMENTAL</span><strong>{decisionLabels[result.recommendation.decision]}</strong><p>{result.recommendation.summary}</p></div>
      <dl className="result-quick-metrics">
        <div><Gauge size={16} /><dt>Confianca</dt><dd>{levelLabels[result.recommendation.confidence]}</dd></div>
        <div><Leaf size={16} /><dt>Qualidade</dt><dd>{quality ? levelLabels[quality] : "Nao informada"}</dd></div>
        <div><MapPinned size={16} /><dt>Area</dt><dd>{formatOperationalArea(analyzedAreaSquareMeters(result))}</dd></div>
        <div><CalendarRange size={16} /><dt>Periodo</dt><dd>{formatOperationalDate(range.start_date)} a {formatOperationalDate(range.end_date)}</dd></div>
      </dl>
      <section className={`result-reasons${result.recommendation.blocking_reasons.length ? " blocking" : ""}`}>
        <h3>{result.recommendation.blocking_reasons.length ? <AlertTriangle size={15} /> : null}Por que esta conclusao?</h3>
        {reasons.length ? <ul>{reasons.map((reason) => <li key={reason}>{recommendationReasonLabel(reason)}</li>)}</ul> : <p>Nenhum motivo adicional registrado.</p>}
      </section>
      <div className="panel-actions"><button type="button" className="secondary-button" onClick={requestFit}><Crosshair size={16} />Enquadrar area analisada</button><button type="button" className="quiet-action" onClick={onRetry}><RotateCcw size={15} />Executar novamente</button></div>
    </div>
  );
}
