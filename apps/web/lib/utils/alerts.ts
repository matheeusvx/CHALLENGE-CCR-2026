import type {
  AlertDetail,
  AlertEventResponse,
  AlertListItem,
  AlertRecommendationValue,
  AlertSeverityValue,
  AlertStatusValue,
  AlertTypeValue,
} from "@/lib/schemas/alerts";

export function formatAlertRecommendation(
  recommendation: AlertRecommendationValue | null | undefined,
): string {
  if (!recommendation) return "INCONCLUSIVO";
  switch (recommendation) {
    case "cortar":
      return "CORTAR";
    case "nao_cortar":
      return "NÃO CORTAR";
    case "inconclusivo":
      return "INCONCLUSIVO";
    default:
      return String(recommendation).toUpperCase();
  }
}

export function getAlertRoadLabel(
  alert: Pick<AlertListItem, "road_ref" | "road_name">,
): string {
  const parts = [alert.road_ref, alert.road_name].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "Trecho sob concessão";
}

export function getCutPendingDays(
  alert: Pick<AlertListItem, "metadata" | "first_detected_at">,
): number {
  if (
    typeof alert.metadata?.cut_pending_days === "number" &&
    Number.isFinite(alert.metadata.cut_pending_days)
  ) {
    return Math.max(0, Math.round(alert.metadata.cut_pending_days));
  }
  const detected = new Date(alert.first_detected_at);
  if (Number.isNaN(detected.getTime())) return 0;
  const now = new Date();
  const diffDays = Math.floor(
    (now.getTime() - detected.getTime()) / (1000 * 60 * 60 * 24),
  );
  return Math.max(0, diffDays);
}

export function getStaleDays(
  alert: Pick<AlertListItem, "metadata" | "last_seen_at">,
): number {
  if (
    typeof alert.metadata?.stale_days === "number" &&
    Number.isFinite(alert.metadata.stale_days)
  ) {
    return Math.max(0, Math.round(alert.metadata.stale_days));
  }
  const lastSeen = new Date(alert.last_seen_at);
  if (Number.isNaN(lastSeen.getTime())) return 0;
  const now = new Date();
  const diffDays = Math.floor(
    (now.getTime() - lastSeen.getTime()) / (1000 * 60 * 60 * 24),
  );
  return Math.max(0, diffDays);
}

export function getAlertHeadline(alert: AlertListItem): string {
  switch (alert.type) {
    case "RECOMMENDATION_CHANGED":
      return "Mudança detectada";
    case "CUT_PENDING": {
      const days = getCutPendingDays(alert);
      return `Corte pendente há ${days} dias`;
    }
    case "REOBSERVATION_REQUIRED":
      return "Nova observação necessária";
    case "STALE_MONITORING":
      return "Monitoramento desatualizado";
    case "SUPPORT_DIVERGENCE":
      return "Acompanhar trecho";
    default:
      return "Alerta operacional";
  }
}

export function getAlertOperationalMessage(
  alert: AlertListItem | AlertDetail,
): string {
  switch (alert.type) {
    case "RECOMMENDATION_CHANGED":
      return "Este trecho apresentou mudança na recomendação e requer atenção.";
    case "CUT_PENDING": {
      const days = getCutPendingDays(alert);
      return `Neste trecho, a recomendação permanece CORTAR há ${days} dias.`;
    }
    case "REOBSERVATION_REQUIRED": {
      return "A análise mais recente não permitiu atualizar a decisão do trecho.";
    }
    case "STALE_MONITORING": {
      const days = getStaleDays(alert);
      return `Este trecho está há ${days} dias sem uma nova observação válida.`;
    }
    case "SUPPORT_DIVERGENCE":
      return "Os dados históricos indicam necessidade de acompanhamento adicional.";
    default:
      return "Este trecho requer atenção operacional.";
  }
}

export function getLastValidDecision(
  alert: AlertListItem | AlertDetail,
): string | null {
  if (alert.previous_recommendation) {
    return formatAlertRecommendation(alert.previous_recommendation);
  }
  const detail = alert as AlertDetail;
  if (detail.latest_analysis?.decision) {
    return formatAlertRecommendation(detail.latest_analysis.decision);
  }
  if (detail.origin_analysis?.decision) {
    return formatAlertRecommendation(detail.origin_analysis.decision);
  }
  return null;
}

export const SEVERITY_LABELS: Record<AlertSeverityValue, string> = {
  critical: "Crítico",
  high: "Alta prioridade",
  medium: "Atenção",
  low: "Informativo",
};

export const STATUS_LABELS: Record<AlertStatusValue, string> = {
  new: "Novo",
  seen: "Visto",
  monitoring: "Em acompanhamento",
  resolved: "Resolvido",
};

export const TYPE_FILTERS: Array<{ id: string; label: string; type?: AlertTypeValue; severity?: AlertSeverityValue }> = [
  { id: "all", label: "Todos" },
  { id: "critical", label: "Críticos", severity: "critical" },
  { id: "cut", label: "Corte", type: "CUT_PENDING" },
  { id: "changes", label: "Mudanças", type: "RECOMMENDATION_CHANGED" },
  { id: "reobservation", label: "Observação", type: "REOBSERVATION_REQUIRED" },
  { id: "monitoring", label: "Monitoramento", type: "STALE_MONITORING" },
  { id: "support", label: "Apoio", type: "SUPPORT_DIVERGENCE" },
];

export function formatShortDateBR(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
  });
}

export function formatFullDateTimeBR(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatEventLabel(
  event: AlertEventResponse,
  alertType?: AlertTypeValue,
): string {
  if (event.new_status === "seen") return "Marcado como visto";
  if (event.new_status === "monitoring") return "Em acompanhamento";
  if (event.new_status === "resolved") return "Resolvido";
  if (event.new_status === "new" && event.previous_status) return "Reaberto";

  const type = (event.event_type || "").toUpperCase();
  if (type === "CREATED" || type === "ALERT_CREATED") {
    switch (alertType) {
      case "RECOMMENDATION_CHANGED":
        return "Mudança detectada";
      case "CUT_PENDING":
        return "Corte pendente identificado";
      case "REOBSERVATION_REQUIRED":
        return "Nova observação necessária";
      case "STALE_MONITORING":
        return "Monitoramento desatualizado";
      case "SUPPORT_DIVERGENCE":
        return "Acompanhamento necessário";
      default:
        return "Alerta registrado";
    }
  }
  if (type === "SEEN") return "Marcado como visto";
  if (type === "MONITORING") return "Em acompanhamento";
  if (type === "RESOLVED") return "Resolvido";
  if (type === "REOPENED") return "Reaberto";
  if (type === "OBSERVATION_UPDATED") return "Nova observação registrada";
  if (type === "SEVERITY_CHANGED") return "Severidade atualizada";
  if (type === "STATUS_CHANGED") {
    return event.new_status ? STATUS_LABELS[event.new_status] : "Status atualizado";
  }

  return "Registro operacional";
}
