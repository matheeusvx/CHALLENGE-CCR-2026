import type { AnalysisResponse, SpatialZone } from "@/lib/schemas/analyses";

const reasonLabels: Record<string, string> = {
  current_percentile_below_or_equal_50: "A vegetação está abaixo do nível considerado alto no histórico recente.",
  stable_or_decreasing_recent_trend: "A vegetação não apresenta tendência recente de crescimento.",
  current_percentile_at_or_above_high_threshold: "A vegetação está em nível alto para o histórico recente da área.",
  positive_or_stable_high_recent_trend: "A vegetação permanece em nível alto, com tendência recente estável ou crescente.",
  no_recent_confirmed_significant_drop: "Não foi identificada queda recente confirmada que indique intervenção.",
  recent_significant_drop_confirmed: "Foi identificada uma queda recente confirmada na evolução da vegetação.",
  quality_acceptable_during_drop: "As observações envolvidas nessa queda possuem qualidade adequada.",
  insufficient_observations: "Ainda não há observações suficientes para uma conclusão segura.",
  excessive_observation_gap: "O intervalo entre observações é grande demais para uma conclusão segura.",
  no_recent_observation: "Não há uma observação recente suficiente para confirmar a situação atual.",
  insufficient_valid_pixel_percentage: "A qualidade dos dados de satélite foi insuficiente em parte do período.",
  insufficient_valid_pixels: "A qualidade dos dados de satélite foi insuficiente em parte do período.",
  partial_aoi_coverage: "Parte relevante da área não possui cobertura adequada.",
  partial_raster_coverage: "Parte relevante da área não possui cobertura adequada.",
  unacceptable_observation_quality: "A qualidade geral das observações não sustenta uma conclusão segura.",
  contradictory_series: "As observações apresentam sinais contraditórios no período.",
  insufficient_trend_data: "Ainda não há dados suficientes para confirmar uma tendência recente.",
  insufficient_data_for_trend: "Ainda não há dados suficientes para confirmar uma tendência recente.",
  unconfirmed_possible_drop: "Uma possível queda recente ainda precisa de uma observação posterior de confirmação.",
  criteria_between_cut_and_no_cut: "Os indicadores estão em uma faixa intermediária e não sustentam uma decisão segura.",
};

const summaryLabels: Record<string, string> = {
  "Evidencias insuficientes para uma recomendacao de corte.": "Evidências insuficientes para uma recomendação de corte.",
  "Vegetacao abaixo do nivel historico alto sem crescimento acelerado.": "Vegetação abaixo do nível histórico alto, sem crescimento acelerado.",
  "Recomendacao experimental de corte baseada no historico local.": "Recomendação de corte baseada no histórico local.",
};

export function recommendationReasonLabel(reason: string) {
  return reasonLabels[reason] ?? "Foi registrado um fator adicional que requer avaliação operacional.";
}

export function formatRecommendationSummary(summary: string) {
  return summaryLabels[summary] ?? summary;
}

export const decisionLabels = {
  cortar: "CORTAR",
  nao_cortar: "NÃO CORTAR",
  inconclusivo: "INCONCLUSIVO",
} as const;

export const levelLabels = { high: "Alta", medium: "Média", low: "Baixa" } as const;

export function formatRecommendation(decision: AnalysisResponse["recommendation"]["decision"]) {
  return decisionLabels[decision];
}

export function formatConfidence(level: AnalysisResponse["recommendation"]["confidence"]) {
  return levelLabels[level];
}

export type EffectiveRecommendation = {
  primaryDecision: "cortar" | "nao_cortar" | "inconclusivo";
  s2Decision: "cortar" | "nao_cortar" | "inconclusivo";
  isMultisource: boolean;
  influenced: boolean;
  fusionRule?: "B" | null;
  fusionReason?: string | null;
  s1Status?: string | null;
  confidence: "high" | "medium" | "low";
  summary: string;
};

export function getEffectiveRecommendation(result: AnalysisResponse): EffectiveRecommendation {
  const experimental = result.multisource?.experimental_fusion;
  const s2Decision = result.recommendation.decision;
  const primaryDecision =
    experimental?.final_recommendation ??
    (result as { final_recommendation?: "cortar" | "nao_cortar" | "inconclusivo" }).final_recommendation ??
    s2Decision;

  if (experimental && (experimental.multisource_recommendation || experimental.final_recommendation)) {
    return {
      primaryDecision,
      s2Decision,
      isMultisource: true,
      influenced: Boolean(experimental.sentinel1_influenced_decision),
      fusionRule: experimental.fusion_rule ?? null,
      fusionReason: experimental.fusion_reason ?? null,
      s1Status: experimental.sentinel1_temporal_status ?? null,
      confidence: result.recommendation.confidence,
      summary: result.recommendation.summary,
    };
  }

  return {
    primaryDecision,
    s2Decision,
    isMultisource: false,
    influenced: false,
    confidence: result.recommendation.confidence,
    summary: result.recommendation.summary,
  };
}

export function formatAnalysisQuality(level: "high" | "medium" | "low" | null) {
  return level ? levelLabels[level] : "Não informada";
}

export function analysisQualityStatus(result: AnalysisResponse): "high" | "medium" | "low" | null {
  const quality = result.summary.analysis_quality;
  if (!quality || typeof quality !== "object" || Array.isArray(quality)) return null;
  const status = (quality as Record<string, unknown>).status;
  return status === "high" || status === "medium" || status === "low" ? status : null;
}

export function selectedAreaSquareMeters(result: AnalysisResponse): number | null {
  const area = result.selected_area_m2 ?? result.aoi.area_square_meters;
  return typeof area === "number" && Number.isFinite(area) ? area : null;
}

export function analyzedAreaSquareMeters(result: AnalysisResponse): number | null {
  return selectedAreaSquareMeters(result);
}

export function effectiveAnalysisPercentage(result: AnalysisResponse): number | null {
  const percentage = result.effective_analysis_pct;
  return typeof percentage === "number" && Number.isFinite(percentage) ? percentage : null;
}

export function formatPercentage(value: number) {
  return `${Math.round(value).toLocaleString("pt-BR")}%`;
}

export function formatDateBR(value?: string) {
  if (!value) return "-";
  const [year, month, day] = value.slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : value;
}

export function formatArea(value: number | null) {
  return value === null
    ? "Não informada"
    : `${Math.round(value).toLocaleString("pt-BR")} m²`;
}

export const formatOperationalDate = formatDateBR;
export const formatOperationalArea = formatArea;

export type ZoneAreaStats = {
  totalArea: number;
  totalZones: number;
  cutArea: number;
  cutCount: number;
  cutPct: number;
  noCutArea: number;
  noCutCount: number;
  noCutPct: number;
  inconclusiveArea: number;
  inconclusiveCount: number;
  inconclusivePct: number;
  coveragePct?: number | null;
};

export function calculateZoneAreaStats(zones: SpatialZone[], coveragePct?: number | null): ZoneAreaStats {
  const totalZones = zones.length;
  let totalArea = 0;
  let cutArea = 0;
  let cutCount = 0;
  let noCutArea = 0;
  let noCutCount = 0;
  let inconclusiveArea = 0;
  let inconclusiveCount = 0;

  for (const zone of zones) {
    const area = typeof zone.area_m2 === "number" && Number.isFinite(zone.area_m2) ? zone.area_m2 : 0;
    totalArea += area;
    if (zone.recommendation === "cortar") {
      cutArea += area;
      cutCount += 1;
    } else if (zone.recommendation === "nao_cortar") {
      noCutArea += area;
      noCutCount += 1;
    } else if (zone.recommendation === "inconclusivo") {
      inconclusiveArea += area;
      inconclusiveCount += 1;
    }
  }

  const cutPct = totalArea > 0 ? (cutArea / totalArea) * 100 : 0;
  const noCutPct = totalArea > 0 ? (noCutArea / totalArea) * 100 : 0;
  const inconclusivePct = totalArea > 0 ? (inconclusiveArea / totalArea) * 100 : 0;

  return {
    totalArea,
    totalZones,
    cutArea,
    cutCount,
    cutPct,
    noCutArea,
    noCutCount,
    noCutPct,
    inconclusiveArea,
    inconclusiveCount,
    inconclusivePct,
    coveragePct,
  };
}
