import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { formatConfidence, formatDateBR, formatRecommendation, getEffectiveRecommendation } from "@/lib/utils/recommendation";

export type ResultPopupPresentation = {
  decision: string;
  confidence: string;
  period: string;
  state: AnalysisResponse["recommendation"]["decision"];
};

export function getResultPopupPresentation(result: AnalysisResponse): ResultPopupPresentation {
  const effective = getEffectiveRecommendation(result);
  return {
    decision: formatRecommendation(effective.primaryDecision),
    confidence: formatConfidence(effective.confidence),
    period: `${formatDateBR(result.analysis_period.start_date)} a ${formatDateBR(result.analysis_period.end_date)}`,
    state: effective.primaryDecision,
  };
}
