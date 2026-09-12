"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CalendarRange, CheckCircle2, ChevronLeft, ChevronRight, Crosshair, FileClock, Gauge, Leaf, Loader2, MapPinned, RefreshCw, Scissors, ShieldQuestion, Trash2 } from "lucide-react";
import { useRef, useState, type ComponentType } from "react";
import { createPortal } from "react-dom";
import { clearAnalysisHistory, getAnalysisDetail, hideAnalysisFromHistory, listAnalyses } from "@/lib/api/analyses";
import type { AnalysisHistoryItem, AnalysisHistoryPage, GeometryDocument } from "@/lib/schemas/analyses";
import type { PolygonGeometry } from "@/lib/map/geometry";
import { formatAnalysisQuality, formatArea, formatConfidence, formatDateBR, formatRecommendation, formatRecommendationSummary } from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

const PAGE_SIZE = 20;
const decisionIcons: Record<"cortar" | "nao_cortar" | "inconclusivo", ComponentType<{ size?: number }>> = {
  cortar: Scissors, nao_cortar: CheckCircle2, inconclusivo: ShieldQuestion,
};
type DeleteConfirmation = { kind: "item"; entry: AnalysisHistoryItem } | { kind: "all" };

function formatSavedAt(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function polygonGeometry(value: GeometryDocument | null | undefined): PolygonGeometry | null {
  if (value?.type === "Polygon" && Array.isArray(value.coordinates)) return value as PolygonGeometry;
  if (value?.type === "Feature" && value.geometry?.type === "Polygon" && Array.isArray(value.geometry.coordinates)) {
    return value.geometry as unknown as PolygonGeometry;
  }
  return null;
}

function polygonBounds(geometry: PolygonGeometry | null) {
  const points = geometry?.coordinates.flat() ?? [];
  if (points.length === 0) return null;
  const longitudes = points.map(([longitude]) => longitude);
  const latitudes = points.map(([, latitude]) => latitude);
  return {
    west: Math.min(...longitudes), south: Math.min(...latitudes),
    east: Math.max(...longitudes), north: Math.max(...latitudes),
  };
}

export function HistoryView({ onStartNewAnalysis, onOpenWorkspace }: {
  onStartNewAnalysis: () => void;
  onOpenWorkspace: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<DeleteConfirmation | null>(null);
  const deletionInFlightRef = useRef(false);
  const queryClient = useQueryClient();
  const restoreHistoricalAnalysis = useAnalysisStore((state) => state.restoreHistoricalAnalysis);
  const historyKey = ["analysis-history", PAGE_SIZE, offset] as const;
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => listAnalyses({ limit: PAGE_SIZE, offset }),
    staleTime: 0,
    refetchOnMount: "always",
  });

  const openAnalysis = async (entry: AnalysisHistoryItem) => {
    setOpeningId(entry.analysis_id);
    setOpenError(null);
    try {
      const detail = await getAnalysisDetail(entry.analysis_id);
      const geometry = polygonGeometry(detail.geometry);
      const hasMapTarget = Boolean(detail.geometry || entry.centroid);
      restoreHistoricalAnalysis(geometry, detail.result, undefined, hasMapTarget ? {
        geometry: detail.geometry ?? null, bounds: polygonBounds(geometry), centroid: entry.centroid ?? null,
        road_ref: entry.road_ref ?? null, road_name: entry.road_name ?? null,
        section_id: entry.section_id ?? null,
      } : undefined);
      onOpenWorkspace();
    } catch {
      setOpenError("Não foi possível abrir esta análise. Tente novamente.");
    } finally {
      setOpeningId(null);
    }
  };

  const entries = history.data?.items ?? [];
  const total = history.data?.total ?? 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + entries.length, total);
  const hideOne = useMutation({
    mutationFn: hideAnalysisFromHistory,
    onSuccess: (_response, analysisId) => {
      setActionError(null);
      queryClient.setQueryData<AnalysisHistoryPage>(historyKey, (current) => current ? {
        ...current,
        total: Math.max(0, current.total - 1),
        items: current.items.filter((item) => item.analysis_id !== analysisId),
      } : current);
      if (entries.length === 1 && offset > 0) setOffset(Math.max(0, offset - PAGE_SIZE));
      setConfirmation(null);
      void queryClient.invalidateQueries({ queryKey: ["analysis-history"] });
    },
    onError: (error) => {
      if (process.env.NODE_ENV !== "production") console.error("Falha ao ocultar análise do Histórico.", error);
      setActionError("Não foi possível excluir esta análise do Histórico.");
      setConfirmation(null);
    },
    onSettled: () => { deletionInFlightRef.current = false; },
  });
  const hideAll = useMutation({
    mutationFn: clearAnalysisHistory,
    onSuccess: () => {
      setActionError(null);
      queryClient.setQueriesData<AnalysisHistoryPage>(
        { queryKey: ["analysis-history"] },
        (current) => current ? { ...current, total: 0, items: [] } : current,
      );
      setOffset(0);
      setConfirmation(null);
      void queryClient.invalidateQueries({ queryKey: ["analysis-history"] });
    },
    onError: (error) => {
      if (process.env.NODE_ENV !== "production") console.error("Falha ao limpar o Histórico.", error);
      setActionError("Não foi possível limpar o Histórico.");
      setConfirmation(null);
    },
    onSettled: () => { deletionInFlightRef.current = false; },
  });
  const deleting = hideOne.isPending || hideAll.isPending;

  const confirmDelete = () => {
    if (!confirmation || deleting || deletionInFlightRef.current) return;
    deletionInFlightRef.current = true;
    if (confirmation.kind === "all") hideAll.mutate();
    else hideOne.mutate(confirmation.entry.analysis_id);
  };

  return (
    <section className="secondary-view" aria-labelledby="secondary-view-history">
      <header>
        <span>Acompanhamento</span>
        <h1 id="secondary-view-history">Histórico</h1>
        <p>Consulte as análises realizadas e acompanhe o histórico dos trechos monitorados.</p>
      </header>

      {history.isLoading ? (
        <div className="view-empty" role="status"><Loader2 size={30} className="animate-spin" aria-hidden="true" /><strong>Carregando histórico</strong><p>Consultando as análises persistidas.</p></div>
      ) : history.isError ? (
        <div className="view-empty" role="alert"><AlertCircle size={30} aria-hidden="true" /><strong>Não foi possível carregar o histórico</strong><p>Verifique a conexão com a API e tente novamente.</p><button type="button" className="secondary-button" onClick={() => history.refetch()}><RefreshCw size={16} aria-hidden="true" />Tentar novamente</button></div>
      ) : entries.length === 0 ? (
        <div className="view-empty"><FileClock size={30} aria-hidden="true" /><strong>Nenhuma análise registrada</strong><p>Execute uma análise para que o resultado apareça neste histórico.</p><button type="button" className="secondary-button" onClick={onStartNewAnalysis}><Leaf size={16} aria-hidden="true" />Ir para Nova análise</button></div>
      ) : (
        <>
          <div className="view-toolbar">
            <span>{first}–{last} de {total} {total === 1 ? "análise" : "análises"}</span>
            <div className="history-toolbar-actions">
              <button type="button" className="quiet-action" onClick={() => history.refetch()} disabled={history.isFetching || deleting}><RefreshCw size={15} className={history.isFetching ? "animate-spin" : undefined} aria-hidden="true" />Atualizar</button>
              <button type="button" className="quiet-action danger" onClick={() => { setActionError(null); setConfirmation({ kind: "all" }); }} disabled={deleting}><Trash2 size={15} aria-hidden="true" />Limpar histórico</button>
            </div>
          </div>
          {openError ? <p className="form-error" role="alert">{openError}</p> : null}
          {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
          <ul className="history-list">
            {entries.map((entry) => {
              const decision = entry.decision ?? "inconclusivo";
              const Icon = decisionIcons[decision];
              const roadLabel = [entry.road_ref, entry.road_name].filter(Boolean).join(" · ");
              const triggerLabel = entry.analysis_trigger === "automatic_viewport" ? "Automática" : "Manual";
              const period = entry.period_start && entry.period_end ? `${formatDateBR(entry.period_start)} a ${formatDateBR(entry.period_end)}` : "Não informado";
              return (
                <li key={entry.analysis_id} className={`history-card ${decision}`}>
                  <div className="history-card-head">
                    <span className="decision-tag" data-decision={decision}><Icon size={15} />{formatRecommendation(decision)}</span>
                    <span className="history-trigger-badge" title={`Origem: análise ${triggerLabel.toLowerCase()}`}>{triggerLabel}</span>
                    {roadLabel ? <span className="history-road-badge" title={`Rodovia monitorada: ${roadLabel}`}>{roadLabel}</span> : null}
                    <time dateTime={entry.created_at}>{formatSavedAt(entry.created_at)}</time>
                    <button type="button" className="icon-action" aria-label={`Excluir análise ${entry.analysis_id} do histórico`} onClick={(event) => { event.stopPropagation(); setActionError(null); setConfirmation({ kind: "item", entry }); }} disabled={deleting}><Trash2 size={15} aria-hidden="true" /></button>
                  </div>
                  <p className="history-card-summary">{formatRecommendationSummary(entry.summary || "Resumo operacional não informado.")}</p>
                  <dl className="history-card-metrics">
                    <div><Gauge size={15} /><dt>Confiança</dt><dd>{entry.confidence ? formatConfidence(entry.confidence) : "Não informada"}</dd></div>
                    <div><Leaf size={15} /><dt>Qualidade</dt><dd>{formatAnalysisQuality(entry.analysis_quality_status ?? null)}</dd></div>
                    <div><MapPinned size={15} /><dt>Área selecionada</dt><dd>{formatArea(entry.selected_area_m2 ?? null)}</dd></div>
                    <div><CalendarRange size={15} /><dt>Período</dt><dd>{period}</dd></div>
                  </dl>
                  <div className="history-card-actions"><button type="button" className="text-action" onClick={() => openAnalysis(entry)} disabled={openingId === entry.analysis_id}>{openingId === entry.analysis_id ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Crosshair size={15} aria-hidden="true" />}Abrir análise</button></div>
                </li>
              );
            })}
          </ul>
          {total > PAGE_SIZE ? (
            <nav className="history-card-actions" aria-label="Paginação do histórico">
              <button type="button" className="text-action" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={15} aria-hidden="true" />Anterior</button>
              <button type="button" className="text-action" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Próxima<ChevronRight size={15} aria-hidden="true" /></button>
            </nav>
          ) : null}
        </>
      )}
      {confirmation ? createPortal(
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="history-delete-title">
          <div className="logout-modal-card">
            <header className="logout-modal-header">
              <Trash2 size={20} className="logout-modal-icon" aria-hidden="true" />
              <h2 id="history-delete-title">{confirmation.kind === "all" ? "Limpar todo o histórico?" : "Excluir esta análise?"}</h2>
            </header>
            <p className="logout-modal-body">{confirmation.kind === "all" ? "Todas as análises serão removidas da visualização do Histórico." : "Ela será removida da visualização do Histórico."}</p>
            <footer className="logout-modal-actions">
              <button type="button" className="secondary-button" onClick={() => setConfirmation(null)} disabled={deleting}>Cancelar</button>
              <button type="button" className="primary-button logout-confirm-btn" onClick={confirmDelete} disabled={deleting}>{deleting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Trash2 size={15} aria-hidden="true" />}{confirmation.kind === "all" ? "Limpar histórico" : "Excluir"}</button>
            </footer>
          </div>
        </div>,
        document.body,
      ) : null}
    </section>
  );
}
