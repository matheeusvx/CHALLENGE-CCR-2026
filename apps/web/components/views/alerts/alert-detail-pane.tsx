import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  AlertCircle,
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Eye,
  Info,
  Loader2,
  MapPin,
  ShieldCheck,
  X,
} from "lucide-react";
import { getAlertDetail } from "@/lib/api/alerts";
import type {
  AlertListItem,
  AlertMapTarget,
  AlertSeverityValue,
} from "@/lib/schemas/alerts";
import {
  formatAlertRecommendation,
  formatEventLabel,
  formatFullDateTimeBR,
  formatShortDateBR,
  getAlertHeadline,
  getAlertOperationalMessage,
  getAlertRoadLabel,
  getLastValidDecision,
  SEVERITY_LABELS,
  STATUS_LABELS,
} from "@/lib/utils/alerts";

type AlertDetailPaneProps = {
  alertId: string;
  onClose: () => void;
  onOpenTrack: (target: AlertMapTarget) => void;
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

export function AlertDetailPane({
  alertId,
  onClose,
  onOpenTrack,
  onStatusChange,
  isMutating = false,
}: AlertDetailPaneProps) {
  const [mapTargetMessage, setMapTargetMessage] = useState<string | null>(null);
  const { data: alert, isLoading, error } = useQuery({
    queryKey: ["alert-detail", alertId],
    queryFn: () => getAlertDetail(alertId),
    enabled: Boolean(alertId),
  });

  if (isLoading) {
    return (
      <aside className="alert-detail-pane" data-testid="alert-detail-loading" aria-label="Detalhes do alerta">
        <div className="alert-detail-header">
          <h2>Detalhes do alerta</h2>
          <button
            type="button"
            className="icon-action"
            onClick={onClose}
            aria-label="Fechar detalhes"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="alert-detail-loading">
          <Loader2 size={24} className="animate-spin" aria-hidden="true" />
          <span>Carregando informações do trecho...</span>
        </div>
      </aside>
    );
  }

  if (error || !alert) {
    return (
      <aside className="alert-detail-pane" data-testid="alert-detail-error" aria-label="Detalhes do alerta">
        <div className="alert-detail-header">
          <h2>Detalhes do alerta</h2>
          <button
            type="button"
            className="icon-action"
            onClick={onClose}
            aria-label="Fechar detalhes"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="alert-detail-empty">
          <AlertCircle size={24} aria-hidden="true" />
          <strong>Não foi possível carregar os detalhes</strong>
          <p>Tente novamente mais tarde.</p>
        </div>
      </aside>
    );
  }

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
  const hasMapTarget = Boolean(
    alert.map_target.geometry || alert.map_target.bounds || alert.map_target.centroid,
  );

  return (
    <aside
      className={`alert-detail-pane severity--${alert.severity}`}
      data-testid="alert-detail-pane"
      aria-label={`Detalhes de ${headline}`}
    >
      <div className="alert-detail-header">
        <div>
          <span className="alert-detail-eyebrow">Trecho monitorado</span>
          <h2 className="alert-detail-title">{headline}</h2>
        </div>
        <button
          type="button"
          className="icon-action"
          onClick={onClose}
          aria-label="Fechar painel de detalhes"
          data-testid="close-detail-btn"
        >
          <X size={18} aria-hidden="true" />
        </button>
      </div>

      <div className="alert-detail-content">
        {/* Rodovia e Metadados Operacionais */}
        <section className="detail-section">
          <div className="detail-road-card">
            <span className="detail-label">Rodovia</span>
            <strong className="detail-value road-name">{roadLabel}</strong>
          </div>

          <div className="detail-meta-grid">
            <div className="detail-meta-item">
              <span className="detail-label">Severidade</span>
              <span className={`severity-badge severity-badge--${alert.severity}`}>
                <SeverityIcon severity={alert.severity} />
                <span>{SEVERITY_LABELS[alert.severity]}</span>
              </span>
            </div>

            <div className="detail-meta-item">
              <span className="detail-label">Status</span>
              <span className={`status-badge status-badge--${alert.status}`}>
                {STATUS_LABELS[alert.status]}
              </span>
            </div>

            <div className="detail-meta-item">
              <span className="detail-label">Recomendação atual</span>
              <strong className={`rec-chip rec-chip--${alert.current_recommendation ?? "inconclusivo"}`}>
                {currRec ?? "INCONCLUSIVO"}
              </strong>
            </div>

            {prevRec && (
              <div className="detail-meta-item">
                <span className="detail-label">Recomendação anterior</span>
                <strong className={`rec-chip rec-chip--${alert.previous_recommendation}`}>
                  {prevRec}
                </strong>
              </div>
            )}
          </div>
        </section>

        {/* Mensagem e Contexto Operacional */}
        <section className="detail-section">
          <span className="detail-label">Situação operacional</span>
          <p className="detail-message">{message}</p>

          {alert.type === "RECOMMENDATION_CHANGED" && prevRec && currRec && (
            <div className="detail-transition-box">
              <span>{prevRec}</span>
              <ArrowRight size={14} aria-hidden="true" />
              <span>{currRec}</span>
            </div>
          )}

          {alert.type === "REOBSERVATION_REQUIRED" && lastDecision && (
            <div className="detail-last-decision">
              <small>Última decisão válida:</small>
              <strong>{lastDecision}</strong>
            </div>
          )}
        </section>

        {/* Datas operacionais */}
        <section className="detail-section detail-dates">
          <div className="detail-meta-item">
            <span className="detail-label">Ativo desde</span>
            <time dateTime={alert.first_detected_at}>
              {formatFullDateTimeBR(alert.first_detected_at)}
            </time>
          </div>

          <div className="detail-meta-item">
            <span className="detail-label">Última atualização</span>
            <time dateTime={alert.updated_at}>
              {formatFullDateTimeBR(alert.updated_at)}
            </time>
          </div>
        </section>

        {/* Histórico / Timeline simples (sem JSON técnico) */}
        <section className="detail-section" aria-labelledby="detail-timeline-title">
          <span id="detail-timeline-title" className="detail-label">
            Histórico de eventos
          </span>

          {alert.timeline && alert.timeline.length > 0 ? (
            <ol className="detail-timeline">
              {alert.timeline.map((event) => {
                const eventText = formatEventLabel(event, alert.type);
                return (
                  <li key={event.id} className="timeline-item">
                    <div className="timeline-marker" aria-hidden="true" />
                    <div className="timeline-content">
                      <time dateTime={event.occurred_at} className="timeline-date">
                        {formatShortDateBR(event.occurred_at)}
                      </time>
                      <strong className="timeline-label">{eventText}</strong>
                    </div>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="detail-empty-timeline">Nenhum evento registrado até o momento.</p>
          )}
        </section>
      </div>

      {/* Rodapé de Ações: Abrir trecho e Ações de Status */}
      <footer className="alert-detail-footer">
        <button
          type="button"
          className="primary-button open-track-btn"
          onClick={() => {
            if (!hasMapTarget) {
              setMapTargetMessage("Este alerta ainda não possui localização disponível no mapa.");
              return;
            }
            setMapTargetMessage(null);
            onOpenTrack(alert.map_target);
          }}
          data-testid="open-track-btn"
          title="Centralizar trecho no mapa do Painel"
        >
          <MapPin size={15} aria-hidden="true" />
          <span>Abrir trecho</span>
        </button>
        {mapTargetMessage ? (
          <p className="detail-empty-timeline" role="status" data-testid="map-target-warning">
            {mapTargetMessage}
          </p>
        ) : null}

        <div className="detail-status-actions">
          {alert.status === "new" && (
            <button
              type="button"
              className="secondary-button"
              disabled={isMutating}
              onClick={() => onStatusChange("seen", alert)}
              data-testid="detail-action-seen"
            >
              <Eye size={14} aria-hidden="true" />
              <span>Marcar como visto</span>
            </button>
          )}

          {(alert.status === "new" || alert.status === "seen") && (
            <button
              type="button"
              className="secondary-button"
              disabled={isMutating}
              onClick={() => onStatusChange("monitoring", alert)}
              data-testid="detail-action-monitoring"
            >
              <CheckCircle2 size={14} aria-hidden="true" />
              <span>Acompanhar</span>
            </button>
          )}

          {alert.status !== "resolved" && (
            <button
              type="button"
              className="secondary-button detail-action-resolve"
              disabled={isMutating}
              onClick={() => onStatusChange("resolved", alert)}
              data-testid="detail-action-resolve"
            >
              <ShieldCheck size={14} aria-hidden="true" />
              <span>Resolver</span>
            </button>
          )}
        </div>
      </footer>
    </aside>
  );
}
