"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarRange,
  CheckCircle2,
  Clock3,
  Crosshair,
  FileClock,
  Gauge,
  Leaf,
  MapPinned,
  Milestone,
  RefreshCw,
  Scissors,
  ShieldQuestion,
} from "lucide-react";
import { useState, type ComponentType } from "react";
import { getAnalysisDetail, listAnalyses } from "@/lib/api/analyses";
import { normalizeGeoJson } from "@/lib/map/geometry";
import type { AnalysisHistoryItem } from "@/lib/schemas/analyses";
import {
  formatAnalysisQuality,
  formatArea,
  formatConfidence,
  formatDateBR,
  formatRecommendation,
  formatRecommendationSummary,
} from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

const decisionIcons: Record<string, ComponentType<{ size?: number }>> = {
  cortar: Scissors,
  nao_cortar: CheckCircle2,
  inconclusivo: ShieldQuestion,
};

function formatSavedAt(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function HistoryView({ onStartNewAnalysis, onOpenWorkspace }: { onStartNewAnalysis: () => void; onOpenWorkspace: () => void }) {
  const history = useQuery({ queryKey: ["analyses"], queryFn: () => listAnalyses({ limit: 100 }) });
  const restoreHistoricalAnalysis = useAnalysisStore((state) => state.restoreHistoricalAnalysis);
  const [openingId, setOpeningId] = useState<string>();
  const [openError, setOpenError] = useState<string>();

  const entries = history.data?.items ?? [];

  const openAnalysis = async (item: AnalysisHistoryItem) => {
    setOpeningId(item.analysis_id);
    setOpenError(undefined);
    try {
      const detail = await getAnalysisDetail(item.analysis_id);
      restoreHistoricalAnalysis(normalizeGeoJson(detail.geometry), detail.result);
      onOpenWorkspace();
    } catch {
      setOpenError("Não foi possível abrir esta análise. Tente novamente.");
    } finally {
      setOpeningId(undefined);
    }
  };

  return (
    <section className="secondary-view" aria-labelledby="secondary-view-history">
      <header>
        <span>Acompanhamento</span>
        <h1 id="secondary-view-history">Histórico</h1>
        <p>Registro operacional de todas as análises executadas, gravado no servidor e compartilhado entre os operadores.</p>
      </header>

      {history.isLoading ? (
        <div className="inline-loading" role="status"><Clock3 className="spin" size={16} />Carregando o histórico</div>
      ) : history.isError ? (
        <div className="view-empty">
          <AlertTriangle size={30} aria-hidden="true" />
          <strong>Não foi possível carregar o histórico</strong>
          <p>Verifique se a API está disponível e tente novamente.</p>
          <button type="button" className="secondary-button" onClick={() => history.refetch()}>
            <RefreshCw size={16} aria-hidden="true" />Tentar novamente
          </button>
        </div>
      ) : entries.length === 0 ? (
        <div className="view-empty">
          <FileClock size={30} aria-hidden="true" />
          <strong>Nenhuma análise registrada</strong>
          <p>Execute uma análise em “Nova análise” para que o resultado apareça neste histórico.</p>
          <button type="button" className="secondary-button" onClick={onStartNewAnalysis}>
            <Leaf size={16} aria-hidden="true" />Ir para Nova análise
          </button>
        </div>
      ) : (
        <>
          <div className="view-toolbar">
            <span>{history.data?.total ?? entries.length} {(history.data?.total ?? entries.length) === 1 ? "análise registrada" : "análises registradas"}</span>
            <button type="button" className="quiet-action" onClick={() => history.refetch()} disabled={history.isFetching}>
              <RefreshCw size={15} className={history.isFetching ? "spin" : undefined} aria-hidden="true" />Atualizar
            </button>
          </div>

          {openError && <div className="inline-error" role="alert"><AlertTriangle size={16} />{openError}</div>}

          <ul className="history-list">
            {entries.map((item) => {
              const decision = item.decision ?? "inconclusivo";
              const Icon = decisionIcons[decision] ?? ShieldQuestion;
              return (
                <li key={item.analysis_id} className={`history-card ${decision}`}>
                  <div className="history-card-head">
                    <span className="decision-tag" data-decision={decision}>
                      <Icon size={15} />{formatRecommendation(decision as "cortar" | "nao_cortar" | "inconclusivo")}
                    </span>
                    {item.nearest_km !== null && item.nearest_km !== undefined ? (
                      <span className="history-card-km"><Milestone size={13} aria-hidden="true" />km {item.nearest_km}</span>
                    ) : null}
                    <time dateTime={item.created_at}>{formatSavedAt(item.created_at)}</time>
                  </div>

                  {item.summary ? <p className="history-card-summary">{formatRecommendationSummary(item.summary)}</p> : null}

                  <dl className="history-card-metrics">
                    <div><Gauge size={15} /><dt>Confiança</dt><dd>{item.confidence ? formatConfidence(item.confidence) : "—"}</dd></div>
                    <div><Leaf size={15} /><dt>Qualidade</dt><dd>{formatAnalysisQuality(item.analysis_quality_status ?? null)}</dd></div>
                    <div><MapPinned size={15} /><dt>Área selecionada</dt><dd>{formatArea(item.selected_area_m2 ?? null)}</dd></div>
                    <div><CalendarRange size={15} /><dt>Período</dt><dd>{formatDateBR(item.period_start ?? undefined)} a {formatDateBR(item.period_end ?? undefined)}</dd></div>
                  </dl>

                  <div className="history-card-actions">
                    <button type="button" className="text-action" onClick={() => openAnalysis(item)} disabled={openingId === item.analysis_id}>
                      <Crosshair size={15} aria-hidden="true" />
                      {openingId === item.analysis_id ? "Abrindo…" : "Abrir análise"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
