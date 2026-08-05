"use client";

import ReactECharts from "echarts-for-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export function TimeseriesChart({ result }: { result: AnalysisResponse }) {
  const records = [...result.timeseries].sort((a, b) => String(a.datetime).localeCompare(String(b.datetime)));
  const dates = records.map((record) => String(record.datetime).slice(0, 10));
  const historical = numeric(result.recommendation.metrics.historical_median);
  const option = {
    animation: false,
    color: ["#6d4aff", "#e08b2c", "#72798a"],
    tooltip: { trigger: "axis", valueFormatter: (value: unknown) => numeric(value)?.toFixed(3) ?? "-" },
    legend: { top: 0, left: 0, textStyle: { color: "#5d6472" } },
    grid: { top: 45, right: 26, bottom: 65, left: 52 },
    xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { color: "#6c7280" } },
    yAxis: { type: "value", min: -1, max: 1, interval: 0.5, axisLabel: { color: "#6c7280" }, splitLine: { lineStyle: { color: "#e6e8ed" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 12 }],
    series: [
      { name: "NDVI medio", type: "line", symbolSize: 8, data: records.map((record) => numeric(record.ndvi_mean)), connectNulls: false, markPoint: records.length ? { symbol: "pin", symbolSize: 38, data: [{ name: "Atual", coord: [dates.length - 1, numeric(records.at(-1)?.ndvi_mean)] }] } : undefined },
      { name: "NDVI mediano", type: "line", symbol: "rect", symbolSize: 7, data: records.map((record) => numeric(record.ndvi_median)), connectNulls: false },
      { name: "Mediana historica", type: "line", symbol: "none", lineStyle: { type: "dashed", width: 1.5 }, data: records.map(() => historical) },
    ],
  };

  return (
    <section className="chart-panel" aria-labelledby="timeseries-title">
      <div className="section-heading"><div><span>SERIE TEMPORAL</span><h2 id="timeseries-title">NDVI consolidado por dia</h2></div><p>{records.length} observacoes</p></div>
      {records.length ? <ReactECharts option={option} style={{ height: 360, width: "100%" }} notMerge /> : <div className="empty-state">A analise nao produziu observacoes validas para o grafico.</div>}
    </section>
  );
}
