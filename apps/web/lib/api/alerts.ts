import {
  alertDetailSchema,
  alertPageSchema,
  alertPatchResponseSchema,
  type AlertDetail,
  type AlertListItem,
  type AlertPage,
  type AlertPatchRequest,
  type AlertSeverityValue,
  type AlertStatusValue,
  type AlertTypeValue,
} from "@/lib/schemas/alerts";
import { apiRequest } from "./client";

export type ListAlertsOptions = {
  status?: AlertStatusValue;
  severity?: AlertSeverityValue;
  type?: AlertTypeValue;
  road?: string;
  section_id?: string;
  limit?: number;
  offset?: number;
};

export function listAlerts(options: ListAlertsOptions = {}): Promise<AlertPage> {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.severity) params.set("severity", options.severity);
  if (options.type) params.set("type", options.type);
  if (options.road) params.set("road", options.road);
  if (options.section_id) params.set("section_id", options.section_id);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));

  const query = params.toString();
  return apiRequest(`/api/alerts${query ? `?${query}` : ""}`, alertPageSchema);
}

export function getAlertDetail(alertId: string): Promise<AlertDetail> {
  return apiRequest(`/api/alerts/${encodeURIComponent(alertId)}`, alertDetailSchema);
}

export function patchAlert(
  alertId: string,
  payload: AlertPatchRequest,
): Promise<AlertListItem> {
  return apiRequest(
    `/api/alerts/${encodeURIComponent(alertId)}`,
    alertPatchResponseSchema,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}
