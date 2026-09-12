"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Bell, RefreshCcw, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { getAlertDetail, listAlerts, patchAlert } from "@/lib/api/alerts";
import { ApiError } from "@/lib/api/client";
import type {
  AlertListItem,
  AlertMapTarget,
  AlertPage,
  AlertSeverityValue,
  AlertStatusValue,
  AlertTypeValue,
} from "@/lib/schemas/alerts";
import { AlertCard } from "./alerts/alert-card";
import { AlertDetailPane } from "./alerts/alert-detail-pane";
import { AlertsFilters, type AlertRoadOption } from "./alerts/alerts-filters";
import { AlertsKpis } from "./alerts/alerts-kpis";

type AlertsViewProps = {
  onOpenTrack: (target: AlertMapTarget) => void;
};

export function AlertsView({ onOpenTrack }: AlertsViewProps) {
  const queryClient = useQueryClient();

  const [activeCategory, setActiveCategory] = useState("all");
  const [selectedRoad, setSelectedRoad] = useState("");
  const [selectedSeverity, setSelectedSeverity] = useState<AlertSeverityValue | "">("");
  const [selectedStatus, setSelectedStatus] = useState("");
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  // Mapeamento da categoria ativa para parâmetros da API
  const categoryFilter = useMemo(() => {
    switch (activeCategory) {
      case "critical":
        return { severity: "critical" as AlertSeverityValue };
      case "cut":
        return { type: "CUT_PENDING" as AlertTypeValue };
      case "changes":
        return { type: "RECOMMENDATION_CHANGED" as AlertTypeValue };
      case "reobservation":
        return { type: "REOBSERVATION_REQUIRED" as AlertTypeValue };
      case "monitoring":
        return { type: "STALE_MONITORING" as AlertTypeValue };
      default:
        return {};
    }
  }, [activeCategory]);

  const queryKey = [
    "alerts-list",
    {
      ...categoryFilter,
      severity: selectedSeverity || categoryFilter.severity,
      road: selectedRoad || undefined,
      status: (selectedStatus as AlertStatusValue) || undefined,
    },
  ];

  const {
    data: alertPage,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery<AlertPage>({
    queryKey,
    queryFn: () =>
      listAlerts({
        ...categoryFilter,
        severity: selectedSeverity || categoryFilter.severity,
        road: selectedRoad || undefined,
        status: (selectedStatus as AlertStatusValue) || undefined,
        limit: 100,
      }),
  });

  // Query global de resumo para KPIs persistentes mesmo quando filtros são aplicados
  const { data: summary, isFetching: isSummaryFetching } = useQuery({
    queryKey: ["alerts-global-summary"],
    queryFn: async () => {
      const [all, critical, high, fresh, monitoring] = await Promise.all([
        listAlerts({ limit: 200 }),
        listAlerts({ severity: "critical", limit: 1 }),
        listAlerts({ severity: "high", limit: 1 }),
        listAlerts({ status: "new", limit: 1 }),
        listAlerts({ status: "monitoring", limit: 1 }),
      ]);
      return {
        all,
        totalActive: all.active_count,
        highPriority: critical.active_count + high.active_count,
        newCount: fresh.total,
        monitoringCount: monitoring.total,
      };
    },
    staleTime: 30_000,
  });

  // Extração das rodovias disponíveis a partir dos registros globais ou locais
  const availableRoads = useMemo(() => {
    const pool = summary?.all.items ?? alertPage?.items ?? [];
    const options = new Map<string, AlertRoadOption>();
    for (const item of pool) {
      const value = item.road_ref || item.road_id || item.road_name;
      const label = [item.road_ref, item.road_name].filter(Boolean).join(" · ");
      if (value && label) options.set(value, { value, label });
    }
    return Array.from(options.values()).sort((a, b) => a.label.localeCompare(b.label));
  }, [summary?.all.items, alertPage?.items]);

  // Cálculo dos KPIs
  const kpiStats = useMemo(() => {
    if (summary) return summary;
    const items = alertPage?.items ?? [];
    return {
      totalActive: alertPage?.active_count ?? 0,
      highPriority: items.filter(
        (item) => item.status !== "resolved" && ["critical", "high"].includes(item.severity),
      ).length,
      newCount: items.filter((item) => item.status === "new").length,
      monitoringCount: items.filter((item) => item.status === "monitoring").length,
    };
  }, [summary, alertPage]);

  // Mutation com Optimistic Locking e Rollback em caso de 409
  const patchMutation = useMutation({
    mutationFn: ({
      alertId,
      status,
      version,
    }: {
      alertId: string;
      status: "seen" | "monitoring" | "resolved";
      version: number;
    }) => patchAlert(alertId, { status, version }),

    onMutate: async (variables) => {
      setConflictMessage(null);
      await queryClient.cancelQueries({ queryKey });

      const previousPage = queryClient.getQueryData<AlertPage>(queryKey);

      // Atualização otimista na lista ativa
      if (previousPage) {
        queryClient.setQueryData<AlertPage>(queryKey, {
          ...previousPage,
          items: previousPage.items.map((item) =>
            item.id === variables.alertId
              ? {
                  ...item,
                  status: variables.status,
                  version: item.version + 1,
                  updated_at: new Date().toISOString(),
                }
              : item,
          ),
          active_count:
            variables.status === "resolved"
              ? Math.max(0, previousPage.active_count - 1)
              : previousPage.active_count,
        });
      }

      return { previousPage };
    },

    onError: async (err, variables, context) => {
      // Rollback para o snapshot anterior
      if (context?.previousPage) {
        queryClient.setQueryData<AlertPage>(queryKey, context.previousPage);
      }

      // Verificação específica de erro 409 de concorrência/versão
      const is409 =
        (err instanceof ApiError && err.status === 409) ||
        (err as { status?: number }).status === 409 ||
        (err as { code?: string }).code === "ALERT_VERSION_CONFLICT";

      if (is409) {
        setConflictMessage(
          "O alerta foi atualizado em outra sessão. As informações foram recarregadas.",
        );
        try {
          const current = await queryClient.fetchQuery({
            queryKey: ["alert-detail", variables.alertId],
            queryFn: () => getAlertDetail(variables.alertId),
            staleTime: 0,
          });
          queryClient.setQueriesData<AlertPage>(
            { queryKey: ["alerts-list"] },
            (page) => page
              ? {
                  ...page,
                  items: page.items.map((item) =>
                    item.id === current.id ? { ...item, ...current } : item
                  ),
                }
              : page,
          );
        } catch {
          // A invalidacao abaixo permanece como fallback de sincronizacao.
        }
      } else {
        setConflictMessage("Não foi possível atualizar o alerta. Tente novamente.");
      }

      // Refetch do alerta específico e da lista para sincronizar o estado real
      void queryClient.invalidateQueries({ queryKey: ["alert-detail", variables.alertId] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-list"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-global-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-count"] });
    },

    onSuccess: (_data, variables) => {
      setConflictMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["alert-detail", variables.alertId] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-list"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-global-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts-count"] });
    },
  });

  const handleStatusChange = (
    newStatus: "seen" | "monitoring" | "resolved",
    alert: AlertListItem,
  ) => {
    patchMutation.mutate({
      alertId: alert.id,
      status: newStatus,
      version: alert.version,
    });
  };

  const items = alertPage?.items ?? [];
  const hasFiltersApplied =
    activeCategory !== "all" ||
    selectedRoad !== "" ||
    selectedSeverity !== "" ||
    selectedStatus !== "";

  return (
    <section className="secondary-view alerts-view" aria-labelledby="alerts-view-title">
      <header className="alerts-view-header page-heading">
        <div className="alerts-view-copy">
          <span className="alerts-view-eyebrow page-heading-eyebrow">Operação</span>
          <h1 id="alerts-view-title" className="page-heading-title">Alertas</h1>
          <p className="page-heading-description">
            Monitoramento de eventos críticos, mudanças na vegetação e pendências operacionais nos
            trechos rodoviários.
          </p>
        </div>
      </header>

      {/* Cards de Resumo / KPIs compactos com botão de atualização à direita */}
      <AlertsKpis
        totalActive={kpiStats.totalActive}
        highPriority={kpiStats.highPriority}
        newCount={kpiStats.newCount}
        monitoringCount={kpiStats.monitoringCount}
        onRefresh={() => {
          void refetch();
          void queryClient.invalidateQueries({ queryKey: ["alerts-global-summary"] });
          void queryClient.invalidateQueries({ queryKey: ["alerts-count"] });
        }}
        isFetching={isFetching || isSummaryFetching}
      />

      {/* Barra de Filtros compacta */}
      <AlertsFilters
        activeCategory={activeCategory}
        onCategoryChange={(category) => {
          setActiveCategory(category);
          if (category === "critical") setSelectedSeverity("");
        }}
        selectedRoad={selectedRoad}
        onRoadChange={setSelectedRoad}
        availableRoads={availableRoads}
        selectedSeverity={selectedSeverity}
        onSeverityChange={(severity) => {
          setSelectedSeverity(severity);
          if (severity && activeCategory === "critical") setActiveCategory("all");
        }}
        selectedStatus={selectedStatus}
        onStatusChange={setSelectedStatus}
      />

      {/* Alerta de conflito / mensagem discreta em caso de 409 */}
      {conflictMessage && (
        <div
          className="alert-conflict-banner"
          role="alert"
          data-testid="alert-conflict-banner"
        >
          <AlertCircle size={16} aria-hidden="true" />
          <span>{conflictMessage}</span>
          <button
            type="button"
            className="conflict-close-btn"
            onClick={() => setConflictMessage(null)}
            aria-label="Fechar aviso"
          >
            ×
          </button>
        </div>
      )}

      {/* Área principal: Lista de cards + Painel de detalhe lateral */}
      <div className={`alerts-stage ${selectedAlertId ? "has-active-detail" : ""}`}>
        {isLoading ? (
          <div className="alerts-loading-state" data-testid="alerts-loading">
            <div className="alerts-skeleton-card" />
            <div className="alerts-skeleton-card" />
            <div className="alerts-skeleton-card" />
          </div>
        ) : isError ? (
          <div className="view-empty alerts-error-state" data-testid="alerts-error">
            <AlertCircle size={32} className="error-icon" aria-hidden="true" />
            <strong>Falha ao carregar alertas operacionais</strong>
            <p>
              {error instanceof Error
                ? error.message
                : "Não foi possível sincronizar os alertas com a central de monitoramento."}
            </p>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void refetch()}
            >
              <RefreshCcw size={14} aria-hidden="true" />
              <span>Tentar novamente</span>
            </button>
          </div>
        ) : items.length === 0 ? (
          hasFiltersApplied ? (
            <div className="view-empty alerts-no-results" data-testid="alerts-no-results">
              <Bell size={28} aria-hidden="true" />
              <strong>Nenhum alerta corresponde aos filtros selecionados</strong>
              <p>Tente ajustar os critérios de filtro ou limpar a seleção atual.</p>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setActiveCategory("all");
                  setSelectedRoad("");
                  setSelectedSeverity("");
                  setSelectedStatus("");
                }}
              >
                Limpar filtros
              </button>
            </div>
          ) : (
            <div className="view-empty alerts-empty-state" data-testid="alerts-empty">
              <ShieldCheck size={36} className="empty-shield" aria-hidden="true" />
              <strong>Nenhum alerta ativo</strong>
              <p>Os trechos monitorados estão sem pendências no momento.</p>
            </div>
          )
        ) : (
          <div className="alerts-list-column">
            <div className="alerts-count-summary">
              <span>
                {items.length} {items.length === 1 ? "alerta encontrado" : "alertas encontrados"}
              </span>
            </div>

            <div className="alerts-cards-list" role="feed" aria-label="Lista de alertas operacionais">
              {items.map((alert) => (
                <AlertCard
                  key={alert.id}
                  alert={alert}
                  isSelected={selectedAlertId === alert.id}
                  onSelect={() =>
                    setSelectedAlertId(selectedAlertId === alert.id ? null : alert.id)
                  }
                  onStatusChange={handleStatusChange}
                  isMutating={patchMutation.isPending}
                />
              ))}
            </div>
          </div>
        )}

        {/* Detalhe do alerta selecionado */}
        {selectedAlertId && (
          <AlertDetailPane
            alertId={selectedAlertId}
            onClose={() => setSelectedAlertId(null)}
            onOpenTrack={onOpenTrack}
            onStatusChange={handleStatusChange}
            isMutating={patchMutation.isPending}
          />
        )}
      </div>
    </section>
  );
}
