"use client";

import { CalendarRange, CheckCircle2, Crosshair, FileClock, Gauge, Leaf, MapPinned, Scissors, ShieldQuestion, Trash2 } from "lucide-react";
import type { ComponentType } from "react";
import {
  analysisQualityStatus,
  formatAnalysisQuality,
  formatArea,
  formatConfidence,
  formatDateBR,
  formatRecommendation,
  formatRecommendationSummary,
  selectedAreaSquareMeters,
} from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";
import { useHistoryStore, type HistoryEntry } from "@/stores/history-store";

const decisionIcons: Record<HistoryEntry["response"]["recommendation"]["decision"], ComponentType<{ size?: number }>> = {
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
  const entries = useHistoryStore((state) => state.entries);
  const removeEntry = useHistoryStore((state) => state.removeEntry);
  const clear = useHistoryStore((state) => state.clear);
  const restoreHistoricalAnalysis = useAnalysisStore((state) => state.restoreHistoricalAnalysis);

  const openAnalysis = (entry: HistoryEntry) => {
    restoreHistoricalAnalysis(entry.geometry, entry.response, entry.geometryValidation);
    onOpenWorkspace();
  };

  const clearAll = () => {
    if (window.confirm("Deseja apagar todo o histórico de análises?")) clear();
  };

  return (
    <section className="secondary-view" aria-labelledby="secondary-view-history">
      <header>
        <span>Acompanhamento</span>
        <h1 id="secondary-view-history">Histórico</h1>
        <p>Todas as análises executadas nesta sessão ficam registradas aqui, salvas neste navegador.</p>
      </header>

      {entries.length === 0 ? (
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
            <span>{entries.length} {entries.length === 1 ? "análise registrada" : "análises registradas"}</span>
            <button type="button" className="quiet-action danger" onClick={clearAll}>
              <Trash2 size={15} aria-hidden="true" />Limpar histórico
            </button>
          </div>

          <ul className="history-list">
            {entries.map((entry) => {
              const recommendation = entry.response.recommendation;
              const quality = analysisQualityStatus(entry.response);
              const period = entry.response.analysis_period;
              const Icon = decisionIcons[recommendation.decision];
              return (
                <li key={`${entry.id}-${entry.savedAt}`} className={`history-card ${recommendation.decision}`}>
                  <div className="history-card-head">
                    <span className="decision-tag" data-decision={recommendation.decision}>
                      <Icon size={15} />{formatRecommendation(recommendation.decision)}
                    </span>
                    <time dateTime={entry.savedAt}>{formatSavedAt(entry.savedAt)}</time>
                    <button type="button" className="icon-action" onClick={() => removeEntry(entry.id)} aria-label="Remover do histórico">
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  </div>

                  <p className="history-card-summary">{formatRecommendationSummary(recommendation.summary)}</p>

                  <dl className="history-card-metrics">
                    <div><Gauge size={15} /><dt>Confiança</dt><dd>{formatConfidence(recommendation.confidence)}</dd></div>
                    <div><Leaf size={15} /><dt>Qualidade</dt><dd>{formatAnalysisQuality(quality)}</dd></div>
                    <div><MapPinned size={15} /><dt>Área selecionada</dt><dd>{formatArea(selectedAreaSquareMeters(entry.response))}</dd></div>
                    <div><CalendarRange size={15} /><dt>Período</dt><dd>{formatDateBR(period.start_date)} a {formatDateBR(period.end_date)}</dd></div>
                  </dl>

                  <div className="history-card-actions">
                    <button type="button" className="text-action" onClick={() => openAnalysis(entry)}>
                      <Crosshair size={15} aria-hidden="true" />Abrir análise
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
