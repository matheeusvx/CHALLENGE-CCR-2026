import { CheckCircle2, Scissors, ShieldQuestion } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  decisionLabels,
  levelLabels,
} from "@/lib/utils/recommendation";

export function RecommendationPanel({ result }: { result: AnalysisResponse }) {
  const recommendation = result.recommendation;
  const quality = analysisQualityStatus(result);
  const Icon = recommendation.decision === "cortar"
    ? Scissors
    : recommendation.decision === "nao_cortar"
      ? CheckCircle2
      : ShieldQuestion;

  return (
    <section
      className={`recommendation ${recommendation.decision}`}
      data-decision={recommendation.decision}
      data-quality={quality ?? "unknown"}
      aria-labelledby="recommendation-title"
    >
      <div className="recommendation-main">
        <div className="recommendation-icon" aria-hidden="true"><Icon size={30} /></div>
        <div className="recommendation-decision">
          <span>RECOMENDACAO EXPERIMENTAL</span>
          <h2 id="recommendation-title">{decisionLabels[recommendation.decision]}</h2>
        </div>
      </div>
      <div className="result-statuses" aria-label="Confianca e qualidade da analise">
        <div><span>Confianca da recomendacao</span><strong>{levelLabels[recommendation.confidence]}</strong></div>
        <div title="Indica a confiabilidade dos dados de satelite utilizados nesta analise.">
          <span>Qualidade da analise</span>
          <strong>{quality ? levelLabels[quality] : "Nao informada"}</strong>
        </div>
      </div>
      <p>{recommendation.summary}</p>
    </section>
  );
}
