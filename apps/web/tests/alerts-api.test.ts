import { describe, expect, it, vi, beforeEach } from "vitest";
import { getAlertDetail, listAlerts, patchAlert } from "@/lib/api/alerts";
import * as client from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import {
  alertDetailSchema,
  alertListItemSchema,
  alertPageSchema,
  alertPatchRequestSchema,
} from "@/lib/schemas/alerts";

describe("Alerts API Client & Zod Schemas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockListItem = {
    id: "alt-101",
    type: "RECOMMENDATION_CHANGED" as const,
    severity: "high" as const,
    status: "new" as const,
    subject_kind: "monitored_section",
    subject_key: "sec-348-km12",
    spatial_key: "roadside:sp348:sec12",
    road_id: "sp348",
    road_ref: "SP-348",
    road_name: "Rodovia dos Bandeirantes",
    axis_id: "main",
    section_id: "sec-12",
    section_index: 12,
    analysis_id: "an-202",
    previous_analysis_id: "an-201",
    last_analysis_id: "an-202",
    current_recommendation: "cortar" as const,
    previous_recommendation: "nao_cortar" as const,
    first_detected_at: "2026-09-11T10:00:00Z",
    last_seen_at: "2026-09-11T12:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T12:00:00Z",
    version: 1,
    metadata: { transition: "nao_cortar:cortar" },
  };

  it("valida o schema de alertListItemSchema", () => {
    const parsed = alertListItemSchema.parse(mockListItem);
    expect(parsed.id).toBe("alt-101");
    expect(parsed.type).toBe("RECOMMENDATION_CHANGED");
    expect(parsed.version).toBe(1);
  });

  it("valida o schema de alertPageSchema", () => {
    const mockPage = {
      total: 1,
      active_count: 1,
      limit: 50,
      offset: 0,
      items: [mockListItem],
    };
    const parsed = alertPageSchema.parse(mockPage);
    expect(parsed.active_count).toBe(1);
    expect(parsed.items).toHaveLength(1);
  });

  it("valida o schema de alertDetailSchema com timeline e map_target", () => {
    const mockDetail = {
      ...mockListItem,
      timeline: [
        {
          id: 1,
          event_type: "CREATED",
          occurred_at: "2026-09-11T10:00:00Z",
          analysis_id: "an-202",
          previous_status: null,
          new_status: "new" as const,
          severity: "high" as const,
          metadata: {},
        },
      ],
      origin_analysis: {
        analysis_id: "an-202",
        created_at: "2026-09-11T10:00:00Z",
        status: "completed",
        decision: "cortar" as const,
        confidence: "high",
        latest_valid_observation_on: "2026-09-10",
      },
      previous_analysis: {
        analysis_id: "an-201",
        created_at: "2026-09-01T10:00:00Z",
        status: "completed",
        decision: "nao_cortar" as const,
        confidence: "high",
        latest_valid_observation_on: "2026-08-30",
      },
      latest_analysis: null,
      road_metadata: {
        road_id: "sp348",
        road_ref: "SP-348",
        road_name: "Rodovia dos Bandeirantes",
        axis_id: "main",
        section_id: "sec-12",
        section_index: 12,
        spatial_key: "roadside:sp348:sec12",
      },
      map_target: {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-46.95, -23.1],
              [-46.94, -23.1],
              [-46.94, -23.11],
              [-46.95, -23.11],
              [-46.95, -23.1],
            ],
          ],
        },
        bounds: { west: -46.95, south: -23.11, east: -46.94, north: -23.1 },
        centroid: { longitude: -46.945, latitude: -23.105 },
        road_ref: "SP-348",
        road_name: "Rodovia dos Bandeirantes",
        section_id: "sec-12",
      },
    };

    const parsed = alertDetailSchema.parse(mockDetail);
    expect(parsed.timeline).toHaveLength(1);
    expect(parsed.map_target.bounds?.west).toBe(-46.95);
  });

  it("valida alertPatchRequestSchema para optimistic locking", () => {
    const valid = alertPatchRequestSchema.parse({ status: "seen", version: 1 });
    expect(valid.status).toBe("seen");
    expect(valid.version).toBe(1);

    expect(() =>
      alertPatchRequestSchema.parse({ status: "invalid_status", version: 1 }),
    ).toThrow();
    expect(() =>
      alertPatchRequestSchema.parse({ status: "seen", version: 0 }),
    ).toThrow();
  });

  it("listAlerts monta query string com filtros opcionais", async () => {
    const spy = vi.spyOn(client, "apiRequest").mockResolvedValue({
      total: 1,
      active_count: 1,
      limit: 50,
      offset: 0,
      items: [mockListItem],
    });

    await listAlerts({
      status: "new",
      severity: "critical",
      type: "CUT_PENDING",
      road: "SP-348",
      section_id: "sec-12",
      limit: 25,
      offset: 10,
    });

    expect(spy).toHaveBeenCalledWith(
      "/api/alerts?status=new&severity=critical&type=CUT_PENDING&road=SP-348&section_id=sec-12&limit=25&offset=10",
      alertPageSchema,
    );
  });

  it("getAlertDetail busca alerta por ID com codificação de URL", async () => {
    const spy = vi.spyOn(client, "apiRequest").mockResolvedValue({
      ...mockListItem,
      timeline: [],
      origin_analysis: {
        analysis_id: "an-1",
        created_at: "2026-09-11T10:00:00Z",
        status: "completed",
      },
      road_metadata: {},
      map_target: {},
    });

    await getAlertDetail("alt/101");
    expect(spy).toHaveBeenCalledWith("/api/alerts/alt%2F101", alertDetailSchema);
  });

  it("patchAlert envia status e version com método PATCH", async () => {
    const spy = vi.spyOn(client, "apiRequest").mockResolvedValue({
      ...mockListItem,
      status: "seen",
      version: 2,
    });

    const result = await patchAlert("alt-101", { status: "seen", version: 1 });
    expect(spy).toHaveBeenCalledWith(
      "/api/alerts/alt-101",
      expect.anything(),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "seen", version: 1 }),
      }),
    );
    expect(result.status).toBe("seen");
    expect(result.version).toBe(2);
  });

  it("propaga erro 409 ALERT_VERSION_CONFLICT", async () => {
    vi.spyOn(client, "apiRequest").mockRejectedValue(
      new ApiError("ALERT_VERSION_CONFLICT", "O alerta foi atualizado por outro operador.", 409),
    );

    await expect(patchAlert("alt-101", { status: "seen", version: 1 })).rejects.toThrow(
      "O alerta foi atualizado por outro operador.",
    );
  });
});
