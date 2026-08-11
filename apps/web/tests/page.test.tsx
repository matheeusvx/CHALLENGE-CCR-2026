import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import * as api from "@/lib/api/analyses";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { useAnalysisStore } from "@/stores/analysis-store";
import type { PolygonGeometry } from "@/lib/map/geometry";

vi.mock("@/lib/api/analyses", () => ({ getHealth: vi.fn(), validateGeometry: vi.fn(), runAnalysis: vi.fn() }));
vi.mock("@/components/map/analysis-map", () => ({
  AnalysisMap: ({ result: mapResult }: { result?: AnalysisResponse }) => <div data-testid="analysis-map" data-decision={mapResult?.recommendation.decision ?? "editing"} />,
}));

const polygon: PolygonGeometry = {
  type: "Polygon" as const,
  coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]],
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
  useAnalysisStore.setState({ geometry: null, geometryRevision: 0, geometrySource: null, geometryValidation: null, isGeometryDirty: false, selectedTool: "navigate", lastValidatedGeometryRevision: null, lastValidatedAt: null, geometryText: "", fitRequestId: 0, activeTab: "area" });
});

describe("workspace geoespacial", () => {
  it("renderiza a pagina, o mapa e o status da API", async () => {
    render(<Home />, { wrapper });
    expect(screen.getByRole("heading", { name: "Motiva Vegetation Intelligence" })).toBeInTheDocument();
    expect(screen.getByTestId("analysis-map")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("api-status")).toHaveTextContent("API operacional"));
  });

  it("mostra somente as etapas Area e Resultado sem controles tecnicos", () => {
    render(<Home />, { wrapper });
    expect(screen.getByRole("tab", { name: "Área" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Resultado" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Parâmetros" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data inicial")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data final")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cobertura maxima de nuvens")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Quantidade maxima de cenas")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pixels validos minimos")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Agregacao diaria")).not.toBeInTheDocument();
  });

  it("mostra o estado sem geometria e bloqueia a analise", () => {
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
  });

  it("rejeita JSON invalido na entrada avancada", () => {
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

  it("exibe o erro estruturado de validacao", async () => {
    vi.mocked(api.validateGeometry).mockRejectedValue(new Error("A geometria enviada nao e valida."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A geometria enviada nao e valida");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("uma validacao atual habilita e executa a analise", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    const runButton = screen.getByRole("button", { name: "Executar análise" });
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    expect((await screen.findAllByText("Vegetação abaixo do nível alto local.")).length).toBeGreaterThan(0);
    expect(api.runAnalysis).toHaveBeenCalledWith({ geometry: polygon }, expect.anything());
    expect(screen.getAllByText("10/07/2026 a 10/08/2026").length).toBeGreaterThan(0);
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "nao_cortar");
    const fitRequest = useAnalysisStore.getState().fitRequestId;
    fireEvent.click(screen.getByRole("button", { name: "Enquadrar área analisada" }));
    expect(useAnalysisStore.getState().fitRequestId).toBe(fitRequest + 1);
  });

  it("exibe loading durante a execucao da analise", async () => {
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

  it("mantem a area validada quando a execucao falha", async () => {
    vi.mocked(api.runAnalysis).mockRejectedValue(new Error("Falha controlada da API."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar área" }));
    await screen.findByText("Área validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar análise" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Falha controlada da API");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("renderiza a serie vazia sem falhar", () => {
    render(<AnalysisResult result={{ ...result, timeseries: [] }} />);
    expect(screen.getByText("A análise não produziu observações válidas para o gráfico.")).toBeInTheDocument();
  });

  it("prioriza decisao, confianca, qualidade, contexto, justificativa e grafico", () => {
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

  it("apresenta cada motivo da recomendacao separadamente", () => {
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

  it("nao renderiza rastreabilidade e metricas tecnicas na visao operacional", () => {
    render(<AnalysisResult result={result} />);
    expect(screen.queryByText("Cenas Sentinel-2")).not.toBeInTheDocument();
    expect(screen.queryByText("S2_TEST")).not.toBeInTheDocument();
    expect(screen.queryByText("Item Sentinel-2")).not.toBeInTheDocument();
    expect(screen.queryByText("NDVI atual")).not.toBeInTheDocument();
    expect(screen.queryByText("Mediana historica")).not.toBeInTheDocument();
    expect(screen.queryByText("Percentil atual")).not.toBeInTheDocument();
    expect(screen.queryByText("Tendencia recente")).not.toBeInTheDocument();
    expect(screen.queryByText("high_vegetation_percentile")).not.toBeInTheDocument();
    expect(screen.queryByText("significant_drop_absolute")).not.toBeInTheDocument();
  });

  it.each([
    ["cortar", "CORTAR"],
    ["nao_cortar", "NÃO CORTAR"],
    ["inconclusivo", "INCONCLUSIVO"],
  ] as const)("aplica o estado visual semantico para %s", (decision, label) => {
    const variant = { ...result, recommendation: { ...result.recommendation, decision } };
    render(<AnalysisResult result={variant} />);
    expect(screen.getByRole("region", { name: label })).toHaveAttribute("data-decision", decision);
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
