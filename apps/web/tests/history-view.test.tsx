import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HistoryView } from "@/components/views/history-view";
import * as api from "@/lib/api/analyses";
import type { AnalysisHistoryItem, AnalysisResponse } from "@/lib/schemas/analyses";
import type { PolygonGeometry } from "@/lib/map/geometry";
import { useAnalysisStore } from "@/stores/analysis-store";

vi.mock("@/lib/api/analyses", () => ({
  listAnalyses: vi.fn(),
  getAnalysisDetail: vi.fn(),
  hideAnalysisFromHistory: vi.fn(),
  clearAnalysisHistory: vi.fn(),
}));

const geometry: PolygonGeometry = {
  type: "Polygon",
  coordinates: [[[-47, -23.1], [-46.9, -23.1], [-46.9, -23], [-47, -23.1]]],
};

const result: AnalysisResponse = {
  analysis_id: "automatic-1",
  status: "completed",
  analysis_period: { start_date: "2026-08-01", end_date: "2026-09-01", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
  recommendation: { decision: "cortar", confidence: "high", experimental: true, summary: "Recomendação operacional.", reasons: [], blocking_reasons: [], limitations: [], metrics: { observation_count: 4 } },
  selected_area_m2: 12000,
  aoi: { area_square_meters: 12000 },
  summary: { analysis_quality: { status: "high" } },
  timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
  analysis_trigger: "automatic_viewport",
};

const automatic: AnalysisHistoryItem = {
  analysis_id: "automatic-1", created_at: "2026-09-12T14:00:00Z",
  status: "completed", decision: "cortar", confidence: "high",
  summary: "Automática mais recente.", period_start: "2026-08-01", period_end: "2026-09-01",
  selected_area_m2: 12000, analysis_quality_status: "high", observation_count: 4,
  centroid: { longitude: -46.95, latitude: -23.05 },
  analysis_trigger: "automatic_viewport", road_ref: "SP-330",
  road_name: "Rodovia Anhanguera", section_id: "section-1",
};
const manual: AnalysisHistoryItem = {
  ...automatic, analysis_id: "manual-1", created_at: "2026-09-11T14:00:00Z",
  decision: "nao_cortar", summary: "Manual anterior.", analysis_trigger: "manual",
  road_ref: null, road_name: null, section_id: null,
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function HistoryQueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return HistoryQueryWrapper;
}

function renderHistory() {
  const onStartNewAnalysis = vi.fn();
  const onOpenWorkspace = vi.fn();
  render(<HistoryView onStartNewAnalysis={onStartNewAnalysis} onOpenWorkspace={onOpenWorkspace} />, { wrapper: wrapper() });
  return { onStartNewAnalysis, onOpenWorkspace };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.hideAnalysisFromHistory).mockResolvedValue({ analysis_id: automatic.analysis_id, hidden: true });
  vi.mocked(api.clearAnalysisHistory).mockResolvedValue({ hidden_count: 2 });
  useAnalysisStore.setState({
    geometry: null, geometryRevision: 0, geometrySource: null,
    geometryValidation: null, isGeometryDirty: false, selectedTool: "navigate",
    lastValidatedGeometryRevision: null, lastValidatedAt: null, geometryText: "",
    fitRequestId: 0, alertTarget: null, alertFitRequestId: 0,
    activeTab: "area", currentResult: null,
  });
});

describe("Histórico persistido", () => {
  it("carrega análises automáticas e manuais do backend na ordem persistida", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 2, limit: 20, offset: 0, items: [automatic, manual] });
    renderHistory();
    expect(screen.getByRole("status")).toHaveTextContent("Carregando histórico");
    expect(await screen.findByText("Automática mais recente.")).toBeInTheDocument();
    expect(screen.getByText("Manual anterior.")).toBeInTheDocument();
    expect(screen.getByText("SP-330 · Rodovia Anhanguera")).toBeInTheDocument();
    expect(screen.getByText("Automática")).toBeInTheDocument();
    expect(screen.getByText("Manual")).toBeInTheDocument();
    const cards = screen.getAllByRole("listitem");
    expect(cards[0]).toHaveTextContent("Automática mais recente.");
    expect(cards[1]).toHaveTextContent("Manual anterior.");
    expect(api.listAnalyses).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(screen.queryByText(/salvas neste navegador/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /excluir análise automatic-1/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /limpar histórico/i })).toBeInTheDocument();
  });

  it("refetch inclui uma análise recém-persistida", async () => {
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 1, limit: 20, offset: 0, items: [manual] })
      .mockResolvedValueOnce({ total: 2, limit: 20, offset: 0, items: [automatic, manual] });
    renderHistory();
    await screen.findByText("Manual anterior.");
    fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    expect(await screen.findByText("Automática mais recente.")).toBeInTheDocument();
    expect(api.listAnalyses).toHaveBeenCalledTimes(2);
  });

  it("abre o detalhe persistido, restaura resultado e prepara fit protegido", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [automatic] });
    vi.mocked(api.getAnalysisDetail).mockResolvedValue({
      analysis_id: automatic.analysis_id, created_at: automatic.created_at,
      geometry, result,
    });
    const { onOpenWorkspace } = renderHistory();
    await screen.findByText("Automática mais recente.");
    fireEvent.click(screen.getByRole("button", { name: "Abrir análise" }));
    await waitFor(() => expect(onOpenWorkspace).toHaveBeenCalledOnce());
    const state = useAnalysisStore.getState();
    expect(state.currentResult?.response.analysis_id).toBe("automatic-1");
    expect(state.geometry).toEqual(geometry);
    expect(state.activeTab).toBe("result");
    expect(state.alertFitRequestId).toBeGreaterThan(0);
    expect(state.alertTarget?.road_ref).toBe("SP-330");
  });

  it("registro legado sem geometry abre o resultado sem quebrar", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [manual] });
    vi.mocked(api.getAnalysisDetail).mockResolvedValue({
      analysis_id: manual.analysis_id, created_at: manual.created_at,
      geometry: null, result: { ...result, analysis_id: manual.analysis_id, analysis_trigger: "manual" },
    });
    const { onOpenWorkspace } = renderHistory();
    await screen.findByText("Manual anterior.");
    fireEvent.click(screen.getByRole("button", { name: "Abrir análise" }));
    await waitFor(() => expect(onOpenWorkspace).toHaveBeenCalledOnce());
    expect(useAnalysisStore.getState().geometry).toBeNull();
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe("manual-1");
  });

  it("trata estados vazio e erro", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValueOnce({ total: 0, limit: 20, offset: 0, items: [] });
    const first = renderHistory();
    expect(await screen.findByText("Nenhuma análise registrada")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ir para Nova análise/i }));
    expect(first.onStartNewAnalysis).toHaveBeenCalledOnce();
  });

  it("exibe erro de carregamento e permite tentar novamente", async () => {
    vi.mocked(api.listAnalyses)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ total: 1, limit: 20, offset: 0, items: [automatic] });
    renderHistory();
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível carregar o histórico");
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("Automática mais recente.")).toBeInTheDocument();
  });

  it("pagina sem carregar o banco inteiro", async () => {
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 21, limit: 20, offset: 0, items: [automatic] })
      .mockResolvedValueOnce({ total: 21, limit: 20, offset: 20, items: [manual] });
    renderHistory();
    await screen.findByText("Automática mais recente.");
    fireEvent.click(screen.getByRole("button", { name: "Próxima" }));
    expect(await screen.findByText("Manual anterior.")).toBeInTheDocument();
    expect(api.listAnalyses).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
  });

  it("pede confirmação e permite cancelar a exclusão individual", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [automatic] });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: /excluir análise automatic-1/i }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Excluir esta análise?");
    expect(dialog).toHaveTextContent("Ela será removida da visualização do Histórico.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(api.hideAnalysisFromHistory).not.toHaveBeenCalled();
    expect(screen.getByText("Automática mais recente.")).toBeInTheDocument();
  });

  it("oculta um item, atualiza o contador e mantém os demais", async () => {
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 2, limit: 20, offset: 0, items: [automatic, manual] })
      .mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [manual] });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: /excluir análise automatic-1/i }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir" }));
    await waitFor(() => expect(vi.mocked(api.hideAnalysisFromHistory).mock.calls[0]?.[0]).toBe("automatic-1"));
    await waitFor(() => expect(screen.queryByText("Automática mais recente.")).not.toBeInTheDocument());
    expect(screen.getByText("Manual anterior.")).toBeInTheDocument();
    expect(screen.getByText("1–1 de 1 análise")).toBeInTheDocument();
  });

  it("mantém o item visível quando a exclusão falha", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [automatic] });
    vi.mocked(api.hideAnalysisFromHistory).mockRejectedValueOnce(new Error("offline"));
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: /excluir análise automatic-1/i }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível excluir esta análise");
    expect(screen.getByText("Automática mais recente.")).toBeInTheDocument();
  });

  it("ignora clique duplo enquanto a exclusão está em andamento", async () => {
    let finish!: (value: { analysis_id: string; hidden: true }) => void;
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 1, limit: 20, offset: 0, items: [automatic] })
      .mockResolvedValue({ total: 0, limit: 20, offset: 0, items: [] });
    vi.mocked(api.hideAnalysisFromHistory).mockReturnValueOnce(
      new Promise((resolve) => { finish = resolve; }),
    );
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: /excluir análise automatic-1/i }));
    const confirm = within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(api.hideAnalysisFromHistory).toHaveBeenCalledTimes(1));
    finish({ analysis_id: automatic.analysis_id, hidden: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("confirma e limpa todos os itens visíveis", async () => {
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 2, limit: 20, offset: 0, items: [automatic, manual] })
      .mockResolvedValue({ total: 0, limit: 20, offset: 0, items: [] });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "Limpar histórico" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Limpar todo o histórico?");
    expect(dialog).toHaveTextContent("Todas as análises serão removidas da visualização do Histórico.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Limpar histórico" }));
    await waitFor(() => expect(api.clearAnalysisHistory).toHaveBeenCalledOnce());
    expect(await screen.findByText("Nenhuma análise registrada")).toBeInTheDocument();
  });

  it("permite cancelar a limpeza total", async () => {
    vi.mocked(api.listAnalyses).mockResolvedValue({ total: 1, limit: 20, offset: 0, items: [automatic] });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "Limpar histórico" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancelar" }));
    expect(api.clearAnalysisHistory).not.toHaveBeenCalled();
  });

  it("volta à página anterior ao excluir o último item da página atual", async () => {
    vi.mocked(api.listAnalyses)
      .mockResolvedValueOnce({ total: 21, limit: 20, offset: 0, items: [automatic] })
      .mockResolvedValueOnce({ total: 21, limit: 20, offset: 20, items: [manual] })
      .mockResolvedValue({ total: 20, limit: 20, offset: 0, items: [automatic] });
    vi.mocked(api.hideAnalysisFromHistory).mockResolvedValueOnce({ analysis_id: manual.analysis_id, hidden: true });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "Próxima" }));
    await screen.findByText("Manual anterior.");
    fireEvent.click(screen.getByRole("button", { name: /excluir análise manual-1/i }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir" }));
    await waitFor(() => expect(api.listAnalyses).toHaveBeenCalledWith({ limit: 20, offset: 0 }));
    expect(await screen.findByText("Automática mais recente.")).toBeInTheDocument();
  });
});
