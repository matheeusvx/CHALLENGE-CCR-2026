import { AlertTriangle, CalendarRange, CheckCircle2, Crosshair, Gauge, Leaf, MapPinned, RotateCcw } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  analyzedAreaSquareMeters,
  formatAnalysisQuality,
  formatArea,
  formatConfidence,
  formatDateBR,
  formatRecommendation,
  recommendationReasonLabel,
} from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

export function AnalysisResultSidebar({ result, onRetry }: { result?: AnalysisResponse; onRetry: () => void }) {
  const requestFit = useAnalysisStore((state) => state.requestGeometryFit);
  if (!result) {
    return <div className="result-panel-empty"><Leaf size={28} /><strong>Nenhum resultado disponível</strong><p>Valide a área e execute a análise para preencher esta etapa.</p></div>;
  }
  const range = result.analysis_period;
  const quality = analysisQualityStatus(result);
  const reasons = [...new Set([
    ...result.recommendation.reasons,
    ...result.recommendation.blocking_reasons,
  ])];
  return (
    <div className="workspace-panel-content result-sidebar">
      <div className={`decision-block ${result.recommendation.decision}`} data-decision={result.recommendation.decision}><span>RECOMENDAÇÃO</span><strong>{formatRecommendation(result.recommendation.decision)}</strong><p>{result.recommendation.summary}</p></div>
      <dl className="result-quick-metrics">
        <div><Gauge size={17} /><dt>Confiança</dt><dd>{formatConfidence(result.recommendation.confidence)}</dd></div>
        <div><Leaf size={17} /><dt>Qualidade</dt><dd>{formatAnalysisQuality(quality)}</dd></div>
        <div><MapPinned size={17} /><dt>Área analisada</dt><dd>{formatArea(analyzedAreaSquareMeters(result))}</dd></div>
        <div><CalendarRange size={17} /><dt>Período</dt><dd>{formatDateBR(range.start_date)} a {formatDateBR(range.end_date)}</dd></div>
      </dl>
      <section className={`result-reasons${result.recommendation.blocking_reasons.length ? " blocking" : ""}`}>
        <h3>{result.recommendation.blocking_reasons.length ? <AlertTriangle size={17} /> : null}Por que o sistema chegou a esta conclusão?</h3>
        {reasons.length ? <ul>{reasons.map((reason) => <li key={reason}><CheckCircle2 size={16} aria-hidden="true" /><span>{recommendationReasonLabel(reason)}</span></li>)}</ul> : <p>Nenhum motivo adicional registrado.</p>}
      </section>
      <div className="panel-actions result-actions"><button type="button" className="secondary-button" onClick={requestFit}><Crosshair size={16} />Enquadrar área analisada</button><button type="button" className="quiet-action" onClick={onRetry}><RotateCcw size={15} />Executar novamente</button></div>
    </div>
  );
}
