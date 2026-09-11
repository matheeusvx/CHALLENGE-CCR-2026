import {
  AlertCircle,
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Clock,
  Eye,
  Info,
  ShieldCheck,
} from "lucide-react";
import type { AlertListItem, AlertSeverityValue } from "@/lib/schemas/alerts";
import {
  formatAlertRecommendation,
  formatShortDateBR,
  getAlertHeadline,
  getAlertOperationalMessage,
  getAlertRoadLabel,
  getLastValidDecision,
  SEVERITY_LABELS,
  STATUS_LABELS,
} from "@/lib/utils/alerts";

type AlertCardProps = {
  alert: AlertListItem;
  isSelected?: boolean;
  onSelect: () => void;
  onStatusChange: (
    newStatus: "seen" | "monitoring" | "resolved",
    alert: AlertListItem,
  ) => void;
  isMutating?: boolean;
};

function SeverityIcon({ severity }: { severity: AlertSeverityValue }) {
  switch (severity) {
    case "critical":
      return <AlertOctagon size={15} aria-hidden="true" />;
    case "high":
      return <AlertTriangle size={15} aria-hidden="true" />;
    case "medium":
      return <AlertCircle size={15} aria-hidden="true" />;
    case "low":
    default:
      return <Info size={15} aria-hidden="true" />;
  }
}

export function AlertCard({
  alert,
  isSelected = false,
  onSelect,
  onStatusChange,
  isMutating = false,
}: AlertCardProps) {
  const headline = getAlertHeadline(alert);
  const roadLabel = getAlertRoadLabel(alert);
  const message = getAlertOperationalMessage(alert);
  const lastDecision = getLastValidDecision(alert);

  const prevRec = alert.previous_recommendation
    ? formatAlertRecommendation(alert.previous_recommendation)
    : null;
  const currRec = alert.current_recommendation
    ? formatAlertRecommendation(alert.current_recommendation)
    : null;

  return (
    <article
      className={`alert-card severity--${alert.severity} status--${alert.status} ${
        isSelected ? "is-selected" : ""
      }`}
      onClick={onSelect}
      data-testid={`alert-card-${alert.id}`}
      data-alert-type={alert.type}
      data-severity={alert.severity}
      data-status={alert.status}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      aria-labelledby={`alert-title-${alert.id}`}
    >
      {/* Indicador de severidade discreto na borda lateral esquerda */}
      <div className="alert-card-header">
        <div className="alert-card-meta">
          <span
            className={`severity-badge severity-badge--${alert.severity}`}
            title={`Severidade: ${SEVERITY_LABELS[alert.severity]}`}
          >
            <SeverityIcon severity={alert.severity} />
            <span>{SEVERITY_LABELS[alert.severity]}</span>
          </span>

          <span
            className={`status-badge status-badge--${alert.status}`}
            title={`Status operacional: ${STATUS_LABELS[alert.status]}`}
          >
            {STATUS_LABELS[alert.status]}
          </span>

          <span className="alert-date" title={`Detectado em ${alert.first_detected_at}`}>
            <Clock size={12} aria-hidden="true" />
            <time dateTime={alert.first_detected_at}>
              {formatShortDateBR(alert.first_detected_at)}
            </time>
          </span>
        </div>

        <button
          type="button"
          className="alert-expand-btn"
          aria-label={`Ver detalhes de ${headline}`}
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="alert-card-body">
        <h3 id={`alert-title-${alert.id}`} className="alert-card-title">
          {headline}
        </h3>

        <div className="alert-card-road" title={`Trecho: ${roadLabel}`}>
          <span>{roadLabel}</span>
        </div>

        {/* Transição de recomendação para RECOMMENDATION_CHANGED */}
        {alert.type === "RECOMMENDATION_CHANGED" && prevRec && currRec && (
          <div className="recommendation-transition" aria-label="Transição de recomendação">
            <span className={`rec-chip rec-chip--${alert.previous_recommendation}`}>
              {prevRec}
            </span>
            <ArrowRight size={13} className="transition-arrow" aria-hidden="true" />
            <span className={`rec-chip rec-chip--${alert.current_recommendation}`}>
              {currRec}
            </span>
          </div>
        )}

        {/* Mensagem operacional clara e objetiva */}
        <p className="alert-card-desc">{message}</p>

        {/* Última decisão válida para REOBSERVATION_REQUIRED se disponível */}
        {alert.type === "REOBSERVATION_REQUIRED" && lastDecision && (
          <div className="last-valid-decision">
            <small>Última decisão válida:</small>
            <strong>{lastDecision}</strong>
          </div>
        )}
      </div>

      {/* Ações operacionais rápidas */}
      <div className="alert-card-actions" onClick={(e) => e.stopPropagation()}>
        {alert.status === "new" && (
          <button
            type="button"
            className="alert-action-btn"
            disabled={isMutating}
            onClick={() => onStatusChange("seen", alert)}
            data-testid={`action-seen-${alert.id}`}
            title="Marcar como visto pelo operador"
          >
            <Eye size={13} aria-hidden="true" />
            <span>Marcar como visto</span>
          </button>
        )}

        {(alert.status === "new" || alert.status === "seen") && (
          <button
            type="button"
            className="alert-action-btn"
            disabled={isMutating}
            onClick={() => onStatusChange("monitoring", alert)}
            data-testid={`action-monitoring-${alert.id}`}
            title="Colocar trecho sob acompanhamento ativo"
          >
            <CheckCircle2 size={13} aria-hidden="true" />
            <span>Acompanhar</span>
          </button>
        )}

        {alert.status !== "resolved" && (
          <button
            type="button"
            className="alert-action-btn alert-action-btn--resolve"
            disabled={isMutating}
            onClick={() => onStatusChange("resolved", alert)}
            data-testid={`action-resolve-${alert.id}`}
            title="Resolver alerta após providência operacional"
          >
            <ShieldCheck size={13} aria-hidden="true" />
            <span>Resolver</span>
          </button>
        )}
      </div>
    </article>
  );
}
