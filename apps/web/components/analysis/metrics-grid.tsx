import { BarChart3, CalendarRange, Gauge, Percent, Sigma, Target } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const numberValue = (value: unknown, digits = 3) => typeof value === "number" ? value.toFixed(digits) : "-";

export function MetricsGrid({ result }: { result: AnalysisResponse }) {
  const metrics = result.recommendation.metrics;
  const dateRange = result.summary.date_range_effectively_processed as { start?: string; end?: string } | undefined;
  const items = [
    { label: "NDVI atual", value: numberValue(metrics.current_ndvi_mean), icon: Gauge },
    { label: "Mediana historica", value: numberValue(metrics.historical_median), icon: Sigma },
    { label: "Percentil atual", value: numberValue(metrics.current_percentile, 1), suffix: "%", icon: Percent },
    { label: "Tendencia recente", value: numberValue(metrics.recent_trend, 4), icon: BarChart3 },
    { label: "Observacoes validas", value: String(metrics.observation_count ?? result.timeseries.length), icon: Target },
    { label: "Intervalo analisado", value: dateRange?.start && dateRange?.end ? `${dateRange.start.slice(0, 10)} a ${dateRange.end.slice(0, 10)}` : "Sem intervalo", icon: CalendarRange },
  ];
  return <section className="metrics-grid" aria-label="Metricas principais">{items.map(({ label, value, suffix, icon: Icon }) => <div className="metric" key={label}><Icon size={18} /><span>{label}</span><strong>{value}{suffix}</strong></div>)}</section>;
}
