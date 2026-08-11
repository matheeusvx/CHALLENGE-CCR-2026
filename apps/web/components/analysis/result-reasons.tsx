import { AlertTriangle, ListChecks } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { recommendationReasonLabel } from "@/lib/utils/recommendation";

export function ResultReasons({ result }: { result: AnalysisResponse }) {
  const reasons = [...new Set([
    ...result.recommendation.reasons,
    ...result.recommendation.blocking_reasons,
  ])];
  const hasAttention = result.recommendation.blocking_reasons.length > 0;

  return (
    <section className={`operational-reasons${hasAttention ? " requires-attention" : ""}`} aria-labelledby="result-reasons-title">
      <div className="section-heading">
        <div>
          <span>JUSTIFICATIVA</span>
          <h2 id="result-reasons-title">Por que o sistema chegou a esta conclusão?</h2>
        </div>
        {hasAttention ? <AlertTriangle size={20} aria-label="Conclusão requer atenção" /> : <ListChecks size={20} aria-hidden="true" />}
      </div>
      {reasons.length
        ? <ul>{reasons.map((reason) => <li key={reason}>{recommendationReasonLabel(reason)}</li>)}</ul>
        : <p>O resultado foi determinado pelas condições resumidas na recomendação.</p>}
    </section>
  );
}
