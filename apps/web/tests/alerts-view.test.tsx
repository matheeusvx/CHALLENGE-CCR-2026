import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AlertsView } from "@/components/views/alerts-view";
import * as alertsApi from "@/lib/api/alerts";
import { ApiError } from "@/lib/api/client";
import type { AlertDetail, AlertListItem } from "@/lib/schemas/alerts";

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

const mockAlerts: AlertListItem[] = [
  {
    id: "alt-change",
    type: "RECOMMENDATION_CHANGED",
    severity: "critical",
    status: "new",
    subject_kind: "monitored_section",
    subject_key: "sec-bandeirantes-1",
    spatial_key: "roadside:sp348:001",
    road_id: "sp348",
    road_ref: "SP-348",
    road_name: "Rodovia dos Bandeirantes",
    axis_id: "main_axis",
    section_id: "sec-01",
    section_index: 1,
    analysis_id: "an-101",
    previous_analysis_id: "an-100",
    last_analysis_id: "an-101",
    current_recommendation: "cortar",
    previous_recommendation: "nao_cortar",
    first_detected_at: "2026-09-11T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 1,
    metadata: { transition: "nao_cortar:cortar" },
  },
  {
    id: "alt-cut",
    type: "CUT_PENDING",
    severity: "high",
    status: "seen",
    subject_kind: "monitored_section",
    subject_key: "sec-anhanguera-1",
    spatial_key: "roadside:sp330:001",
    road_id: "sp330",
    road_ref: "SP-330",
    road_name: "Rodovia Anhanguera",
    axis_id: "main_axis",
    section_id: "sec-02",
    section_index: 2,
    analysis_id: "an-102",
    current_recommendation: "cortar",
    previous_recommendation: null,
    first_detected_at: "2026-09-02T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: "2026-09-03T09:00:00Z",
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 3,
    metadata: { cut_pending_days: 9, not_a_mowing_date_estimate: true },
  },
  {
    id: "alt-reobs",
    type: "REOBSERVATION_REQUIRED",
    severity: "medium",
    status: "monitoring",
    subject_kind: "monitored_section",
    subject_key: "sec-imigrantes-1",
    spatial_key: "roadside:sp160:001",
    road_id: "sp160",
    road_ref: "SP-160",
    road_name: "Rodovia dos Imigrantes",
    axis_id: "main_axis",
    section_id: "sec-03",
    section_index: 3,
    analysis_id: "an-103",
    current_recommendation: "inconclusivo",
    previous_recommendation: "nao_cortar",
    first_detected_at: "2026-09-05T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 2,
    metadata: {},
  },
  {
    id: "alt-stale",
    type: "STALE_MONITORING",
    severity: "medium",
    status: "new",
    subject_kind: "monitored_section",
    subject_key: "sec-anchieta-1",
    spatial_key: "roadside:sp150:001",
    road_id: "sp150",
    road_ref: "SP-150",
    road_name: "Via Anchieta",
    axis_id: "main_axis",
    section_id: "sec-04",
    section_index: 4,
    analysis_id: "an-104",
    first_detected_at: "2026-08-01T08:00:00Z",
    last_seen_at: "2026-08-01T08:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-08-01T08:00:00Z",
    version: 1,
    metadata: { stale_days: 35, threshold_days: 30 },
  },
  {
    id: "alt-divergence",
    type: "SUPPORT_DIVERGENCE",
    severity: "low",
    status: "new",
    subject_kind: "monitored_section",
    subject_key: "sec-castello-1",
    spatial_key: "roadside:sp280:001",
    road_id: "sp280",
    road_ref: "SP-280",
    road_name: "Rodovia Castello Branco",
    axis_id: "main_axis",
    section_id: "sec-05",
    section_index: 5,
    analysis_id: "an-105",
    first_detected_at: "2026-09-10T08:00:00Z",
    last_seen_at: "2026-09-11T10:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    updated_at: "2026-09-11T10:00:00Z",
    version: 1,
    metadata: { rule: "B", score: 0.88 },
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
    {
      id: 2,
      event_type: "SEEN",
      occurred_at: "2026-09-12T09:00:00Z",
      new_status: "seen",
      severity: "critical",
      metadata: {},
    },
    {
      id: 3,
      event_type: "MONITORING",
      occurred_at: "2026-09-13T10:00:00Z",
      new_status: "monitoring",
      severity: "critical",
      metadata: {},
    },
  ],
  origin_analysis: {
    analysis_id: "an-101",
    created_at: "2026-09-11T08:00:00Z",
    status: "completed",
    decision: "cortar",
  },
  previous_analysis: {
    analysis_id: "an-100",
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

describe("AlertsView — Requisitos Operacionais (ALERT-04)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(alertsApi, "listAlerts").mockImplementation(async (options = {}) => {
      const filtered = mockAlerts.filter((alert) =>
        (!options.status || alert.status === options.status) &&
        (!options.severity || alert.severity === options.severity) &&
        (!options.type || alert.type === options.type) &&
        (!options.road || [alert.road_ref, alert.road_id, alert.road_name].includes(options.road))
      );
      return {
        total: filtered.length,
        active_count: filtered.filter((alert) => alert.status !== "resolved").length,
        limit: options.limit ?? 50,
        offset: options.offset ?? 0,
        items: filtered.slice(options.offset ?? 0, (options.offset ?? 0) + (options.limit ?? 50)),
      };
    });
    vi.spyOn(alertsApi, "getAlertDetail").mockResolvedValue(mockDetail);
  });

  // -------------------------------------------------------------------------
  // 1. Tratamento dos 5 Tipos e Linguagem Operacional
  // -------------------------------------------------------------------------
  it("trata os 5 tipos de alerta com linguagem estritamente operacional", async () => {
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Mudança detectada")).toBeInTheDocument();
    });

    // 1) RECOMMENDATION_CHANGED
    const cardChange = screen.getByTestId("alert-card-alt-change");
    expect(within(cardChange).getByText("SP-348 · Rodovia dos Bandeirantes")).toBeInTheDocument();
    expect(within(cardChange).getByText("Este trecho apresentou mudança na recomendação e requer atenção.")).toBeInTheDocument();
    expect(within(cardChange).getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(within(cardChange).getByText("CORTAR")).toBeInTheDocument();

    // 2) CUT_PENDING: "Corte pendente há 9 dias" e "Neste trecho, a recomendação permanece CORTAR há 9 dias."
    const cardCut = screen.getByTestId("alert-card-alt-cut");
    expect(within(cardCut).getByText("Corte pendente há 9 dias")).toBeInTheDocument();
    expect(within(cardCut).getByText("SP-330 · Rodovia Anhanguera")).toBeInTheDocument();
    expect(within(cardCut).getByText("Neste trecho, a recomendação permanece CORTAR há 9 dias.")).toBeInTheDocument();

    // REGRA CRÍTICA: Nunca escrever "último corte há 9 dias"
    expect(screen.queryByText(/último corte há/i)).toBeNull();

    // 3) REOBSERVATION_REQUIRED
    expect(screen.getByText("Nova observação necessária")).toBeInTheDocument();
    expect(screen.getByText("A análise mais recente não permitiu atualizar a decisão do trecho.")).toBeInTheDocument();
    expect(screen.getByText("Última decisão válida:")).toBeInTheDocument();

    // 4) STALE_MONITORING
    expect(screen.getByText("Monitoramento desatualizado")).toBeInTheDocument();
    expect(screen.getByText("Este trecho está há 35 dias sem uma nova observação válida.")).toBeInTheDocument();

    // 5) SUPPORT_DIVERGENCE
    expect(screen.getByText("Acompanhar trecho")).toBeInTheDocument();
    expect(screen.getByText("Os dados históricos indicam necessidade de acompanhamento adicional.")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // 2. Não Exibir Jargão Técnico Nem Detalhes Internos
  // -------------------------------------------------------------------------
  it("NÃO exibe termos técnicos (Regra B, Sentinel-1 mixed, score bruto, spatial_key, axis_id)", async () => {
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Mudança detectada")).toBeInTheDocument();
    });

    const bodyText = document.body.textContent || "";
    expect(bodyText).not.toContain("Regra B");
    expect(bodyText).not.toContain("Sentinel-1 mixed");
    expect(bodyText).not.toContain("calibration_status");
    expect(bodyText).not.toContain("axis_id");
    expect(bodyText).not.toContain("spatial_key");
    expect(bodyText).not.toContain("subject_key");
    expect(bodyText).not.toContain("score bruto");
  });

  // -------------------------------------------------------------------------
  // 3. Severidade com Visual Discreto
  // -------------------------------------------------------------------------
  it("aplica indicadores discretos de severidade sem cards inteiros vermelhos", async () => {
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("alert-card-alt-change")).toBeInTheDocument();
    });

    const criticalCard = screen.getByTestId("alert-card-alt-change");
    expect(criticalCard).toHaveClass("severity--critical");
    expect(criticalCard.querySelector(".severity-badge--critical")).toHaveTextContent("Crítico");

    const highCard = screen.getByTestId("alert-card-alt-cut");
    expect(highCard).toHaveClass("severity--high");
    expect(highCard.querySelector(".severity-badge--high")).toHaveTextContent("Alta prioridade");
  });

  // -------------------------------------------------------------------------
  // 4. KPIs e Resumo Operacional
  // -------------------------------------------------------------------------
  it("renderiza KPIs no topo com Ativos, Alta prioridade, Novos e Em acompanhamento", async () => {
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("kpi-active")).toHaveTextContent("5");
    });

    expect(screen.getByTestId("kpi-high-priority")).toHaveTextContent("2");
    expect(screen.getByTestId("kpi-new")).toHaveTextContent("3");
    expect(screen.getByTestId("kpi-monitoring")).toHaveTextContent("1");
  });

  // -------------------------------------------------------------------------
  // 5. Filtros Compactos: Categoria, Rodovia e Status
  // -------------------------------------------------------------------------
  it("filtra por categoria, rodovia e status", async () => {
    const onOpenTrack = vi.fn();
    const listSpy = vi.spyOn(alertsApi, "listAlerts");
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Críticos")).toBeInTheDocument();
    });

    // Filtro rápido "Críticos"
    fireEvent.click(screen.getByText("Críticos"));
    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ severity: "critical" }),
    );

    // Filtro por rodovia
    fireEvent.change(screen.getByTestId("filter-road-select"), {
      target: { value: "SP-348" },
    });
    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ road: "SP-348" }),
    );

    fireEvent.change(screen.getByTestId("filter-severity-select"), {
      target: { value: "high" },
    });
    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ severity: "high" }),
    );

    // Filtro por status
    fireEvent.change(screen.getByTestId("filter-status-select"), {
      target: { value: "seen" },
    });
    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ status: "seen" }),
    );
  });

  it("limpa todos os filtros e volta à visão padrão", async () => {
    const listSpy = vi.spyOn(alertsApi, "listAlerts");
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={vi.fn()} />, { wrapper: Wrapper });
    await screen.findByTestId("filter-road-select");
    fireEvent.click(screen.getByText("Corte"));
    fireEvent.change(screen.getByTestId("filter-road-select"), {
      target: { value: "SP-330" },
    });
    fireEvent.change(screen.getByTestId("filter-severity-select"), {
      target: { value: "high" },
    });
    fireEvent.change(screen.getByTestId("filter-status-select"), {
      target: { value: "seen" },
    });
    fireEvent.click(screen.getByTestId("clear-filters-btn"));

    expect(screen.getByTestId("filter-road-select")).toHaveValue("");
    expect(screen.getByTestId("filter-severity-select")).toHaveValue("");
    expect(screen.getByTestId("filter-status-select")).toHaveValue("");
    expect(screen.getByTestId("filter-category-all")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(listSpy).toHaveBeenCalledWith({
      severity: undefined,
      road: undefined,
      status: undefined,
      limit: 100,
    }));
  });

  // -------------------------------------------------------------------------
  // 6. Transição de Status e Optimistic Locking (PATCH com version)
  // -------------------------------------------------------------------------
  it("dispara PATCH com version ao marcar como visto", async () => {
    const patchSpy = vi.spyOn(alertsApi, "patchAlert").mockResolvedValue({
      ...mockAlerts[0],
      status: "seen",
      version: 2,
    });

    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("action-seen-alt-change")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("action-seen-alt-change"));

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith("alt-change", {
        status: "seen",
        version: 1,
      });
    });
  });

  it("envia PATCH para acompanhamento e resolução e atualiza os contadores", async () => {
    let records = mockAlerts.map((alert) => ({ ...alert }));
    vi.spyOn(alertsApi, "listAlerts").mockImplementation(async (options = {}) => {
      const filtered = records.filter((alert) =>
        (!options.status || alert.status === options.status) &&
        (!options.severity || alert.severity === options.severity) &&
        (!options.type || alert.type === options.type)
      );
      return {
        total: filtered.length,
        active_count: filtered.filter((alert) => alert.status !== "resolved").length,
        limit: options.limit ?? 50,
        offset: options.offset ?? 0,
        items: filtered,
      };
    });
    const patchSpy = vi.spyOn(alertsApi, "patchAlert").mockImplementation(
      async (id, payload) => {
        const current = records.find((alert) => alert.id === id)!;
        const updated = { ...current, status: payload.status, version: current.version + 1 };
        records = records.map((alert) => alert.id === id ? updated : alert);
        return updated;
      },
    );
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={vi.fn()} />, { wrapper: Wrapper });
    await screen.findByTestId("action-monitoring-alt-change");

    fireEvent.click(screen.getByTestId("action-monitoring-alt-change"));
    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith(
      "alt-change", { status: "monitoring", version: 1 },
    ));
    await waitFor(() => expect(screen.getByTestId("kpi-monitoring")).toHaveTextContent("2"));

    fireEvent.click(screen.getByTestId("action-resolve-alt-change"));
    await waitFor(() => expect(patchSpy).toHaveBeenLastCalledWith(
      "alt-change", { status: "resolved", version: 2 },
    ));
    await waitFor(() => expect(screen.getByTestId("kpi-active")).toHaveTextContent("4"));
  });

  it("desfaz estado otimista e exibe mensagem discreta em conflito 409", async () => {
    vi.spyOn(alertsApi, "patchAlert").mockRejectedValue(
      new ApiError(
        "ALERT_VERSION_CONFLICT",
        "O alerta foi atualizado por outro operador.",
        409,
      ),
    );

    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("action-seen-alt-change")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("action-seen-alt-change"));

    await waitFor(() => {
      expect(screen.getByTestId("alert-conflict-banner")).toBeInTheDocument();
    });

    expect(
      screen.getByText("O alerta foi atualizado em outra sessão. As informações foram recarregadas."),
    ).toBeInTheDocument();
    expect(alertsApi.getAlertDetail).toHaveBeenCalledWith("alt-change");
  });

  it("botão Atualizar refaz as consultas sem limpar filtros", async () => {
    const listSpy = vi.spyOn(alertsApi, "listAlerts");
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={vi.fn()} />, { wrapper: Wrapper });
    await screen.findByTestId("filter-status-select");
    fireEvent.change(screen.getByTestId("filter-status-select"), {
      target: { value: "seen" },
    });
    await waitFor(() => expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ status: "seen" }),
    ));
    const callsBefore = listSpy.mock.calls.length;
    fireEvent.click(screen.getByTestId("refresh-alerts-btn"));
    await waitFor(() => expect(listSpy.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(screen.getByTestId("filter-status-select")).toHaveValue("seen");
  });

  // -------------------------------------------------------------------------
  // 7. Painel de Detalhes e Timeline Sem JSON Técnico
  // -------------------------------------------------------------------------
  it("abre o painel de detalhes com timeline simples e sem JSON técnico", async () => {
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("alert-card-alt-change")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("alert-card-alt-change"));

    await waitFor(() => {
      expect(screen.getByTestId("alert-detail-pane")).toBeInTheDocument();
    });

    const detailPane = screen.getByTestId("alert-detail-pane");

    // Timeline simples: datas e eventos amigáveis
    expect(within(detailPane).getByText("Histórico de eventos")).toBeInTheDocument();
    expect(within(detailPane).getAllByText("Mudança detectada").length).toBeGreaterThan(0);
    expect(within(detailPane).getByText("Marcado como visto")).toBeInTheDocument();
    expect(within(detailPane).getByText("Em acompanhamento")).toBeInTheDocument();

    // Sem JSON
    expect(detailPane.textContent).not.toContain("{");
    expect(detailPane.textContent).not.toContain("}");

    // Botão Abrir trecho
    const openTrackBtn = screen.getByTestId("open-track-btn");
    expect(openTrackBtn).toBeInTheDocument();
    fireEvent.click(openTrackBtn);
    expect(onOpenTrack).toHaveBeenCalledWith(mockDetail.map_target);
  });

  it("informa discretamente quando o alerta não possui alvo utilizável", async () => {
    vi.spyOn(alertsApi, "getAlertDetail").mockResolvedValue({
      ...mockDetail,
      map_target: {
        geometry: null,
        bounds: null,
        centroid: null,
        road_ref: "SP-348",
        road_name: "Rodovia dos Bandeirantes",
        section_id: "sec-01",
      },
    });
    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });
    await screen.findByTestId("alert-card-alt-change");
    fireEvent.click(screen.getByTestId("alert-card-alt-change"));
    await screen.findByTestId("open-track-btn");
    fireEvent.click(screen.getByTestId("open-track-btn"));
    expect(await screen.findByTestId("map-target-warning")).toHaveTextContent(
      "Este alerta ainda não possui localização disponível no mapa.",
    );
    expect(onOpenTrack).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // 8. Estados de UX: Vazio e Erro
  // -------------------------------------------------------------------------
  it("exibe estado vazio quando não há alertas ativos", async () => {
    vi.spyOn(alertsApi, "listAlerts").mockResolvedValue({
      total: 0,
      active_count: 0,
      limit: 100,
      offset: 0,
      items: [],
    });

    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("alerts-empty")).toBeInTheDocument();
    });

    expect(screen.getByText("Nenhum alerta ativo")).toBeInTheDocument();
    expect(screen.getByText("Os trechos monitorados estão sem pendências no momento.")).toBeInTheDocument();
  });

  it("exibe estado de erro com botão para tentar novamente", async () => {
    vi.spyOn(alertsApi, "listAlerts").mockRejectedValue(new Error("Erro de conexão com a API"));

    const onOpenTrack = vi.fn();
    const Wrapper = createWrapper();
    render(<AlertsView onOpenTrack={onOpenTrack} />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("alerts-error")).toBeInTheDocument();
    });

    expect(screen.getByText("Falha ao carregar alertas operacionais")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tentar novamente/i })).toBeInTheDocument();
  });
});
