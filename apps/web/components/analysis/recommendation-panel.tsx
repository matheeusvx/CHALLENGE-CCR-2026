import { CheckCircle2, Scissors, ShieldQuestion } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  formatAnalysisQuality,
  formatConfidence,
  formatRecommendation,
  formatRecommendationSummary,
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
        <div className="recommendation-icon" aria-hidden="true"><Icon size={32} /></div>
        <div className="recommendation-decision">
          <span>Recomendação</span>
          <h2 id="recommendation-title">{formatRecommendation(recommendation.decision)}</h2>
        </div>
      </div>
      <div className="result-statuses" aria-label="Confiança e qualidade da análise">
        <div><span>Confiança da recomendação</span><strong>{formatConfidence(recommendation.confidence)}</strong></div>
        <div title="Indica a confiabilidade dos dados de satélite utilizados nesta análise.">
          <span>Qualidade da análise</span>
          <strong>{formatAnalysisQuality(quality)}</strong>
        </div>
      </div>
      <p>{formatRecommendationSummary(recommendation.summary)}</p>
    </section>
  );
}
