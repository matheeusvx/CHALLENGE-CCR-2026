import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PolygonGeometry } from "@/lib/map/geometry";
import {
  automaticAnalysisResponseSchema,
  type AnalysisResponse,
  type AutomaticAnalysisResponse,
  type ViewportBounds,
} from "@/lib/schemas/analyses";
import { AUTO_ANALYSIS_CONFIG, computeTileKey, DEFAULT_MAP_STYLE_ID, MAP_CONFIG } from "@/lib/map/config";
import { getCurrentAnalysisResponse, useAnalysisStore } from "@/stores/analysis-store";
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";
import { useHistoryStore } from "@/stores/history-store";

type MapMockShape = {
  options: Record<string, unknown>;
  handlers: Map<string, Set<(...args: never[]) => void>>;
  sources: Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>;
  layers: Map<string, { id: string; paint?: Record<string, unknown> }>;
  fitBounds: ReturnType<typeof vi.fn>;
  setStyle: ReturnType<typeof vi.fn>;
  setFilter: ReturnType<typeof vi.fn>;
  setLayoutProperty: ReturnType<typeof vi.fn>;
  moveLayer: ReturnType<typeof vi.fn>;
  loaded: boolean;
  removed: boolean;
  zoom: number;
  center: { lng: number; lat: number };
  bounds: { west: number; south: number; east: number; north: number };
  emit: (event: string, payload?: unknown) => void;
  getBounds: () => { getWest: () => number; getSouth: () => number; getEast: () => number; getNorth: () => number };
  getZoom: () => number;
  getCenter: () => { lng: number; lat: number };
  getLayer: (id: string) => { id: string; paint?: Record<string, unknown> } | undefined;
  getSource: (id: string) => { setData: ReturnType<typeof vi.fn>; data: unknown } | undefined;
};

type DrawMockShape = {
  handlers: Map<string, Set<() => void>>;
  snapshot: Array<{ id?: string; geometry: PolygonGeometry }>;
  mode: string;
  stopped: boolean;
  emit: (event: string) => void;
};

const runtime = vi.hoisted(() => ({
  maps: [] as MapMockShape[],
  draws: [] as DrawMockShape[],
  popups: [] as Array<Record<string, unknown>>,
  workerUrls: [] as string[],
}));

vi.mock("maplibre-gl", () => {
  class MapMock {
    options: Record<string, unknown>;
    handlers = new Map<string, Set<(...args: never[]) => void>>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
    layers = new Map<string, { id: string; paint?: Record<string, unknown> }>();
    fitBounds = vi.fn();
    moveLayer = vi.fn();
    removed = false;
    loaded = false;
    canvas = document.createElement("canvas");
    setStyle = vi.fn();
    setFilter = vi.fn();
    setLayoutProperty = vi.fn();
    zoom = 17;
    center = { lng: -46.961, lat: -23.108 };
    bounds = { west: -46.9625, south: -23.1095, east: -46.9595, north: -23.1065 };

    constructor(options: Record<string, unknown>) {
      this.options = options;
      runtime.maps.push(this);
    }
    key(event: string, layer?: string) { return layer ? `${event}:${layer}` : event; }
    on(event: string, layerOrHandler: string | ((...args: never[]) => void), handler?: (...args: never[]) => void) {
      const key = this.key(event, typeof layerOrHandler === "string" ? layerOrHandler : undefined);
      const listener = typeof layerOrHandler === "function" ? layerOrHandler : handler!;
      const listeners = this.handlers.get(key) ?? new Set();
      listeners.add(listener);
      this.handlers.set(key, listeners);
    }
    off(event: string, layerOrHandler: string | ((...args: never[]) => void), handler?: (...args: never[]) => void) {
      const key = this.key(event, typeof layerOrHandler === "string" ? layerOrHandler : undefined);
      const listener = typeof layerOrHandler === "function" ? layerOrHandler : handler!;
      this.handlers.get(key)?.delete(listener);
    }
    emit(event: string, payload?: unknown) {
      if (event === "style.load" || event === "load") this.loaded = true;
      this.handlers.get(event)?.forEach((handler) => handler(payload as never));
    }
    addControl() {}
    addSource(id: string, source: { data: unknown }) { this.sources.set(id, { data: source.data, setData: vi.fn() }); }
    getSource(id: string) { return this.sources.get(id); }
    removeSource(id: string) { this.sources.delete(id); }
    addLayer(layer: { id: string; paint?: Record<string, unknown> }) { this.layers.set(layer.id, layer); }
    getLayer(id: string) { return this.layers.get(id); }
    removeLayer(id: string) { this.layers.delete(id); }
    setPaintProperty(id: string, key: string, value: unknown) {
      const layer = this.layers.get(id);
      if (layer) layer.paint = { ...layer.paint, [key]: value };
    }
    setFeatureState() {}
    getCanvas() { return this.canvas; }
    getStyle() { return this.options.style as { glyphs?: string }; }
    isStyleLoaded() { return this.loaded; }
    getCenter() { return this.center; }
    getZoom() { return this.zoom; }
    getBearing() { return 0; }
    getPitch() { return 0; }
    getBounds() {
      return {
        getWest: () => this.bounds.west,
        getSouth: () => this.bounds.south,
        getEast: () => this.bounds.east,
        getNorth: () => this.bounds.north,
      };
    }
    remove() { this.removed = true; this.handlers.clear(); }
  }
  return {
    Map: MapMock,
    setWorkerUrl: (url: string) => runtime.workerUrls.push(url),
    NavigationControl: class {},
    Popup: class {
      options: Record<string, unknown>;
      constructor(options: Record<string, unknown>) { this.options = options; runtime.popups.push(this as unknown as Record<string, unknown>); }
      setLngLat() { return this; }
      setDOMContent() { return this; }
      addTo() { return this; }
      remove() { return this; }
    },
  };
});

vi.mock("terra-draw", () => {
  class DrawMock {
    handlers = new Map<string, Set<() => void>>();
    snapshot: Array<{ id?: string; geometry: PolygonGeometry }> = [];
    mode = "navigate";
    stopped = false;
    constructor() { runtime.draws.push(this); }
    on(event: string, handler: () => void) {
      const handlers = this.handlers.get(event) ?? new Set();
      handlers.add(handler);
      this.handlers.set(event, handlers);
    }
    off(event: string, handler: () => void) { this.handlers.get(event)?.delete(handler); }
    emit(event: string) { this.handlers.get(event)?.forEach((handler) => handler()); }
    start() {}
    stop() { this.stopped = true; this.handlers.clear(); }
    setMode(mode: string) { this.mode = mode; }
    getMode() { return this.mode; }
    getSnapshot() { return this.snapshot; }
    clear() { this.snapshot = []; }
    addFeatures(features: Array<{ id?: string; geometry: PolygonGeometry }>) { this.snapshot = features; }
    removeFeatures() {}
    clearUndoRedoHistory() {}
    canUndo() { return false; }
    canRedo() { return false; }
    undo() {}
    redo() {}
  }
  return {
    TerraDraw: DrawMock,
    TerraDrawPolygonMode: class {},
    TerraDrawRenderMode: class {},
    TerraDrawSelectMode: class {},
    TerraDrawSessionUndoRedo: class {},
  };
});

vi.mock("terra-draw-maplibre-gl-adapter", () => ({ TerraDrawMapLibreGLAdapter: class {} }));

const mockRunAutomaticAnalysis = vi.fn();
vi.mock("@/lib/api/analyses", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/analyses")>();
  return {
    ...actual,
    runAutomaticAnalysis: (...args: unknown[]) => mockRunAutomaticAnalysis(...args),
  };
});

import { MapCanvas } from "@/components/map/map-canvas";
import { AutoAnalysisPanel } from "@/components/map/auto-analysis-panel";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import { CANONICAL_AOI_LAYER_IDS } from "@/components/map/layers/canonical-aoi-layer";

const mockBounds: ViewportBounds = {
  west: -46.9625,
  south: -23.1095,
  east: -46.9595,
  north: -23.1065,
};

const makeMockResult = (decision: "cortar" | "nao_cortar" | "inconclusivo" = "nao_cortar"): AnalysisResponse => ({
  analysis_id: "7b47b4d1-81d3-4f05-8e3d-71b312b07e5b",
  status: "completed",
  analysis_trigger: "automatic_viewport",
  analysis_period: {
    start_date: "2026-07-01",
    end_date: "2026-07-31",
    timezone: "America/Sao_Paulo",
    strategy: "previous_calendar_month",
  },
  recommendation: {
    decision,
    confidence: "high",
    experimental: true,
    summary: "Monitoramento concluído.",
    reasons: [],
    blocking_reasons: [],
    limitations: [],
    metrics: {},
  },
  aoi: {},
  summary: {},
  timeseries: [],
  scenes: [],
  artifacts: {},
  warnings: [],
  errors: [],
});

describe("AUTO-02 — Análise automática por viewport no mapa", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockRunAutomaticAnalysis.mockReset();
    runtime.maps.length = 0;
    runtime.draws.length = 0;
    runtime.popups.length = 0;

    useAnalysisStore.setState({
      geometry: null,
      geometryRevision: 0,
      geometrySource: null,
      geometryValidation: null,
      isGeometryDirty: false,
      selectedTool: "navigate",
      activeMapStyle: DEFAULT_MAP_STYLE_ID,
      mapViewport: { ...MAP_CONFIG.initialViewport },
      lastValidatedGeometryRevision: null,
      lastValidatedAt: null,
      fitRequestId: 0,
      activeTab: "area",
    });

    useAutoAnalysisStore.setState({
      enabled: false,
      uiStatus: "disabled",
      currentSpatialKey: null,
      canonicalBounds: null,
      result: null,
      isCacheHit: false,
      reason: null,
    });

    useHistoryStore.setState({ entries: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("A) controle automático compacto — sem card grande", () => {
    const { container } = render(<AutoAnalysisPanel />);
    const pill = container.querySelector(".auto-analysis-pill");
    expect(pill).toBeInTheDocument();

    const switchInput = screen.getByRole("switch", { name: "Ativar análise automática por navegação" });
    expect(switchInput).not.toBeChecked();

    // Nenhum card grande ou cabeçalho volumoso presente
    expect(container.querySelector(".auto-analysis-card")).toBeNull();
    expect(container.querySelector(".auto-analysis-header")).toBeNull();
    // Sem badge de resultado sem análise concluída
    expect(container.querySelector(".auto-result-badge")).toBeNull();

    // Toggle ativa o estado
    fireEvent.click(switchInput);
    expect(useAutoAnalysisStore.getState().enabled).toBe(true);
    expect(switchInput).toBeChecked();
  });

  it("B) spatial_key nunca aparece visualmente", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "idle",
      currentSpatialKey: "tile:17/48443/72688",
    });

    const { container } = render(<MapCanvas />);
    expect(screen.queryByText(/tile:17\/48443\/72688/)).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("tile:17/48443/72688");
  });

  it("C) tile id não aparece após resultado", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      spatial_key: "tile:17/48443/72688",
      canonical_bounds: mockBounds,
      result: makeMockResult(),
    } satisfies AutomaticAnalysisResponse);

    const { container } = render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("tile:17/48443/72688");
    expect(container.textContent).not.toMatch(/tile:17/);
  });

  it("D) AOI temporária desaparece após conclusão", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      spatial_key: "tile:17/48443/72688",
      result: makeMockResult(),
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Inicia análise (estado transitório analyzing)
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    // Durante a resposta completed/cache_hit:
    await act(async () => {
      await Promise.resolve();
    });

    // O overlay canônico é removido do mapa após conclusão
    expect(map.getLayer(CANONICAL_AOI_LAYER_IDS.outline)).toBeUndefined();
    expect(map.getLayer(CANONICAL_AOI_LAYER_IDS.fill)).toBeUndefined();
  });

  it("E) novo zoom mínimo permite análise mais afastada", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result: makeMockResult(),
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Zoom 13 é aceito e dispara análise
    map.zoom = 13;
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(mockRunAutomaticAnalysis).toHaveBeenCalledWith(
      expect.objectContaining({ zoom: 13 }),
      expect.any(AbortSignal),
    );

    // Zoom 12 está abaixo do novo mínimo (13) e exige mais zoom
    map.zoom = 12;
    mockRunAutomaticAnalysis.mockClear();
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    expect(mockRunAutomaticAnalysis).not.toHaveBeenCalled();
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("zoom_required");
    expect(screen.getByText("Aproxime o mapa")).toBeInTheDocument();
  });

  it("mantém polling além do antigo limite de 75s até o backend concluir", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const completedResult = makeMockResult("nao_cortar");
    let callCount = 0;
    mockRunAutomaticAnalysis.mockImplementation(async () => {
      callCount += 1;
      if (callCount <= 31) {
        return {
          status: callCount === 1 ? "analysis_started" : "in_progress",
          analysis_id: "long-running-analysis",
          analysis_started: callCount === 1,
          cache_hit: false,
          automatic: true,
          spatial_key: "roadside:v1:test:section:1:side:both:profile",
          canonical_bounds: mockBounds,
          cache_state: "in_progress",
          expires_at: "2026-09-08T17:00:00-03:00",
        } satisfies AutomaticAnalysisResponse;
      }
      return {
        status: "completed",
        analysis_id: completedResult.analysis_id,
        analysis_started: false,
        cache_hit: false,
        automatic: true,
        canonical_bounds: mockBounds,
        result: completedResult,
      } satisfies AutomaticAnalysisResponse;
    });

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.debounceMs); });

    for (let index = 0; index < 31; index += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.pollingIntervalMs);
      });
    }

    expect(mockRunAutomaticAnalysis).toHaveBeenCalledTimes(32);
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("completed");
    expect(useAutoAnalysisStore.getState().reason).not.toBe("timeout");
    expect(useAutoAnalysisStore.getState().result?.analysis_id).toBe(completedResult.analysis_id);
  });

  it("F) backend e frontend possuem limites consistentes", () => {
    expect(AUTO_ANALYSIS_CONFIG.minZoom).toBe(13);
  });

  it("G) analysis completed → resultado vira análise selecionada", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const completedResult = makeMockResult("cortar");
    completedResult.analysis_id = "comp-1111-2222-3333-444444444444";

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result: completedResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    const storeState = useAnalysisStore.getState();
    expect(storeState.currentResult?.response.analysis_id).toBe(completedResult.analysis_id);
    expect(getCurrentAnalysisResponse(storeState)?.analysis_id).toBe(completedResult.analysis_id);
  });

  it("H) cache_hit → resultado vira análise selecionada", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const cachedResult = makeMockResult("nao_cortar");
    cachedResult.analysis_id = "cache-aaaa-bbbb-cccc-dddddddddddd";

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result: cachedResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    const storeState = useAnalysisStore.getState();
    expect(storeState.currentResult?.response.analysis_id).toBe(cachedResult.analysis_id);
    expect(getCurrentAnalysisResponse(storeState)?.analysis_id).toBe(cachedResult.analysis_id);
  });

  it("I) sidebar direita muda automaticamente para Resultado", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    expect(useAnalysisStore.getState().activeTab).toBe("area");

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result: makeMockResult(),
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(useAnalysisStore.getState().activeTab).toBe("result");
  });

  it("J) operador não precisa visitar Histórico", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const result = makeMockResult("cortar");

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    // Análise já está selecionada e ativa diretamente no store sem necessidade de intervenção do Histórico
    expect(useAnalysisStore.getState().activeTab).toBe("result");
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(result.analysis_id);
  });

  it("K) Histórico ainda recebe análise automática", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const result = makeMockResult();

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    const entries = useHistoryStore.getState().entries;
    expect(entries).toHaveLength(1);
    expect(entries[0].response.analysis_id).toBe(result.analysis_id);
    expect(entries[0].response.analysis_trigger).toBe("automatic_viewport");
  });

  it("L) manual continua funcionando", () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));

    // Selecionar ferramenta de desenho manual suspende análise automática
    fireEvent.click(screen.getByRole("button", { name: "Desenhar polígono" }));
    expect(useAnalysisStore.getState().selectedTool).toBe("draw");

    // Micro pan não dispara auto análise quando em draw
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1500));
    expect(mockRunAutomaticAnalysis).not.toHaveBeenCalled();

    // Desenhar polígono manual salva no analysis-store
    const draw = runtime.draws.at(-1)!;
    const manualPolygon: PolygonGeometry = {
      type: "Polygon",
      coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]],
    };
    draw.snapshot = [{ id: "manual-1", geometry: manualPolygon }];
    draw.mode = "polygon";
    act(() => draw.emit("finish"));

    expect(useAnalysisStore.getState().geometry).toEqual(manualPolygon);
    expect(useAnalysisStore.getState().isGeometryDirty).toBe(true);
  });

  it("M) resultado principal segue Sentinel-2 e remove detalhes internos da fusao", async () => {
    const fusionResult = makeMockResult("cortar");
    fusionResult.multisource = {
      enabled: true,
      fusion_mode: "experimental",
      generated_at: "2026-08-01T00:00:00Z",
      experimental_fusion: {
        schema_version: "1.0",
        fusion_mode: "experimental",
        experimental_policy_version: "1.0",
        sentinel2_recommendation: "cortar",
        multisource_recommendation: "inconclusivo",
        sentinel1_influenced_decision: true,
        fusion_rule: "B",
        fusion_reason: "sentinel1_temporal_mixed_with_sentinel2_cut",
        sentinel1_temporal_status: "mixed",
        experimental: true,
        operationally_authorized: false,
        experimental_fusion_evaluated: true,
        experimental_fusion_evaluable: true,
        fusion_not_evaluable_reason: null,
      },
    };

    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      automatic: true,
      canonical_bounds: mockBounds,
      result: fusionResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    const activeResult = useAnalysisStore.getState().currentResult?.response;
    expect(activeResult?.analysis_id).toBe(fusionResult.analysis_id);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    // Renderizar a sidebar com a análise selecionada
    render(
      <QueryClientProvider client={queryClient}>
        <AnalysisResultSidebar result={activeResult} onRetry={() => undefined} />
      </QueryClientProvider>
    );

    // UI principal mostra CORTAR em destaque (priorizando Sentinel-2)
    expect(screen.getAllByText("CORTAR").length).toBeGreaterThanOrEqual(1);
    // NÃO exibe detalhes internos de fusão na UI operacional
    expect(screen.queryByText("Sentinel-1 influenciou esta análise")).not.toBeInTheDocument();
    expect(screen.queryByText("Regra B")).not.toBeInTheDocument();
    expect(screen.queryByText("Resultado multissensor")).not.toBeInTheDocument();
    expect(screen.queryByText(/Sentinel-2:\s*CORTAR/)).not.toBeInTheDocument();
  });

  it("N) stale response não troca sidebar para resultado antigo", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });

    let resolveRequestA: (val: unknown) => void = () => undefined;
    const promiseA = new Promise((resolve) => {
      resolveRequestA = resolve;
    });

    const resultB = makeMockResult("cortar");
    resultB.analysis_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

    mockRunAutomaticAnalysis
      .mockImplementationOnce(() => promiseA)
      .mockResolvedValueOnce({
        status: "completed",
        analysis_started: false,
        cache_hit: false,
        automatic: true,
        canonical_bounds: mockBounds,
        result: resultB,
      } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Viewport A dispara
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    // Operador move para Viewport B
    map.center = { lng: -46.95, lat: -23.10 };
    act(() => map.emit("movestart"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    // Resposta de B chega primeiro
    await act(async () => {
      await Promise.resolve();
    });

    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(resultB.analysis_id);

    // Agora resposta de A (antiga/stale) chega
    const resultA = makeMockResult("nao_cortar");
    resultA.analysis_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
    await act(async () => {
      resolveRequestA({
        status: "completed",
        analysis_started: false,
        cache_hit: false,
        automatic: true,
        canonical_bounds: mockBounds,
        result: resultA,
      });
      await Promise.resolve();
    });

    // O currentResult continua sendo B! A não sobrescreve B!
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(resultB.analysis_id);
  });

  it("movimentos rápidos → debounce evita múltiplos requests", () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValue({
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      result: makeMockResult(),
    });

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Micro movimentos sucessivos
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(300));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(400));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(500));

    expect(mockRunAutomaticAnalysis).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(1000));
    expect(mockRunAutomaticAnalysis).toHaveBeenCalledTimes(1);
  });

  it("same spatial_key → não cria spam", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    mockRunAutomaticAnalysis.mockResolvedValue({
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      spatial_key: computeTileKey(-46.961, -23.108, 17),
      canonical_bounds: mockBounds,
      result: makeMockResult(),
    });

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // First trigger
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRunAutomaticAnalysis).toHaveBeenCalledTimes(1);

    // Micro deslocamento no mesmo tile
    map.center = { lng: -46.96101, lat: -23.10801 };
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    expect(mockRunAutomaticAnalysis).toHaveBeenCalledTimes(1);
  });

  it("cleanup listeners e timers ao desmontar", () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });
    const view = render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    act(() => map.emit("moveend"));
    view.unmount();

    act(() => vi.advanceTimersByTime(1500));
    expect(mockRunAutomaticAnalysis).not.toHaveBeenCalled();
    expect(map.removed).toBe(true);
  });

  it("P) badge NÃO CORTAR aparece abaixo do toggle após resultado automático", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "completed",
      result: makeMockResult("nao_cortar"),
      isCacheHit: false,
    });

    const { container } = render(<AutoAnalysisPanel />);

    // Badge de resultado presente
    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("NÃO CORTAR");
    expect(badge).toHaveClass("auto-result-badge--no-cut");

    // data-decision correto
    expect(badge).toHaveAttribute("data-decision", "nao_cortar");

    // Nenhum card grande
    expect(container.querySelector(".auto-analysis-card")).toBeNull();
    expect(container.querySelector(".map-result-popup")).toBeNull();
  });

  it("Q) badge CORTAR com classe vermelha", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "completed",
      result: makeMockResult("cortar"),
      isCacheHit: false,
    });

    const { container } = render(<AutoAnalysisPanel />);
    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("CORTAR");
    expect(badge).toHaveClass("auto-result-badge--cut");
  });

  it("R) badge INCONCLUSIVO com classe âmbar", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "completed",
      result: makeMockResult("inconclusivo"),
      isCacheHit: false,
    });

    const { container } = render(<AutoAnalysisPanel />);
    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("INCONCLUSIVO");
    expect(badge).toHaveClass("auto-result-badge--inconclusive");
  });

  it("S) decisao do Sentinel-2 tem prioridade no badge do mapa", () => {
    const fusionResult = makeMockResult("cortar"); // S2 = cortar
    fusionResult.multisource = {
      enabled: true,
      fusion_mode: "experimental",
      generated_at: "2026-08-01T00:00:00Z",
      experimental_fusion: {
        schema_version: "1.0",
        fusion_mode: "experimental",
        experimental_policy_version: "1.0",
        sentinel2_recommendation: "cortar",
        multisource_recommendation: "inconclusivo", // multisource = inconclusivo
        sentinel1_influenced_decision: true,
        fusion_rule: "B",
        fusion_reason: "sentinel1_temporal_mixed_with_sentinel2_cut",
        sentinel1_temporal_status: "mixed",
        experimental: true,
        operationally_authorized: false,
        experimental_fusion_evaluated: true,
        experimental_fusion_evaluable: true,
        fusion_not_evaluable_reason: null,
      },
    };

    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "completed",
      result: fusionResult,
      isCacheHit: false,
    });

    const { container } = render(<AutoAnalysisPanel />);
    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    // Deve mostrar prioridade Sentinel-2 (CORTAR), divergência de Sentinel-1 permanece interna
    expect(badge).toHaveTextContent("CORTAR");
    expect(badge).toHaveClass("auto-result-badge--cut");
    expect(badge).not.toHaveTextContent("INCONCLUSIVO");
  });

  it("T) badge desaparece ao desativar análise automática", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "completed",
      result: makeMockResult("nao_cortar"),
      isCacheHit: false,
    });

    const { container } = render(<AutoAnalysisPanel />);
    expect(container.querySelector(".auto-result-badge")).toBeInTheDocument();

    // Desativar análise automática
    const switchInput = screen.getByRole("switch", { name: "Ativar análise automática por navegação" });
    fireEvent.click(switchInput);
    expect(useAutoAnalysisStore.getState().enabled).toBe(false);

    // Badge some junto com o painel de status
    expect(container.querySelector(".auto-result-badge")).toBeNull();
  });

  it("U) cache_hit também exibe badge de resultado", () => {
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "cache_hit",
      result: makeMockResult("nao_cortar"),
      isCacheHit: true,
    });

    const { container } = render(<AutoAnalysisPanel />);
    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("NÃO CORTAR");
  });

  it("V) stale response não altera badge — badge reflete store atual", async () => {
    useAutoAnalysisStore.setState({ enabled: true, uiStatus: "idle" });

    let resolveRequestA: (val: unknown) => void = () => undefined;
    const promiseA = new Promise((resolve) => { resolveRequestA = resolve; });

    const resultB = makeMockResult("cortar");
    resultB.analysis_id = "b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2";

    mockRunAutomaticAnalysis
      .mockImplementationOnce(() => promiseA)
      .mockResolvedValueOnce({
        status: "completed",
        analysis_started: false,
        cache_hit: false,
        automatic: true,
        canonical_bounds: mockBounds,
        result: resultB,
      } satisfies AutomaticAnalysisResponse);

    const { container } = render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Viewport A dispara (fica pendente)
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    // Operador move → cancela A → dispara B
    map.center = { lng: -46.950, lat: -23.100 };
    act(() => map.emit("movestart"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    // Resposta B chega → badge CORTAR
    await act(async () => { await Promise.resolve(); });

    const badge = container.querySelector(".auto-result-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("CORTAR");

    // Resposta stale de A chega → NÃO deve mudar o badge para NÃO CORTAR
    const resultA = makeMockResult("nao_cortar");
    resultA.analysis_id = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1";
    await act(async () => {
      resolveRequestA({
        status: "completed",
        analysis_started: false,
        cache_hit: false,
        automatic: true,
        canonical_bounds: mockBounds,
        result: resultA,
      });
      await Promise.resolve();
    });

    // Badge ainda deve mostrar CORTAR (B), não NÃO CORTAR (A stale)
    const badgeAfter = container.querySelector(".auto-result-badge");
    expect(badgeAfter).toHaveTextContent("CORTAR");
  });
});

describe("AUTO-03D — Frontend road-aware", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    runtime.maps = [];
    runtime.draws = [];
    runtime.popups = [];
    mockRunAutomaticAnalysis.mockReset();
    useAutoAnalysisStore.setState({
      enabled: true,
      uiStatus: "idle",
      result: null,
      currentSpatialKey: null,
      canonicalBounds: null,
      road: null,
      analyzedGeometry: null,
      centerline: null,
      sideAGeometry: null,
      sideBGeometry: null,
      spatialStrategy: null,
    });
    useAnalysisStore.setState({
      geometry: null,
      geometryRevision: 0,
      activeTab: "area",
      currentResult: null,
      selectedTool: "navigate",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const mockRoadsidePolygon = {
    type: "Polygon",
    coordinates: [
      [
        [-46.95, -23.1],
        [-46.948, -23.1],
        [-46.948, -23.098],
        [-46.95, -23.098],
        [-46.95, -23.1],
      ],
    ],
  };

  const mockCenterline = {
    type: "LineString",
    coordinates: [
      [-46.95, -23.1],
      [-46.948, -23.098],
    ],
  };

  it("cache_hit real policy 1.1 substitui failed anterior e renderiza MultiPolygon roadside", async () => {
    const cachedResult = makeMockResult("nao_cortar");
    cachedResult.analysis_id = "3c6eaaa6-98a7-4b52-b8c9-bc93279ff545";
    cachedResult.multisource = {
      enabled: true,
      fusion_mode: "experimental",
      official_recommendation_changed: false,
      generated_at: "2026-09-08T17:11:00-03:00",
      experimental_fusion: {
        schema_version: "1.0",
        fusion_mode: "experimental",
        fusion_policy: "experimental_v1",
        experimental_policy_version: "1.1",
        sentinel2_recommendation: "nao_cortar",
        final_recommendation: "nao_cortar",
        multisource_recommendation: "nao_cortar",
        sentinel1_influenced_decision: false,
        fusion_rule: null,
        fusion_reason: null,
        sentinel1_temporal_status: "stable",
        experimental: true,
        operationally_authorized: false,
      },
    };
    const analyzedMultiPolygon = {
      type: "MultiPolygon",
      coordinates: [
        [[[ -46.957, -23.119 ], [ -46.956, -23.119 ], [ -46.956, -23.118 ], [ -46.957, -23.119 ]]],
        [[[ -46.955, -23.117 ], [ -46.954, -23.117 ], [ -46.954, -23.116 ], [ -46.955, -23.117 ]]],
      ],
    };
    const rawCacheHit = {
      status: "cache_hit",
      cache_hit: true,
      analysis_started: false,
      automatic: true,
      analysis_id: cachedResult.analysis_id,
      spatial_key: "roadside:v1:local_geojson:sp348:section:000170:side:both:profile",
      canonical_bounds: mockBounds,
      cache_state: "fresh",
      result: cachedResult,
      road: { id: "SP-348", ref: "SP-348", name: "Rodovia dos Bandeirantes" },
      spatial_strategy: { name: "roadside", version: "1.0" },
      centerline: mockCenterline,
      side_a_geometry: mockRoadsidePolygon,
      side_b_geometry: mockRoadsidePolygon,
      analyzed_geometry: analyzedMultiPolygon,
      roadside_metrics: { area_m2: 23994.7 },
    };

    const parsed = automaticAnalysisResponseSchema.parse(rawCacheHit);
    expect(parsed.result?.multisource?.experimental_fusion?.experimental_policy_version).toBe("1.1");
    expect(parsed.analyzed_geometry?.type).toBe("MultiPolygon");

    useAutoAnalysisStore.getState().setFailed("old_failure", mockBounds);
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("failed");
    mockRunAutomaticAnalysis.mockResolvedValueOnce(parsed);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.debounceMs);
    });

    const autoState = useAutoAnalysisStore.getState();
    expect(autoState.uiStatus).toBe("cache_hit");
    expect(autoState.reason).toBeNull();
    expect(autoState.result?.analysis_id).toBe(cachedResult.analysis_id);
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(cachedResult.analysis_id);
    expect(useAnalysisStore.getState().activeTab).toBe("result");
    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.queryByText("Falha na análise")).not.toBeInTheDocument();
    expect(screen.queryByText("Regra B")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Controle de análise automática" })).toHaveAttribute(
      "data-analysis-id",
      cachedResult.analysis_id,
    );
    expect(map.getSource("roadside-aoi-source")).toBeDefined();
    expect(map.getSource("canonical-aoi-source")).toBeUndefined();
  });

  it("mantém a AOI roadside e enquadra uma vez durante started, polling e cache_hit", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const cachedResult = makeMockResult("nao_cortar");
    cachedResult.analysis_id = "roadside-hook-regression";
    const analyzedMultiPolygon = {
      type: "MultiPolygon",
      coordinates: [
        [[[-46.957, -23.119], [-46.956, -23.119], [-46.956, -23.118], [-46.957, -23.119]]],
        [[[-46.955, -23.117], [-46.954, -23.117], [-46.954, -23.116], [-46.955, -23.117]]],
      ],
    };
    const roadsideResponse = {
      analysis_id: cachedResult.analysis_id,
      automatic: true,
      spatial_key: "roadside:v1:test:axis:section:1:side:both:profile",
      canonical_bounds: mockBounds,
      spatial_strategy: "roadside",
      road: { id: "SP-348", ref: "SP-348", name: "Rodovia dos Bandeirantes" },
      centerline: mockCenterline,
      side_a_geometry: mockRoadsidePolygon,
      side_b_geometry: mockRoadsidePolygon,
      analyzed_geometry: analyzedMultiPolygon,
    };

    useAutoAnalysisStore.setState({
      uiStatus: "analyzing",
      canonicalBounds: mockBounds,
      spatialStrategy: null,
      analyzedGeometry: null,
    });
    mockRunAutomaticAnalysis
      .mockResolvedValueOnce({
        ...roadsideResponse,
        status: "analysis_started",
        analysis_started: true,
        cache_hit: false,
      } satisfies AutomaticAnalysisResponse)
      .mockResolvedValueOnce({
        ...roadsideResponse,
        status: "in_progress",
        analysis_started: false,
        cache_hit: false,
      } satisfies AutomaticAnalysisResponse)
      .mockResolvedValueOnce({
        ...roadsideResponse,
        status: "cache_hit",
        analysis_started: false,
        cache_hit: true,
        result: cachedResult,
      } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.debounceMs);
    });
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("analyzing");
    const roadsideSource = map.getSource("roadside-aoi-source");
    expect(roadsideSource).toBeDefined();
    expect(map.getSource("canonical-aoi-source")).toBeUndefined();
    expect(map.fitBounds).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.pollingIntervalMs);
    });
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("analyzing");
    expect(map.getSource("roadside-aoi-source")).toBe(roadsideSource);
    expect(map.getSource("canonical-aoi-source")).toBeUndefined();
    expect(map.fitBounds).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_ANALYSIS_CONFIG.pollingIntervalMs);
    });

    const hookErrors = consoleError.mock.calls.filter(([message]) =>
      String(message).includes("changed size between renders"),
    );
    expect(hookErrors).toEqual([]);
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("cache_hit");
    expect(useAutoAnalysisStore.getState().reason).toBeNull();
    expect(useAnalysisStore.getState().currentResult?.response.analysis_id).toBe(cachedResult.analysis_id);
    expect(useAnalysisStore.getState().activeTab).toBe("result");
    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.queryByText("Falha na análise")).not.toBeInTheDocument();
    expect(map.getSource("roadside-aoi-source")).toBe(roadsideSource);
    expect(map.getSource("canonical-aoi-source")).toBeUndefined();
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    expect(map.getLayer("roadside-aoi-fill")?.paint?.["fill-color"]).toBe("#27865b");
  });

  it("1) resposta roadside_v1 desenha analyzed_geometry e centerline sem quadrado z17", async () => {
    const roadsideResult = makeMockResult("nao_cortar");
    roadsideResult.analysis_id = "roadside-test-1111";

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      spatial_strategy: "roadside",
      road: { id: "sp-330", ref: "SP-330", name: "Rodovia Anhanguera" },
      analyzed_geometry: mockRoadsidePolygon,
      centerline: mockCenterline,
      result: roadsideResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    // Camada roadside foi instalada
    expect(map.getSource("roadside-aoi-source")).toBeDefined();
    expect(map.getLayer("roadside-aoi-fill")).toBeDefined();
    expect(map.getLayer("roadside-aoi-outline")).toBeDefined();
    expect(map.getSource("roadside-centerline-source")).toBeDefined();
    expect(map.getLayer("roadside-centerline-line")).toBeDefined();

    // Quadrado z17 (canonical) NÃO deve estar presente
    expect(map.getSource("canonical-aoi-source")).toBeUndefined();

    // Store foi atualizado com metadados da rodovia
    const storeState = useAutoAnalysisStore.getState();
    expect(storeState.road?.ref).toBe("SP-330");
    expect(storeState.road?.name).toBe("Rodovia Anhanguera");
    expect(storeState.spatialStrategy).toBe("roadside");
  });

  it("2) roadside em andamento exibe feedback visual analyzing", async () => {
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "analysis_started",
      spatial_strategy: "roadside",
      road: { id: "sp-330", ref: "SP-330", name: "Rodovia Anhanguera" },
      analyzed_geometry: mockRoadsidePolygon,
      centerline: mockCenterline,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(map.getSource("roadside-aoi-source")).toBeDefined();
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("analyzing");
    expect(screen.getByText("Analisando...")).toBeInTheDocument();
  });

  it("3) exibe mensagens informativas não-bloqueantes para road_context_required, road_not_found e road_ambiguous", async () => {
    // A) road_context_required
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "road_context_required",
      reason: "zoom_too_far_for_road_detection",
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(useAutoAnalysisStore.getState().uiStatus).toBe("road_context_required");
    expect(screen.getByText("Aproxime um pouco para identificar o trecho")).toBeInTheDocument();
    expect(map.getSource("roadside-aoi-source")).toBeUndefined();

    // B) road_not_found
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "road_not_found",
      reason: "no_managed_road_in_area",
    } satisfies AutomaticAnalysisResponse);

    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(useAutoAnalysisStore.getState().uiStatus).toBe("road_not_found");
    expect(screen.getByText("Nenhuma rodovia monitorada identificada neste ponto")).toBeInTheDocument();

    // C) road_ambiguous
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "road_ambiguous",
      reason: "multiple_candidates_equidistant",
    } satisfies AutomaticAnalysisResponse);

    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(useAutoAnalysisStore.getState().uiStatus).toBe("road_ambiguous");
    expect(screen.getByText("Não foi possível identificar o trecho com segurança")).toBeInTheDocument();
  });

  it("4) resultado roadside abre aba Resultado imediatamente e exibe nome da rodovia sem chaves tecnicas", async () => {
    const roadsideResult = makeMockResult("nao_cortar");
    roadsideResult.analysis_id = "roadside-completed-auto";

    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      spatial_strategy: "roadside",
      road: { id: "sp-330", ref: "SP-330", name: "Rodovia Anhanguera" },
      analyzed_geometry: mockRoadsidePolygon,
      result: roadsideResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    // Aba de resultado abre imediatamente sem clique extra
    expect(useAnalysisStore.getState().activeTab).toBe("result");

    const queryClient = new QueryClient();
    const activeResult = useAnalysisStore.getState().currentResult?.response;
    expect(activeResult).toBeDefined();

    render(
      <QueryClientProvider client={queryClient}>
        <AnalysisResultSidebar result={activeResult} onRetry={() => undefined} />
      </QueryClientProvider>
    );

    // Exibe identificação amigável da rodovia
    expect(screen.getByText("SP-330")).toBeInTheDocument();
    expect(screen.getByText("Rodovia Anhanguera")).toBeInTheDocument();
    expect(screen.getByText("Trecho analisado")).toBeInTheDocument();

    // PROIBIDO exibir chaves técnicas na UI operacional
    expect(screen.queryByText(/axis_id/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/section_id/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/spatial_key/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Regra B")).not.toBeInTheDocument();
    expect(screen.queryByText("Sentinel-1 influenciou esta análise")).not.toBeInTheDocument();
  });

  it("5) movimentação para fora de rodovia limpa geometria anterior (sem fantasmas)", async () => {
    const roadsideResult = makeMockResult("nao_cortar");
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      spatial_strategy: "roadside",
      road: { id: "sp-330", ref: "SP-330", name: "Rodovia Anhanguera" },
      analyzed_geometry: mockRoadsidePolygon,
      result: roadsideResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    expect(map.getSource("roadside-aoi-source")).toBeDefined();

    // Operador move para ponto sem rodovia
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "road_not_found",
      reason: "no_road",
    } satisfies AutomaticAnalysisResponse);

    act(() => map.emit("movestart"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    // Geometria roadside foi removida (sem fantasmas)
    expect(map.getSource("roadside-aoi-source")).toBeUndefined();
    expect(useAutoAnalysisStore.getState().analyzedGeometry).toBeNull();
  });

  it("6) retrocompatibilidade com tile_v1: canonical_bounds continua funcionando", async () => {
    const tileResult = makeMockResult("cortar");
    mockRunAutomaticAnalysis.mockResolvedValueOnce({
      status: "completed",
      cache_hit: false,
      analysis_started: false,
      canonical_bounds: mockBounds,
      result: tileResult,
    } satisfies AutomaticAnalysisResponse);

    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => map.emit("moveend"));
    act(() => vi.advanceTimersByTime(1000));

    await act(async () => {
      await Promise.resolve();
    });

    // Tile v1 abre resultado normalmente
    expect(useAnalysisStore.getState().activeTab).toBe("result");
    expect(useAutoAnalysisStore.getState().uiStatus).toBe("completed");
  });
});
