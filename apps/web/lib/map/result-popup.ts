import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { formatConfidence, formatDateBR, formatRecommendation } from "@/lib/utils/recommendation";

export type ResultPopupPresentation = {
  decision: string;
  confidence: string;
  period: string;
  state: AnalysisResponse["recommendation"]["decision"];
};

export function getResultPopupPresentation(result: AnalysisResponse): ResultPopupPresentation {
  return {
    decision: formatRecommendation(result.recommendation.decision),
    confidence: formatConfidence(result.recommendation.confidence),
    period: `${formatDateBR(result.analysis_period.start_date)} a ${formatDateBR(result.analysis_period.end_date)}`,
    state: result.recommendation.decision,
  };
}
