"use client";

import ReactECharts from "echarts-for-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export function TimeseriesChart({ result }: { result: AnalysisResponse }) {
  const records = [...result.timeseries].sort((a, b) => String(a.datetime).localeCompare(String(b.datetime)));
  const dates = records.map((record) => String(record.datetime).slice(0, 10));
  const option = {
    animation: false,
    color: ["#6546d7"],
    tooltip: { trigger: "axis", valueFormatter: (value: unknown) => numeric(value)?.toFixed(3) ?? "-" },
    grid: { top: 20, right: 26, bottom: 65, left: 52 },
    xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { color: "#6c7280" } },
    yAxis: { type: "value", min: -1, max: 1, interval: 0.5, axisLabel: { color: "#6c7280" }, splitLine: { lineStyle: { color: "#e6e8ed" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 12 }],
    series: [
      { name: "NDVI", type: "line", symbolSize: 8, lineStyle: { width: 3 }, areaStyle: { opacity: 0.06 }, data: records.map((record) => numeric(record.ndvi_mean)), connectNulls: false, markPoint: records.length ? { symbol: "pin", symbolSize: 38, data: [{ name: "Atual", coord: [dates.length - 1, numeric(records.at(-1)?.ndvi_mean)] }] } : undefined },
    ],
  };

  return (
    <section className="chart-panel" aria-labelledby="timeseries-title">
      <div className="section-heading"><div><span>ÍNDICE NDVI NO PERÍODO ANALISADO</span><h2 id="timeseries-title">Evolução da vegetação</h2></div><p>{records.length} observações</p></div>
      {records.length ? <ReactECharts option={option} style={{ height: 360, width: "100%" }} notMerge /> : <div className="empty-state">A análise não produziu observações válidas para o gráfico.</div>}
    </section>
  );
}
