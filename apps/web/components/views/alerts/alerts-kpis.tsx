import { AlertTriangle, BellDot, CheckCircle2, Eye, RefreshCcw } from "lucide-react";

type AlertsKpisProps = {
  totalActive: number;
  highPriority: number;
  newCount: number;
  monitoringCount: number;
  onRefresh?: () => void;
  isFetching?: boolean;
};

export function AlertsKpis({
  totalActive,
  highPriority,
  newCount,
  monitoringCount,
  onRefresh,
  isFetching = false,
}: AlertsKpisProps) {
  return (
    <div className="alerts-kpi-section">
      {onRefresh && (
        <div className="alerts-kpi-toolbar">
          <button
            type="button"
            className="alerts-refresh-btn"
            onClick={onRefresh}
            disabled={isFetching}
            data-testid="refresh-alerts-btn"
            aria-label="Atualizar alertas"
          >
            <RefreshCcw size={13} className={isFetching ? "animate-spin" : ""} aria-hidden="true" />
            <span>Atualizar</span>
          </button>
        </div>
      )}

      <div className="alerts-kpi-grid" role="region" aria-label="Resumo operacional de alertas">
        <div className="alerts-kpi-card" data-testid="kpi-active">
          <div className="alerts-kpi-icon alerts-kpi-icon--active">
            <CheckCircle2 size={16} aria-hidden="true" />
          </div>
          <div className="alerts-kpi-content">
            <span className="alerts-kpi-label">Ativos</span>
            <strong className="alerts-kpi-value">{totalActive}</strong>
          </div>
        </div>

        <div className="alerts-kpi-card" data-testid="kpi-high-priority">
          <div className="alerts-kpi-icon alerts-kpi-icon--high">
            <AlertTriangle size={16} aria-hidden="true" />
          </div>
          <div className="alerts-kpi-content">
            <span className="alerts-kpi-label">Alta prioridade</span>
            <strong className="alerts-kpi-value">{highPriority}</strong>
          </div>
        </div>

        <div className="alerts-kpi-card" data-testid="kpi-new">
          <div className="alerts-kpi-icon alerts-kpi-icon--new">
            <BellDot size={16} aria-hidden="true" />
          </div>
          <div className="alerts-kpi-content">
            <span className="alerts-kpi-label">Novos</span>
            <strong className="alerts-kpi-value">{newCount}</strong>
          </div>
        </div>

        <div className="alerts-kpi-card" data-testid="kpi-monitoring">
          <div className="alerts-kpi-icon alerts-kpi-icon--monitoring">
            <Eye size={16} aria-hidden="true" />
          </div>
          <div className="alerts-kpi-content">
            <span className="alerts-kpi-label">Em acompanhamento</span>
            <strong className="alerts-kpi-value">{monitoringCount}</strong>
          </div>
        </div>
      </div>
    </div>
  );
}
