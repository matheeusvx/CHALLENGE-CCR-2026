"use client";

import ReactECharts from "echarts-for-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { formatDateBR } from "@/lib/utils/recommendation";

const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export function TimeseriesChart({ result }: { result: AnalysisResponse }) {
  const records = [...result.timeseries].sort((a, b) => String(a.datetime).localeCompare(String(b.datetime)));
  const dates = records.map((record) => String(record.datetime).slice(0, 10));
  const option = {
    animation: false,
    color: ["#7c3aed"],
    tooltip: {
      trigger: "axis",
      backgroundColor: "#211f29",
      borderColor: "#4b455c",
      borderWidth: 1,
      textStyle: { color: "#f5f3f8", fontSize: 12 },
      valueFormatter: (value: unknown) => {
        const parsed = numeric(value);
        return parsed === null ? "-" : `NDVI ${parsed.toFixed(3)}`;
      },
    },
    grid: { top: 24, right: 26, bottom: 62, left: 54 },
    xAxis: {
      type: "category",
      data: dates.map(formatDateBR),
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#cfd3dc" } },
      axisTick: { show: false },
      axisLabel: { color: "#6b7280", fontSize: 11 },
    },
    yAxis: {
      type: "value",
      min: -1,
      max: 1,
      interval: 0.5,
      axisLabel: { color: "#6b7280", fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#e8eaf0" } },
    },
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 12, borderColor: "#d8dbe4", fillerColor: "rgba(124,58,237,.14)", handleStyle: { color: "#7c3aed" } },
    ],
    series: [
      {
        name: "NDVI médio",
        type: "line",
        symbol: "circle",
        symbolSize: 7,
        lineStyle: { width: 3, color: "#7c3aed" },
        itemStyle: { color: "#7c3aed", borderColor: "#ffffff", borderWidth: 2 },
        areaStyle: { color: "rgba(124,58,237,.08)" },
        data: records.map((record) => numeric(record.ndvi_mean)),
        connectNulls: false,
        markPoint: records.length ? {
          symbol: "pin",
          symbolSize: 40,
          label: { color: "#ffffff", fontSize: 10 },
          itemStyle: { color: "#1d1b25" },
          data: [{ name: "Atual", coord: [dates.length - 1, numeric(records.at(-1)?.ndvi_mean)] }],
        } : undefined,
      },
    ],
  };

  return (
    <section className="chart-panel" aria-labelledby="timeseries-title">
      <div className="section-heading">
        <div>
          <span>Evolução temporal</span>
          <h2 id="timeseries-title">Evolução da vegetação</h2>
          <p>Índice NDVI no período analisado</p>
        </div>
        <p>{records.length} observações</p>
      </div>
      {records.length ? <ReactECharts option={option} style={{ height: 360, width: "100%" }} notMerge /> : <div className="empty-state">A análise não produziu observações válidas para o gráfico.</div>}
    </section>
  );
}
