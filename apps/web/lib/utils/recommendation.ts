const reasonLabels: Record<string, string> = {
  current_percentile_below_or_equal_50: "NDVI atual abaixo ou igual ao percentil 50 do historico local",
  stable_or_decreasing_recent_trend: "Tendencia recente estavel ou decrescente",
  high_current_vegetation_percentile: "NDVI atual em nivel historico alto",
  stable_or_positive_recent_trend: "Tendencia recente estavel ou crescente",
  recent_confirmed_significant_drop: "Queda significativa recente confirmada por observacao posterior",
  insufficient_observations: "Quantidade insuficiente de observacoes validas",
  excessive_observation_gap: "Intervalo excessivo entre observacoes",
  no_recent_observation: "Nenhuma observacao recente disponivel",
  insufficient_valid_pixels: "Percentual insuficiente de pixels validos",
  partial_raster_coverage: "Cobertura parcial relevante da area",
  contradictory_series: "Serie temporal contraditoria",
  insufficient_data_for_trend: "Dados insuficientes para calcular tendencia",
  unconfirmed_possible_drop: "Possivel queda sem observacao posterior de confirmacao",
};

export function recommendationReasonLabel(reason: string) {
  return reasonLabels[reason] ?? reason.replaceAll("_", " ");
}

