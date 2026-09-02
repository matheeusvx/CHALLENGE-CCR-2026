import { AlertTriangle, CheckCircle2, Scissors, ShieldQuestion } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  calculateZoneAreaStats,
  formatAnalysisQuality,
  formatArea,
  formatConfidence,
  formatPercentage,
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

  const hasSegmentation = Boolean(
    result.spatial_segmentation?.status === "available" && (result.spatial_segmentation.zones.length > 0),
  );
  const zoneStats = hasSegmentation
    ? calculateZoneAreaStats(result.spatial_segmentation!.zones, result.spatial_segmentation!.effective_coverage_pct)
    : null;

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
          <span>{hasSegmentation ? "Resultado consolidado" : "Recomendação"}</span>
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

      {zoneStats && zoneStats.cutCount > 0 ? (
        <div className="localized-intervention-callout" role="status">
          <div className="callout-main">
            <AlertTriangle size={14} className="callout-icon" aria-hidden="true" />
            <span className="callout-title">
              {zoneStats.cutCount} {zoneStats.cutCount === 1 ? "zona requer intervenção" : "zonas requerem intervenção"}
            </span>
          </div>
          <span className="callout-detail">
            {formatArea(zoneStats.cutArea)} · {formatPercentage(zoneStats.cutPct)} da área segmentada
          </span>
        </div>
      ) : null}
    </section>
  );
}

