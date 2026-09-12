import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import * as api from "@/lib/api/analyses";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { useAnalysisStore } from "@/stores/analysis-store";
import { useHistoryStore } from "@/stores/history-store";
import type { PolygonGeometry } from "@/lib/map/geometry";

vi.mock("@/lib/api/analyses", () => ({
  getHealth: vi.fn(),
  validateGeometry: vi.fn(),
  runAnalysis: vi.fn(),
  listAnalyses: vi.fn(),
  getAnalysisDetail: vi.fn(),
  hideAnalysisFromHistory: vi.fn(),
  clearAnalysisHistory: vi.fn(),
}));
vi.mock("@/components/map/analysis-map", () => ({
  AnalysisMap: ({ result: mapResult }: { result?: AnalysisResponse }) => <div data-testid="analysis-map" data-decision={mapResult?.recommendation.decision ?? "editing"} />,
}));

const polygon: PolygonGeometry = {
  type: "Polygon" as const,
  coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]],
};

const polygonB: PolygonGeometry = {
  type: "Polygon" as const,
  coordinates: [[[-46.958, -23.112], [-46.956, -23.112], [-46.956, -23.11], [-46.958, -23.11], [-46.958, -23.112]]],
};

const validation = {
  valid: true as const,
  geometry_type: "Polygon",
  area_square_meters: 4500,
  centroid: { longitude: -46.961, latitude: -23.108 },
  bounding_box: [-46.962, -23.109, -46.96, -23.107],
  estimated_sentinel_pixels: 45,
  warnings: [],
};

const result: AnalysisResponse = {
  analysis_id: "6d7ba572-321d-4a27-9f0f-9fcbd5ecab62",
  status: "completed",
  analysis_period: { start_date: "2026-07-10", end_date: "2026-08-10", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
  recommendation: { decision: "nao_cortar", confidence: "high", experimental: true, summary: "Vegetação abaixo do nível alto local.", reasons: ["current_percentile_below_or_equal_50"], blocking_reasons: [], limitations: ["Validação de campo necessária."], metrics: { current_ndvi_mean: 0.52, historical_median: 0.58, current_percentile: 40, recent_trend: -0.01, observation_count: 4 } },
  height_estimation: { status: "experimental", estimated_class: "le_30_cm", probability_gt_30_cm: 0.23, confidence: "medium", reference_threshold_cm: 30, model_version: "height-estimator-v0" },
  selected_area_m2: 12450,
  effective_analysis_area_m2: 11578.5,
  effective_analysis_pct: 93,
  aoi: { source: "geojson_inline", area_square_meters: 12450 },
  summary: {
    date_range_effectively_processed: { start: "2026-06-01", end: "2026-08-01" },
    analysis_quality: { score: 92, status: "high", mean_scene_quality_score: 89, rejected_scene_count: 3 },
    thresholds: { high_vegetation_percentile: 75, significant_drop_absolute: 0.06 },
  },
  timeseries: [{ datetime: "2026-06-01T00:00:00Z", ndvi_mean: 0.61, ndvi_median: 0.6, scene_quality_score: 90 }, { datetime: "2026-08-01T00:00:00Z", ndvi_mean: 0.52, ndvi_median: 0.51, scene_quality_score: 92 }],
  scenes: [{ item_id: "S2_TEST", datetime: "2026-08-01T00:00:00Z", cloud_cover: 8, valid_pixel_count: 124, total_pixel_count: 130, valid_pixel_percentage: 92, local_invalid_pixel_percentage: 8, aoi_coverage_percentage: 98, scene_quality_score: 91, quality_status: "high", accepted_for_timeseries: true, daily_aggregation: "best", temporal_outlier_suspected: false }],
  artifacts: {}, warnings: [], errors: [],
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", service: "motiva-vegetation-api", version: "0.1.0" });
  vi.mocked(api.validateGeometry).mockResolvedValue(validation);
  vi.mocked(api.runAnalysis).mockResolvedValue(result);
  vi.mocked(api.listAnalyses).mockResolvedValue({ total: 0, limit: 20, offset: 0, items: [] });
  vi.mocked(api.getAnalysisDetail).mockResolvedValue({
    analysis_id: result.analysis_id,
    created_at: "2026-08-10T12:00:00Z",
    geometry: polygon,
    result,
  });
  vi.mocked(api.hideAnalysisFromHistory).mockResolvedValue({ analysis_id: result.analysis_id, hidden: true });
  vi.mocked(api.clearAnalysisHistory).mockResolvedValue({ hidden_count: 0 });
  useAnalysisStore.setState({ geometry: null, geometryRevision: 0, geometrySource: null, geometryValidation: null, isGeometryDirty: false, selectedTool: "navigate", lastValidatedGeometryRevision: null, lastValidatedAt: null, geometryText: "", fitRequestId: 0, activeTab: "area", currentResult: null });
  useHistoryStore.setState({ entries: [] });
});

async function completeCurrentAnalysis() {
  fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
  await screen.findByText("Área validada");
  fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));
  await screen.findAllByText("Vegetação abaixo do nível alto local.");
}

describe("workspace geoespacial", () => {
  it("renderiza a página, o mapa e o produto", () => {
    render(<Home />, { wrapper });
    expect(screen.getByRole("heading", { name: "Motiva Faixa Verde" })).toBeInTheDocument();
    expect(screen.queryByText("Operações / Vegetação")).not.toBeInTheDocument();
    expect(screen.getByText("Monitoramento inteligente da vegetação lateral rodoviária")).toBeInTheDocument();
    expect(screen.queryByText("Sentinel-2 L2A")).not.toBeInTheDocument();
    expect(screen.queryByText("Planetary Computer")).not.toBeInTheDocument();
    expect(screen.getByTestId("analysis-map")).toBeInTheDocument();
    expect(screen.queryByTestId("api-status")).not.toBeInTheDocument();
    expect(screen.queryByText("API operacional")).not.toBeInTheDocument();
  });

  it("inicia uma sessão limpa ao voltar do Histórico para Painel", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Histórico" }));
    expect(screen.getByRole("heading", { name: "Histórico" })).toBeInTheDocument();
    expect(screen.getByTestId("analysis-map")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Fontes de dados" }));
    expect(screen.getByRole("heading", { name: "Fontes de dados" })).toBeInTheDocument();
    expect(screen.getByText(/Sentinel-2 L2A/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("operator-profile-trigger"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Configurações" }));
    expect(screen.getByRole("heading", { name: "Configurações" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Cobertura máxima de nuvens")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Painel" }));
    expect(screen.getByRole("heading", { name: "Motiva Faixa Verde" })).toBeInTheDocument();
    expect(useAnalysisStore.getState().geometry).toBeNull();
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
  });

  it("mostra somente as etapas Área e Resultado sem controles técnicos", () => {
    render(<Home />, { wrapper });
    expect(screen.getByRole("tab", { name: "Área" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Resultado" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Resultado" })).toBeDisabled();
    expect(screen.queryByRole("tab", { name: "Parâmetros" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data inicial")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data final")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cobertura máxima de nuvens")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Quantidade máxima de cenas")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pixels válidos mínimos")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Agregação diária")).not.toBeInTheDocument();
  });

  it("mostra o estado sem geometria e bloqueia a análise", () => {
    render(<Home />, { wrapper });
    expect(screen.getByText("Nenhuma área delimitada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Executar análise" })).toBeDisabled();
  });

  it("aplica um Polygon colado ao mapa", () => {
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByText("Entrada avançada por GeoJSON"));
    fireEvent.change(screen.getByLabelText("GeoJSON da área de interesse"), { target: { value: JSON.stringify(polygon) } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(screen.getByText("Área aguardando validação")).toBeInTheDocument();
    expect(useAnalysisStore.getState().geometrySource).toBe("pasted");
    expect(screen.getByText("Área selecionada").closest("div")).toHaveClass("metric-area");
  });

  it("rejeita JSON inválido na entrada avançada", () => {
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByText("Entrada avançada por GeoJSON"));
    fireEvent.change(screen.getByLabelText("GeoJSON da área de interesse"), { target: { value: "{" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("JSON válido");
  });

  it("exibe loading enquanto valida", async () => {
    let resolve!: (value: typeof validation) => void;
    vi.mocked(api.validateGeometry).mockReturnValue(new Promise((done) => { resolve = done; }));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Validando geometria");
    await act(async () => resolve(validation));
  });

  it("exibe o erro estruturado de validação", async () => {
    vi.mocked(api.validateGeometry).mockRejectedValue(new Error("A geometria enviada não é válida."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A geometria enviada não é válida");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("uma validação atual habilita e executa a análise", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    const runButton = screen.getByRole("button", { name: "Executar análise" });
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    expect((await screen.findAllByText("Vegetação abaixo do nível alto local.")).length).toBeGreaterThan(0);
    expect(api.runAnalysis).toHaveBeenCalledWith({ geometry: polygon });
    expect(screen.getAllByText("10/07/2026 a 10/08/2026").length).toBeGreaterThan(0);
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "nao_cortar");
    expect(screen.getByText("Altura estimada")).toBeInTheDocument();
    expect(screen.getByText("Até 30 cm")).toBeInTheDocument();
    expect(screen.queryByText("0.23")).not.toBeInTheDocument();
    expect(screen.queryByText("height-estimator-v0")).not.toBeInTheDocument();
    expect(screen.getAllByText("Área selecionada").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cobertura efetiva").length).toBeGreaterThan(0);
    expect(screen.getAllByText("93%").length).toBeGreaterThan(0);
    const fitRequest = useAnalysisStore.getState().fitRequestId;
    fireEvent.click(screen.getByRole("button", { name: "Enquadrar área selecionada" }));
    expect(useAnalysisStore.getState().fitRequestId).toBe(fitRequest + 1);
  });

  it("mantém análises históricas sem contabilidade espacial compatíveis", () => {
    const legacy = {
      ...result,
      selected_area_m2: undefined,
      effective_analysis_area_m2: undefined,
      effective_analysis_pct: undefined,
    };

    render(<AnalysisResult result={legacy} />);

    expect(screen.getByText("Área selecionada")).toBeInTheDocument();
    expect(screen.getByText("12.450 m²")).toBeInTheDocument();
    expect(screen.queryByText("Cobertura efetiva")).not.toBeInTheDocument();
  });

  it("invalida o Resultado A assim que uma nova Geometria B é criada", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    await completeCurrentAnalysis();

    expect(useAnalysisStore.getState().activeTab).toBe("result");
    act(() => useAnalysisStore.getState().setGeometry(polygonB, "drawn"));

    expect(useAnalysisStore.getState().geometry).toEqual(polygonB);
    expect(useAnalysisStore.getState().geometryValidation).toBeNull();
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(screen.queryByText("Vegetação abaixo do nível alto local.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Executar análise" })).toBeDisabled();
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "editing");
  });

  it("invalida o resultado quando um vértice da geometria analisada é editado", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    await completeCurrentAnalysis();

    const editedPolygon: PolygonGeometry = {
      ...polygon,
      coordinates: [[...polygon.coordinates[0].slice(0, 2), [-46.9595, -23.1065], ...polygon.coordinates[0].slice(3)]],
    };
    act(() => useAnalysisStore.getState().setGeometry(editedPolygon, "drawn"));

    expect(useAnalysisStore.getState().geometryRevision).toBe(2);
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(screen.queryByText("Vegetação abaixo do nível alto local.")).not.toBeInTheDocument();
  });

  it("limpa geometria, validação e resultado sem apagar o histórico", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    await completeCurrentAnalysis();
    expect(useHistoryStore.getState().entries).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "Área" }));
    fireEvent.click(screen.getByRole("button", { name: "Limpar área" }));

    expect(useAnalysisStore.getState().geometry).toBeNull();
    expect(useAnalysisStore.getState().geometryValidation).toBeNull();
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(useHistoryStore.getState().entries).toHaveLength(1);
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "editing");
  });

  it("reinicia a sessão ao clicar em Painel mesmo quando já está no workspace", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    await completeCurrentAnalysis();

    fireEvent.click(screen.getByRole("button", { name: "Painel" }));

    expect(useAnalysisStore.getState().geometry).toBeNull();
    expect(useAnalysisStore.getState().geometryValidation).toBeNull();
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(useAnalysisStore.getState().selectedTool).toBe("navigate");
    expect(screen.getByText("Nenhuma área delimitada")).toBeInTheDocument();
    expect(screen.queryByText("Vegetação abaixo do nível alto local.")).not.toBeInTheDocument();
  });

  it("descarta a resposta da análise A quando a Geometria B passa a ser atual", async () => {
    let resolve!: (value: AnalysisResponse) => void;
    vi.mocked(api.runAnalysis).mockReturnValue(new Promise((done) => { resolve = done; }));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));

    act(() => useAnalysisStore.getState().setGeometry(polygonB, "drawn"));
    await act(async () => resolve(result));

    expect(useAnalysisStore.getState().geometry).toEqual(polygonB);
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(useHistoryStore.getState().entries).toHaveLength(0);
    expect(screen.queryByText("Vegetação abaixo do nível alto local.")).not.toBeInTheDocument();
  });

  it("mantém uma nova sessão limpa quando a resposta anterior chega atrasada", async () => {
    let resolve!: (value: AnalysisResponse) => void;
    vi.mocked(api.runAnalysis).mockReturnValue(new Promise((done) => { resolve = done; }));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));

    fireEvent.click(screen.getByRole("button", { name: "Painel" }));
    await act(async () => resolve(result));

    expect(useAnalysisStore.getState().geometry).toBeNull();
    expect(useAnalysisStore.getState().currentResult).toBeNull();
    expect(useAnalysisStore.getState().activeTab).toBe("area");
    expect(useHistoryStore.getState().entries).toHaveLength(0);
    expect(screen.getByText("Nenhuma área delimitada")).toBeInTheDocument();
  });

  it("preserva o histórico e restaura geometria e resultado explicitamente", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    await completeCurrentAnalysis();
    expect(useHistoryStore.getState().entries).toHaveLength(1);

    vi.mocked(api.listAnalyses).mockResolvedValueOnce({
      total: 1, limit: 20, offset: 0,
      items: [{
        analysis_id: result.analysis_id,
        created_at: "2026-08-10T12:00:00Z",
        status: "completed",
        decision: "nao_cortar",
        confidence: "high",
        summary: "Vegetação abaixo do nível alto local.",
        period_start: "2026-07-10",
        period_end: "2026-08-10",
        selected_area_m2: 12450,
        analysis_quality_status: "high",
        observation_count: 4,
        analysis_trigger: "manual",
      }],
    });

    fireEvent.click(screen.getByRole("button", { name: "Painel" }));
    expect(useHistoryStore.getState().entries).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Histórico" }));
    fireEvent.click(await screen.findByRole("button", { name: "Abrir análise" }));

    await waitFor(() => expect(api.getAnalysisDetail).toHaveBeenCalledWith(result.analysis_id));

    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(result.analysis_id);
    expect(useAnalysisStore.getState().currentResult?.geometryRevision).toBe(useAnalysisStore.getState().geometryRevision);
    expect(useAnalysisStore.getState().activeTab).toBe("result");
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "nao_cortar");
    expect(screen.getAllByText("Vegetação abaixo do nível alto local.").length).toBeGreaterThan(0);
  });

  it("exibe loading durante a execução da análise", async () => {
    let resolve!: (value: AnalysisResponse) => void;
    vi.mocked(api.runAnalysis).mockReturnValue(new Promise((done) => { resolve = done; }));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Processando cenas Sentinel-2");
    await act(async () => resolve(result));
  });

  it("mantém a área validada quando a execução falha", async () => {
    vi.mocked(api.runAnalysis).mockRejectedValue(new Error("Falha controlada da API."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Falha controlada da API");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("renderiza a série vazia sem falhar", () => {
    render(<AnalysisResult result={{ ...result, timeseries: [] }} />);
    expect(screen.getByText("A análise não produziu observações válidas para o gráfico.")).toBeInTheDocument();
  });

  it("prioriza decisão, confiança, qualidade, contexto, justificativa e gráfico", () => {
    render(<AnalysisResult result={result} />);
    expect(screen.getByRole("heading", { name: "NÃO CORTAR" })).toBeInTheDocument();
    expect(screen.getByText("Confiança da recomendação")).toBeInTheDocument();
    expect(screen.getByText("Qualidade da análise")).toBeInTheDocument();
    expect(screen.getByText(/12\.450 m/)).toBeInTheDocument();
    expect(screen.getByText("10/07/2026 a 10/08/2026")).toBeInTheDocument();
    expect(screen.getByText("A vegetação está abaixo do nível considerado alto no histórico recente.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Evolução da vegetação" })).toBeInTheDocument();
    expect(screen.getByTestId("echarts")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Por que o sistema chegou a esta conclusão?" })).toBeInTheDocument();
    expect(screen.queryByText("Sobre esta análise")).not.toBeInTheDocument();
  });

  it("apresenta o resumo operacional conhecido com acentuação correta", () => {
    const withLegacySummary = {
      ...result,
      recommendation: {
        ...result.recommendation,
        summary: "Evidencias insuficientes para uma recomendacao de corte.",
      },
    };
    render(<AnalysisResult result={withLegacySummary} />);
    expect(screen.getByText("Evidências insuficientes para uma recomendação de corte.")).toBeInTheDocument();
    expect(screen.queryByText("Evidencias insuficientes para uma recomendacao de corte.")).not.toBeInTheDocument();
  });

  it("apresenta cada motivo da recomendação separadamente", () => {
    const withReasons = {
      ...result,
      recommendation: {
        ...result.recommendation,
        reasons: ["current_percentile_below_or_equal_50", "stable_or_decreasing_recent_trend"],
      },
    };
    render(<AnalysisResult result={withReasons} />);
    const section = screen.getByRole("heading", { name: "Por que o sistema chegou a esta conclusão?" }).closest("section");
    expect(section).not.toBeNull();
    expect(within(section!).getAllByRole("listitem")).toHaveLength(2);
  });

  it("não renderiza rastreabilidade e métricas técnicas na visão operacional", () => {
    render(<AnalysisResult result={result} />);
    expect(screen.queryByText("Cenas Sentinel-2")).not.toBeInTheDocument();
    expect(screen.queryByText("S2_TEST")).not.toBeInTheDocument();
    expect(screen.queryByText("Item Sentinel-2")).not.toBeInTheDocument();
    expect(screen.queryByText("NDVI atual")).not.toBeInTheDocument();
    expect(screen.queryByText("Mediana histórica")).not.toBeInTheDocument();
    expect(screen.queryByText("Percentil atual")).not.toBeInTheDocument();
    expect(screen.queryByText("Tendência recente")).not.toBeInTheDocument();
    expect(screen.queryByText("high_vegetation_percentile")).not.toBeInTheDocument();
    expect(screen.queryByText("significant_drop_absolute")).not.toBeInTheDocument();
  });

  it.each([
    ["cortar", "CORTAR"],
    ["nao_cortar", "NÃO CORTAR"],
    ["inconclusivo", "INCONCLUSIVO"],
  ] as const)("aplica o estado visual semântico para %s", (decision, label) => {
    const variant = { ...result, recommendation: { ...result.recommendation, decision } };
    render(<AnalysisResult result={variant} />);
    expect(screen.getByRole("region", { name: label })).toHaveAttribute("data-decision", decision);
  });

  it("torna a altura visual inconclusiva quando conflita com a recommendation", () => {
    const conflicting = {
      ...result,
      height_estimation: {
        ...result.height_estimation!,
        estimated_class: "gt_30_cm" as const,
        probability_gt_30_cm: 0.71,
      },
    };

    render(<AnalysisResult result={conflicting} />);

    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.getByText("Inconclusiva")).toBeInTheDocument();
    expect(screen.queryByText("Acima de 30 cm")).not.toBeInTheDocument();
    expect(screen.queryByText("0.71")).not.toBeInTheDocument();
    expect(conflicting.height_estimation.estimated_class).toBe("gt_30_cm");
    expect(conflicting.height_estimation.probability_gt_30_cm).toBe(0.71);
  });

  it("apresenta baixa qualidade sem transmitir certeza alta", () => {
    const lowQuality = {
      ...result,
      recommendation: { ...result.recommendation, decision: "inconclusivo" as const, confidence: "low" as const },
      summary: { ...result.summary, analysis_quality: { score: 42, status: "low" } },
    };
    render(<AnalysisResult result={lowQuality} />);
    const recommendation = screen.getByRole("region", { name: "INCONCLUSIVO" });
    expect(recommendation).toHaveAttribute("data-quality", "low");
    expect(screen.getByText("Qualidade da análise")).toBeInTheDocument();
    expect(screen.getAllByText("Baixa").length).toBeGreaterThanOrEqual(2);
  });
});
