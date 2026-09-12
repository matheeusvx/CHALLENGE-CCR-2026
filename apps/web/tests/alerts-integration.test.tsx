import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/layout/app-shell";
import * as alertsApi from "@/lib/api/alerts";
import type { AlertDetail, AlertListItem, AlertMapTarget } from "@/lib/schemas/alerts";
import { useAnalysisStore } from "@/stores/analysis-store";
import { useOnboardingStore } from "@/stores/onboarding-store";

const mockAlerts: AlertListItem[] = [
  {
    id: "alt-integration-1",
    type: "RECOMMENDATION_CHANGED",
    severity: "critical",
    status: "new",
    subject_kind: "monitored_section",
    subject_key: "sec-integration-1",
    spatial_key: "roadside:sp348:001",
    road_id: "sp348",
    road_ref: "SP-348",
    road_name: "Rodovia dos Bandeirantes",
    axis_id: "main_axis",
    section_id: "sec-01",
    section_index: 1,
    analysis_id: "an-201",
    previous_analysis_id: "an-200",
    last_analysis_id: "an-201",
    current_recommendation: "cortar",
    previous_recommendation: "nao_cortar",
    first_detected_at: "2026-09-11T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 1,
    metadata: {},
  },
  {
    id: "alt-integration-2",
    type: "CUT_PENDING",
    severity: "high",
    status: "new",
    subject_kind: "monitored_section",
    subject_key: "sec-integration-2",
    spatial_key: "roadside:sp330:001",
    road_id: "sp330",
    road_ref: "SP-330",
    road_name: "Rodovia Anhanguera",
    axis_id: "main_axis",
    section_id: "sec-02",
    section_index: 2,
    analysis_id: "an-202",
    current_recommendation: "cortar",
    previous_recommendation: null,
    first_detected_at: "2026-09-01T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 1,
    metadata: { cut_pending_days: 10 },
  },
];

const mockDetail: AlertDetail = {
  ...mockAlerts[0],
  timeline: [
    {
      id: 1,
      event_type: "CREATED",
      occurred_at: "2026-09-11T08:00:00Z",
      new_status: "new",
      severity: "critical",
      metadata: {},
    },
  ],
  origin_analysis: {
    analysis_id: "an-201",
    created_at: "2026-09-11T08:00:00Z",
    status: "completed",
    decision: "cortar",
  },
  previous_analysis: {
    analysis_id: "an-200",
    created_at: "2026-09-01T08:00:00Z",
    status: "completed",
    decision: "nao_cortar",
  },
  latest_analysis: null,
  road_metadata: {
    road_id: "sp348",
    road_ref: "SP-348",
    road_name: "Rodovia dos Bandeirantes",
    section_id: "sec-01",
    spatial_key: "roadside:sp348:001",
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
    section_id: "sec-01",
  },
};

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("ALERT-04 — Integração de Alertas no AppShell e Mapa", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useOnboardingStore.setState({ onboardingCompleted: true, tourActive: false });
    useAnalysisStore.setState({
      alertTarget: null,
      alertFitRequestId: 0,
      currentResult: null,
      selectedTool: "navigate",
    });

    vi.spyOn(alertsApi, "listAlerts").mockResolvedValue({
      total: mockAlerts.length,
      active_count: mockAlerts.length,
      limit: 100,
      offset: 0,
      items: mockAlerts,
    });
    vi.spyOn(alertsApi, "getAlertDetail").mockResolvedValue(mockDetail);
  });

  it("navega da aba Alertas para o Painel ao clicar em 'Abrir trecho' e atualiza o store", async () => {
    const Wrapper = createWrapper();
    render(
      <AppShell>
        <div data-testid="analysis-view-content">Conteúdo do Painel</div>
      </AppShell>,
      { wrapper: Wrapper },
    );

    // Navega para a aba Alertas pela Sidebar
    const alertsNavBtn = screen.getByTestId("nav-item-alerts");
    expect(alertsNavBtn).toBeInTheDocument();
    fireEvent.click(alertsNavBtn);

    // Aguarda carregar a lista de alertas
    await waitFor(() => {
      expect(screen.getByTestId("alert-card-alt-integration-1")).toBeInTheDocument();
    });

    // Clica no card para abrir o painel de detalhes
    fireEvent.click(screen.getByTestId("alert-card-alt-integration-1"));

    await waitFor(() => {
      expect(screen.getByTestId("alert-detail-pane")).toBeInTheDocument();
    });

    // Clica no botão "Abrir trecho"
    const openTrackBtn = screen.getByTestId("open-track-btn");
    expect(openTrackBtn).toBeInTheDocument();
    fireEvent.click(openTrackBtn);

    // Verifica que retornou ao Painel (analysis view ativa)
    await waitFor(() => {
      expect(screen.getByTestId("analysis-view-content")).toBeInTheDocument();
    });

    // Verifica que o estado global do mapa foi devidamente configurado com o alertTarget
    const storeState = useAnalysisStore.getState();
    expect(storeState.alertTarget).toEqual(mockDetail.map_target);
    expect(storeState.alertFitRequestId).toBeGreaterThan(0);
    expect(storeState.selectedTool).toBe("navigate");
    expect(storeState.activeTab).toBe("area");
    expect(storeState.geometry).toEqual(mockDetail.map_target?.geometry);
  });

  it("tolera target com geometry: null sem travar a aplicação", () => {
    const targetWithoutGeometry: AlertMapTarget = {
      geometry: null,
      bounds: { west: -47.0, south: -23.5, east: -46.9, north: -23.4 },
      centroid: { longitude: -46.95, latitude: -23.45 },
      road_ref: "SP-300",
      road_name: "Rodovia Marechal Rondon",
      section_id: "sec-99",
    };

    expect(() => {
      useAnalysisStore.getState().focusAlertTarget(targetWithoutGeometry);
    }).not.toThrow();

    const storeState = useAnalysisStore.getState();
    expect(storeState.alertTarget).toEqual(targetWithoutGeometry);
    expect(storeState.alertFitRequestId).toBeGreaterThan(0);
    expect(storeState.selectedTool).toBe("navigate");
  });

  it("tolera target totalmente vazio (geometry, bounds e centroid nulos) de forma segura", () => {
    const emptyTarget: AlertMapTarget = {
      geometry: null,
      bounds: null,
      centroid: null,
      road_ref: null,
      road_name: null,
      section_id: null,
    };

    expect(() => {
      useAnalysisStore.getState().focusAlertTarget(emptyTarget);
    }).not.toThrow();

    const storeState = useAnalysisStore.getState();
    expect(storeState.alertTarget).toEqual(emptyTarget);
    expect(storeState.alertFitRequestId).toBeGreaterThan(0);
  });

  it("exibe badge com active_count e suporte a 99+ na navegação", async () => {
    const Wrapper = createWrapper();
    render(
      <AppShell>
        <div>Painel</div>
      </AppShell>,
      { wrapper: Wrapper },
    );

    // Badge inicial com contagem 2 e indicador de pulso ativo
    await waitFor(() => {
      const badge = screen.getByTestId("alerts-badge");
      expect(badge).toBeInTheDocument();
      expect(badge).toHaveTextContent("2");
      expect(screen.getByTestId("alerts-pulse-dot")).toBeInTheDocument();
    });
  });

  it("exibe badge 99+ quando active_count excede 99", async () => {
    vi.spyOn(alertsApi, "listAlerts").mockResolvedValue({
      total: 150,
      active_count: 150,
      limit: 100,
      offset: 0,
      items: mockAlerts,
    });

    const Wrapper = createWrapper();
    render(
      <AppShell>
        <div>Painel</div>
      </AppShell>,
      { wrapper: Wrapper },
    );

    await waitFor(() => {
      const badge = screen.getByTestId("alerts-badge");
      expect(badge).toBeInTheDocument();
      expect(badge).toHaveTextContent("99+");
    });
  });
});
