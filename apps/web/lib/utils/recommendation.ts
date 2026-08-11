import type { AnalysisResponse } from "@/lib/schemas/analyses";

const reasonLabels: Record<string, string> = {
  current_percentile_below_or_equal_50: "A vegetacao esta abaixo do nivel considerado alto no historico recente.",
  stable_or_decreasing_recent_trend: "A vegetacao nao apresenta tendencia recente de crescimento.",
  current_percentile_at_or_above_high_threshold: "A vegetacao esta em nivel alto para o historico recente da area.",
  positive_or_stable_high_recent_trend: "A vegetacao permanece em nivel alto, com tendencia recente estavel ou crescente.",
  no_recent_confirmed_significant_drop: "Nao foi identificada queda recente confirmada que indique intervencao.",
  recent_significant_drop_confirmed: "Foi identificada uma queda recente confirmada na evolucao da vegetacao.",
  quality_acceptable_during_drop: "As observacoes envolvidas nessa queda possuem qualidade adequada.",
  insufficient_observations: "Ainda nao ha observacoes suficientes para uma conclusao segura.",
  excessive_observation_gap: "O intervalo entre observacoes e grande demais para uma conclusao segura.",
  no_recent_observation: "Nao ha uma observacao recente suficiente para confirmar a situacao atual.",
  insufficient_valid_pixel_percentage: "A qualidade dos dados de satelite foi insuficiente em parte do periodo.",
  insufficient_valid_pixels: "A qualidade dos dados de satelite foi insuficiente em parte do periodo.",
  partial_aoi_coverage: "Parte relevante da area nao possui cobertura adequada.",
  partial_raster_coverage: "Parte relevante da area nao possui cobertura adequada.",
  unacceptable_observation_quality: "A qualidade geral das observacoes nao sustenta uma conclusao segura.",
  contradictory_series: "As observacoes apresentam sinais contraditorios no periodo.",
  insufficient_trend_data: "Ainda nao ha dados suficientes para confirmar uma tendencia recente.",
  insufficient_data_for_trend: "Ainda nao ha dados suficientes para confirmar uma tendencia recente.",
  unconfirmed_possible_drop: "Uma possivel queda recente ainda precisa de uma observacao posterior de confirmacao.",
  criteria_between_cut_and_no_cut: "Os indicadores estao em uma faixa intermediaria e nao sustentam uma decisao segura.",
};

export function recommendationReasonLabel(reason: string) {
  return reasonLabels[reason] ?? "Foi registrado um fator adicional que requer avaliacao operacional.";
}

export const decisionLabels = {
  cortar: "CORTAR",
  nao_cortar: "NAO CORTAR",
  inconclusivo: "INCONCLUSIVO",
} as const;

export const levelLabels = { high: "Alta", medium: "Media", low: "Baixa" } as const;

export function analysisQualityStatus(result: AnalysisResponse): "high" | "medium" | "low" | null {
  const quality = result.summary.analysis_quality;
  if (!quality || typeof quality !== "object" || Array.isArray(quality)) return null;
  const status = (quality as Record<string, unknown>).status;
  return status === "high" || status === "medium" || status === "low" ? status : null;
}

export function analyzedAreaSquareMeters(result: AnalysisResponse): number | null {
  const area = result.aoi.area_square_meters;
  return typeof area === "number" && Number.isFinite(area) ? area : null;
}

export function formatOperationalDate(value?: string) {
  if (!value) return "-";
  const [year, month, day] = value.slice(0, 10).split("-");
  return year && month && day ? `${day}/${month}/${year}` : value;
}

export function formatOperationalArea(value: number | null) {
  return value === null
    ? "Nao informada"
    : `${Math.round(value).toLocaleString("pt-BR")} m\u00B2`;
}

