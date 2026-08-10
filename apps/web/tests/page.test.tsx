import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  recommendation: { decision: "nao_cortar", confidence: "high", experimental: true, summary: "Vegetacao abaixo do nivel alto local.", reasons: ["current_percentile_below_or_equal_50"], blocking_reasons: [], limitations: ["Validacao de campo necessaria."], metrics: { current_ndvi_mean: 0.52, historical_median: 0.58, current_percentile: 40, recent_trend: -0.01, observation_count: 4 } },
  aoi: { source: "geojson_inline" },
  summary: { date_range_effectively_processed: { start: "2026-06-01", end: "2026-08-01" } },
  timeseries: [{ datetime: "2026-06-01T00:00:00Z", ndvi_mean: 0.61, ndvi_median: 0.6 }, { datetime: "2026-08-01T00:00:00Z", ndvi_mean: 0.52, ndvi_median: 0.51 }],
  scenes: [{ item_id: "S2_TEST", datetime: "2026-08-01T00:00:00Z", cloud_cover: 8, valid_pixel_percentage: 92, quality_status: "high", accepted_for_timeseries: true, daily_aggregation: "best" }],
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
    expect(screen.getByRole("tab", { name: "Area" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Resultado" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Parametros" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data inicial")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data final")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cobertura maxima de nuvens")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Quantidade maxima de cenas")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pixels validos minimos")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Agregacao diaria")).not.toBeInTheDocument();
  });

  it("mostra o estado sem geometria e bloqueia a analise", () => {
    render(<Home />, { wrapper });
    expect(screen.getByText("Nenhuma area delimitada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Executar analise" })).toBeDisabled();
  });

  it("aplica um Polygon colado ao mapa", () => {
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByText("Entrada avancada por GeoJSON"));
    fireEvent.change(screen.getByLabelText("GeoJSON da area de interesse"), { target: { value: JSON.stringify(polygon) } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(screen.getByText("Area aguardando validacao")).toBeInTheDocument();
    expect(useAnalysisStore.getState().geometrySource).toBe("pasted");
  });

  it("rejeita JSON invalido na entrada avancada", () => {
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByText("Entrada avancada por GeoJSON"));
    fireEvent.change(screen.getByLabelText("GeoJSON da area de interesse"), { target: { value: "{" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("JSON valido");
  });

  it("exibe loading enquanto valida", async () => {
    let resolve!: (value: typeof validation) => void;
    vi.mocked(api.validateGeometry).mockReturnValue(new Promise((done) => { resolve = done; }));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar area" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Validando geometria");
    await act(async () => resolve(validation));
  });

  it("exibe o erro estruturado de validacao", async () => {
    vi.mocked(api.validateGeometry).mockRejectedValue(new Error("A geometria enviada nao e valida."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar area" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A geometria enviada nao e valida");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("uma validacao atual habilita e executa a analise", async () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar area" }));
    await screen.findByText("Area validada");
    const runButton = screen.getByRole("button", { name: "Executar analise" });
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    expect((await screen.findAllByText("Vegetacao abaixo do nivel alto local.")).length).toBeGreaterThan(0);
    expect(api.runAnalysis).toHaveBeenCalledWith({ geometry: polygon }, expect.anything());
    expect(screen.getAllByText("2026-07-10 a 2026-08-10").length).toBeGreaterThan(0);
    expect(screen.getByTestId("analysis-map")).toHaveAttribute("data-decision", "nao_cortar");
  });

  it("mantem a area validada quando a execucao falha", async () => {
    vi.mocked(api.runAnalysis).mockRejectedValue(new Error("Falha controlada da API."));
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<Home />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Validar area" }));
    await screen.findByText("Area validada");
    fireEvent.click(screen.getByRole("button", { name: "Executar analise" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Falha controlada da API");
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
  });

  it("renderiza a serie vazia sem falhar", () => {
    render(<AnalysisResult result={{ ...result, timeseries: [] }} />);
    expect(screen.getByText("A analise nao produziu observacoes validas para o grafico.")).toBeInTheDocument();
  });

  it("renderiza o grafico e a tabela de cenas", () => {
    render(<AnalysisResult result={result} />);
    expect(screen.getByTestId("echarts")).toBeInTheDocument();
    expect(screen.getByText("S2_TEST")).toBeInTheDocument();
    expect(screen.getByText("92.0%")).toBeInTheDocument();
  });
});
