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
  getEffectiveRecommendation,
} from "@/lib/utils/recommendation";

export function RecommendationPanel({ result }: { result: AnalysisResponse }) {
  const effective = getEffectiveRecommendation(result);
  const quality = analysisQualityStatus(result);
  const Icon = effective.primaryDecision === "cortar"
    ? Scissors
    : effective.primaryDecision === "nao_cortar"
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
      className={`recommendation ${effective.primaryDecision}`}
      data-decision={effective.primaryDecision}
      data-quality={quality ?? "unknown"}
      aria-labelledby="recommendation-title"
    >
      <div className="recommendation-main">
        <div className="recommendation-icon" aria-hidden="true"><Icon size={32} /></div>
        <div className="recommendation-decision">
          <span>{effective.isMultisource ? "Resultado multissensor" : hasSegmentation ? "Resultado consolidado" : "Recomendação"}</span>
          <h2 id="recommendation-title">{formatRecommendation(effective.primaryDecision)}</h2>
          {effective.isMultisource ? (
            <div className="multisource-audit-row">
              <span className="s2-audit-tag">Sentinel-2: {formatRecommendation(effective.s2Decision)}</span>
              {effective.influenced ? (
                <span className="s1-influenced-tag">Sentinel-1 influenciou esta análise</span>
              ) : null}
              {effective.fusionRule ? (
                <span className="fusion-rule-tag">Regra {effective.fusionRule}</span>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="result-statuses" aria-label="Confiança e qualidade da análise">
        <div><span>Confiança da recomendação</span><strong>{formatConfidence(effective.confidence)}</strong></div>
        <div title="Indica a confiabilidade dos dados de satélite utilizados nesta análise.">
          <span>Qualidade da análise</span>
          <strong>{formatAnalysisQuality(quality)}</strong>
        </div>
      </div>
      <p>{formatRecommendationSummary(effective.summary)}</p>

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

