"use client";

import { AlertCircle, Info, Loader2, ZoomIn } from "lucide-react";
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";
import { getEffectiveRecommendation, decisionLabels } from "@/lib/utils/recommendation";

/** Mapeia decisão para classe CSS de cor semântica */
const decisionClass: Record<"cortar" | "nao_cortar" | "inconclusivo", string> = {
  nao_cortar: "auto-result-badge--no-cut",
  cortar: "auto-result-badge--cut",
  inconclusivo: "auto-result-badge--inconclusive",
};

export function AutoAnalysisPanel() {
  const enabled = useAutoAnalysisStore((state) => state.enabled);
  const uiStatus = useAutoAnalysisStore((state) => state.uiStatus);
  const result = useAutoAnalysisStore((state) => state.result);
  const setEnabled = useAutoAnalysisStore((state) => state.setEnabled);

  // Resolução da recomendação efetiva (multisource tem prioridade)
  const effective = result ? getEffectiveRecommendation(result) : null;
  const decisionKey = effective?.primaryDecision ?? null;
  const decisionLabel = decisionKey ? decisionLabels[decisionKey] : null;

  // Mostrar badge de resultado enquanto uiStatus for completed/cache_hit E existir resultado
  const showResult = (uiStatus === "completed" || uiStatus === "cache_hit") && decisionKey !== null;

  return (
    <div
      className="auto-analysis-pill"
      role="region"
      aria-label="Controle de análise automática"
      data-status={uiStatus}
      data-analysis-id={result?.analysis_id}
      data-tour="auto-analysis"
    >
      {/* Linha 1: toggle */}
      <label className="auto-analysis-switch-label" htmlFor="auto-analysis-switch">
        <input
          id="auto-analysis-switch"
          type="checkbox"
          role="switch"
          checked={enabled}
          aria-checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          aria-label="Ativar análise automática por navegação"
        />
        <span className="pill-slider" aria-hidden="true" />
        <span className="pill-text">Análise automática</span>
      </label>

      {/* Linha 2: status transitório ou badge de resultado */}
      {enabled && (
        <div className="pill-status-area" aria-live="polite">
          {uiStatus === "analyzing" ? (
            <span className="pill-status-item analyzing">
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              <span>Analisando...</span>
            </span>
          ) : uiStatus === "stabilizing" ? (
            <span className="pill-status-item stabilizing">
              <span className="pulsing-dot" aria-hidden="true" />
              <span>Estabilizando...</span>
            </span>
          ) : uiStatus === "zoom_required" ? (
            <span className="pill-status-item zoom-required" title="Aproxime o mapa (zoom ≥ 13)">
              <ZoomIn size={12} aria-hidden="true" />
              <span>Aproxime o mapa</span>
            </span>
          ) : uiStatus === "road_context_required" ? (
            <span className="pill-status-item road-context-required" title="Aproxime um pouco para identificar o trecho">
              <ZoomIn size={12} aria-hidden="true" />
              <span>Aproxime um pouco para identificar o trecho</span>
            </span>
          ) : uiStatus === "road_not_found" ? (
            <span className="pill-status-item road-not-found" title="Nenhuma rodovia monitorada identificada neste ponto">
              <Info size={12} aria-hidden="true" />
              <span>Nenhuma rodovia monitorada identificada neste ponto</span>
            </span>
          ) : uiStatus === "road_ambiguous" ? (
            <span className="pill-status-item road-ambiguous" title="Não foi possível identificar o trecho com segurança">
              <AlertCircle size={12} aria-hidden="true" />
              <span>Não foi possível identificar o trecho com segurança</span>
            </span>
          ) : uiStatus === "failed" ? (
            <span className="pill-status-item failed" title="Falha na análise automática">
              <AlertCircle size={12} aria-hidden="true" />
              <span>Falha na análise</span>
            </span>
          ) : showResult && decisionKey && decisionLabel ? (
            <span
              className={`auto-result-badge ${decisionClass[decisionKey]}`}
              data-decision={decisionKey}
              aria-label={`Recomendação automática: ${decisionLabel}`}
            >
              {decisionLabel}
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}
