import type { HistoryEntry } from "@/stores/history-store";

export type ProductivityPeriod = "7d" | "30d" | "all";

export type RoadStat = {
  road: string;
  count: number;
  percentage: number;
};

export type TimelinePoint = {
  date: string;
  label: string;
  count: number;
  areaM2: number;
};

export type ProductivityStats = {
  period: ProductivityPeriod;
  totalAnalyses: number;
  totalAreaM2: number;
  averageAreaM2: number;
  decisions: {
    cortar: number;
    nao_cortar: number;
    inconclusivo: number;
  };
  decisionPercentages: {
    cortar: number;
    nao_cortar: number;
    inconclusivo: number;
  };
  analysesLast7Days: number;
  analysesLast30Days: number;
  mostAnalyzedRoad: string | null;
  mostAnalyzedRoadCount: number;
  roadDistribution: RoadStat[];
  timeline: TimelinePoint[];
};

/**
 * Retorna a área em metros quadrados de uma análise do histórico.
 */
export function getEntryAreaM2(entry: HistoryEntry): number {
  const area =
    entry.response.selected_area_m2 ??
    entry.response.effective_analysis_area_m2 ??
    entry.geometryValidation?.area_square_meters ??
    0;
  return typeof area === "number" && !isNaN(area) && area > 0 ? area : 0;
}

/**
 * Deriva a rodovia representativa de uma análise a partir das zonas de segmentação espacial.
 * Deduplica as referências por análise e escolhe a rodovia de forma determinística
 * (maior frequência na análise, com desempate alfabético).
 * Retorna null se não houver road_ref válido.
 */
export function getRepresentativeRoad(entry: HistoryEntry): string | null {
  const zones = entry.response.spatial_segmentation?.zones;
  if (!zones || !Array.isArray(zones) || zones.length === 0) {
    return null;
  }

  const validRoads = zones
    .map((z) => (typeof z.road_ref === "string" ? z.road_ref.trim() : ""))
    .filter(Boolean);

  if (validRoads.length === 0) {
    return null;
  }

  // Contagem de frequência dentro desta análise específica
  const roadCounts = new Map<string, number>();
  for (const road of validRoads) {
    roadCounts.set(road, (roadCounts.get(road) ?? 0) + 1);
  }

  // Ordena por maior frequência e desempate alfabético
  const sorted = Array.from(roadCounts.entries()).sort((a, b) => {
    if (b[1] !== a[1]) return b[1] - a[1];
    return a[0].localeCompare(b[0]);
  });

  return sorted[0]?.[0] ?? null;
}

/**
 * Filtra análises pelo período solicitado com base no savedAt.
 */
export function filterEntriesByPeriod(
  entries: HistoryEntry[],
  period: ProductivityPeriod,
  referenceTime = Date.now()
): HistoryEntry[] {
  if (period === "all") return entries;

  const days = period === "7d" ? 7 : 30;
  const cutoffTime = referenceTime - days * 24 * 60 * 60 * 1000;

  return entries.filter((e) => {
    const time = new Date(e.savedAt).getTime();
    return !isNaN(time) && time >= cutoffTime;
  });
}

/**
 * Calcula todas as métricas de produtividade a partir das entradas do histórico.
 */
export function computeProductivityStats(
  entries: HistoryEntry[],
  period: ProductivityPeriod = "all",
  referenceTime = Date.now()
): ProductivityStats {
  const filtered = filterEntriesByPeriod(entries, period, referenceTime);
  const totalAnalyses = filtered.length;

  const totalAreaM2 = filtered.reduce((acc, e) => acc + getEntryAreaM2(e), 0);
  const averageAreaM2 = totalAnalyses > 0 ? totalAreaM2 / totalAnalyses : 0;

  // Decisões
  const decisions = {
    cortar: 0,
    nao_cortar: 0,
    inconclusivo: 0,
  };

  for (const e of filtered) {
    const d = e.response.recommendation?.decision;
    if (d === "cortar") decisions.cortar += 1;
    else if (d === "nao_cortar") decisions.nao_cortar += 1;
    else if (d === "inconclusivo") decisions.inconclusivo += 1;
  }

  const decisionPercentages = {
    cortar: totalAnalyses > 0 ? Math.round((decisions.cortar / totalAnalyses) * 100) : 0,
    nao_cortar: totalAnalyses > 0 ? Math.round((decisions.nao_cortar / totalAnalyses) * 100) : 0,
    inconclusivo: totalAnalyses > 0 ? Math.round((decisions.inconclusivo / totalAnalyses) * 100) : 0,
  };

  // Análises globais nos últimos 7 e 30 dias (independente do filtro atual)
  const analysesLast7Days = filterEntriesByPeriod(entries, "7d", referenceTime).length;
  const analysesLast30Days = filterEntriesByPeriod(entries, "30d", referenceTime).length;

  // Distribuição por rodovia (cada análise computa no máximo 1 rodovia representativa)
  const roadCounts = new Map<string, number>();
  for (const e of filtered) {
    const road = getRepresentativeRoad(e);
    if (road) {
      roadCounts.set(road, (roadCounts.get(road) ?? 0) + 1);
    }
  }

  const sortedRoads = Array.from(roadCounts.entries()).sort((a, b) => {
    if (b[1] !== a[1]) return b[1] - a[1];
    return a[0].localeCompare(b[0]);
  });

  const totalRoadAnalyses = sortedRoads.reduce((acc, [, c]) => acc + c, 0);
  const roadDistribution: RoadStat[] = sortedRoads.map(([road, count]) => ({
    road,
    count,
    percentage: totalRoadAnalyses > 0 ? Math.round((count / totalRoadAnalyses) * 100) : 0,
  }));

  const mostAnalyzedRoad = sortedRoads[0]?.[0] ?? null;
  const mostAnalyzedRoadCount = sortedRoads[0]?.[1] ?? 0;

  // Linha do tempo diária
  const dayMap = new Map<string, { count: number; areaM2: number }>();
  for (const e of filtered) {
    const d = new Date(e.savedAt);
    const key = !isNaN(d.getTime())
      ? d.toISOString().slice(0, 10)
      : "Indeterminado";

    const current = dayMap.get(key) ?? { count: 0, areaM2: 0 };
    dayMap.set(key, {
      count: current.count + 1,
      areaM2: current.areaM2 + getEntryAreaM2(e),
    });
  }

  const timeline: TimelinePoint[] = Array.from(dayMap.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, data]) => {
      const parts = date.split("-");
      const label = parts.length === 3 ? `${parts[2]}/${parts[1]}` : date;
      return {
        date,
        label,
        count: data.count,
        areaM2: data.areaM2,
      };
    });

  return {
    period,
    totalAnalyses,
    totalAreaM2,
    averageAreaM2,
    decisions,
    decisionPercentages,
    analysesLast7Days,
    analysesLast30Days,
    mostAnalyzedRoad,
    mostAnalyzedRoadCount,
    roadDistribution,
    timeline,
  };
}

/**
 * Gera o conteúdo CSV para exportação do histórico de produtividade.
 */
export function buildProductivityCsvContent(
  entries: HistoryEntry[],
  period: ProductivityPeriod = "all",
  referenceTime = Date.now()
): string {
  const filtered = filterEntriesByPeriod(entries, period, referenceTime);

  const headers = [
    "analysis_id",
    "data",
    "decisão",
    "confiança",
    "área_m2",
    "rodovia",
    "período_da_análise",
  ];

  const escapeCell = (value: string | number) => {
    const str = String(value ?? "");
    if (str.includes(";") || str.includes('"') || str.includes("\n")) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  const rows = filtered.map((entry) => {
    const id = entry.response.analysis_id ?? entry.id;
    const date = entry.savedAt ? entry.savedAt.slice(0, 19).replace("T", " ") : "";
    const decision = entry.response.recommendation?.decision ?? "";
    const confidence = entry.response.recommendation?.confidence ?? "";
    const area = Math.round(getEntryAreaM2(entry));
    const road = getRepresentativeRoad(entry) ?? "Sem dados";
    const p = entry.response.analysis_period;
    const periodStr = p?.start_date && p?.end_date ? `${p.start_date} a ${p.end_date}` : "";

    return [
      escapeCell(id),
      escapeCell(date),
      escapeCell(decision),
      escapeCell(confidence),
      escapeCell(area),
      escapeCell(road),
      escapeCell(periodStr),
    ].join(";");
  });

  // Prefixo BOM UTF-8 (\uFEFF) para garantir acentuação correta no Excel
  return "\uFEFF" + [headers.join(";"), ...rows].join("\r\n");
}

/**
 * Dispara o download no navegador do arquivo CSV de produtividade.
 */
export function downloadProductivityCsv(
  entries: HistoryEntry[],
  period: ProductivityPeriod = "all"
): void {
  if (typeof window === "undefined") return;

  const csv = buildProductivityCsvContent(entries, period);
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10);
  const fileName = `motiva-produtividade-${dateStr}.csv`;

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
