import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PolygonGeometry } from "@/lib/map/geometry";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, OPERATIONAL_RASTER_STYLE } from "@/lib/map/config";
import { useAnalysisStore } from "@/stores/analysis-store";

type MapMockShape = {
  options: Record<string, unknown>;
  handlers: Map<string, Set<(...args: never[]) => void>>;
  sources: Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>;
  layers: Map<string, { id: string; paint?: Record<string, unknown> }>;
  fitBounds: ReturnType<typeof vi.fn>;
  setStyle: ReturnType<typeof vi.fn>;
  moveLayer: ReturnType<typeof vi.fn>;
  loaded: boolean;
  removed: boolean;
  emit: (event: string, payload?: unknown) => void;
};
type DrawMockShape = {
  handlers: Map<string, Set<() => void>>;
  snapshot: Array<{ id?: string; geometry: PolygonGeometry }>;
  mode: string;
  stopped: boolean;
  emit: (event: string) => void;
};
type PopupMockShape = { options: Record<string, unknown>; content?: HTMLElement; removed: boolean };

const runtime = vi.hoisted(() => ({
  maps: [] as MapMockShape[],
  draws: [] as DrawMockShape[],
  popups: [] as PopupMockShape[],
  polygonModeOptions: [] as Array<Record<string, unknown>>,
  workerUrls: [] as string[],
}));

vi.mock("maplibre-gl", () => {
  class MapMock {
    options: Record<string, unknown>;
    handlers = new Map<string, Set<(...args: never[]) => void>>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
    layers = new Map<string, { id: string; paint?: Record<string, unknown> }>();
    fitBounds = vi.fn();
    moveLayer = vi.fn((id: string) => { const layer = this.layers.get(id); if (layer) { this.layers.delete(id); this.layers.set(id, layer); } });
    removed = false;
    loaded = false;
    canvas = document.createElement("canvas");
    setStyle = vi.fn((style: unknown) => { this.options.style = style; this.installBaseStyle(style); this.loaded = false; });
    constructor(options: Record<string, unknown>) { this.options = options; this.installBaseStyle(options.style); runtime.maps.push(this); }
    installBaseStyle(style: unknown) {
      this.sources.clear();
      this.layers.clear();
      const specification = style as { sources?: Record<string, unknown>; layers?: Array<{ id: string; paint?: Record<string, unknown> }> };
      Object.entries(specification.sources ?? {}).forEach(([id, source]) => this.sources.set(id, { data: source, setData: vi.fn() }));
      specification.layers?.forEach((layer) => this.layers.set(layer.id, layer));
    }
    key(event: string, layer?: string) { return layer ? `${event}:${layer}` : event; }
    on(event: string, layerOrHandler: string | ((...args: never[]) => void), handler?: (...args: never[]) => void) {
      const key = this.key(event, typeof layerOrHandler === "string" ? layerOrHandler : undefined);
      const listener = typeof layerOrHandler === "function" ? layerOrHandler : handler!;
      const listeners = this.handlers.get(key) ?? new Set(); listeners.add(listener); this.handlers.set(key, listeners);
    }
    off(event: string, layerOrHandler: string | ((...args: never[]) => void), handler?: (...args: never[]) => void) {
      const key = this.key(event, typeof layerOrHandler === "string" ? layerOrHandler : undefined);
      const listener = typeof layerOrHandler === "function" ? layerOrHandler : handler!;
      this.handlers.get(key)?.delete(listener);
    }
    emit(event: string, payload?: unknown) { if (event === "style.load" || event === "load") this.loaded = true; this.handlers.get(event)?.forEach((handler) => handler(payload as never)); }
    addControl() {}
    addSource(id: string, source: { data: unknown }) { this.sources.set(id, { data: source.data, setData: vi.fn() }); }
    getSource(id: string) { return this.sources.get(id); }
    removeSource(id: string) { this.sources.delete(id); }
    addLayer(layer: { id: string; paint?: Record<string, unknown> }) { this.layers.set(layer.id, layer); }
    getLayer(id: string) { return this.layers.get(id); }
    removeLayer(id: string) { this.layers.delete(id); }
    setPaintProperty(id: string, key: string, value: unknown) { const layer = this.layers.get(id); if (layer) layer.paint = { ...layer.paint, [key]: value }; }
    setFeatureState() {}
    getCanvas() { return this.canvas; }
    getStyle() { return this.options.style as { glyphs?: string }; }
    isStyleLoaded() { return this.loaded; }
    getCenter() { const [lng, lat] = this.options.center as [number, number]; return { lng, lat }; }
    getZoom() { return this.options.zoom as number; }
    getBearing() { return this.options.bearing as number; }
    getPitch() { return this.options.pitch as number; }
    remove() { this.removed = true; this.handlers.clear(); }
  }
  return {
    Map: MapMock,
    setWorkerUrl: (url: string) => runtime.workerUrls.push(url),
    NavigationControl: class {},
    Popup: class {
      options: Record<string, unknown>;
      content?: HTMLElement;
      removed = false;
      constructor(options: Record<string, unknown>) { this.options = options; runtime.popups.push(this); }
      setLngLat() { return this; }
      setDOMContent(content: HTMLElement) { this.content = content; return this; }
      addTo() { return this; }
      remove() { this.removed = true; return this; }
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
    on(event: string, handler: () => void) { const handlers = this.handlers.get(event) ?? new Set(); handlers.add(handler); this.handlers.set(event, handlers); }
    off(event: string, handler: () => void) { this.handlers.get(event)?.delete(handler); }
    emit(event: string) { this.handlers.get(event)?.forEach((handler) => handler()); }
    start() {}
    stop() { this.stopped = true; this.handlers.clear(); }
    setMode(mode: string) { this.mode = mode; }
    getMode() { return this.mode; }
    getSnapshot() { return this.snapshot; }
    clear() { this.snapshot = []; }
    addFeatures(features: Array<{ id?: string; geometry: PolygonGeometry }>) { this.snapshot = features; }
    removeFeatures(ids: string[]) { this.snapshot = this.snapshot.filter((feature) => !feature.id || !ids.includes(feature.id)); }
    clearUndoRedoHistory() {}
    canUndo() { return false; }
    canRedo() { return false; }
    undo() {}
    redo() {}
  }
  return {
    TerraDraw: DrawMock,
    TerraDrawPolygonMode: class { constructor(options: Record<string, unknown>) { runtime.polygonModeOptions.push(options); } },
    TerraDrawRenderMode: class {},
    TerraDrawSelectMode: class {},
    TerraDrawSessionUndoRedo: class {},
  };
});
vi.mock("terra-draw-maplibre-gl-adapter", () => ({ TerraDrawMapLibreGLAdapter: class {} }));

import { AOI_LAYER_IDS } from "@/components/map/layers/aoi-layer";
import { MapCanvas } from "@/components/map/map-canvas";

const polygon: PolygonGeometry = { type: "Polygon", coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]] };

beforeEach(() => {
  runtime.maps.length = 0;
  runtime.draws.length = 0;
  runtime.popups.length = 0;
  runtime.polygonModeOptions.length = 0;
  runtime.workerUrls.length = 0;
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
});

describe("lifecycle operacional do mapa", () => {
  it("cria uma unica camera em Louveira com o estilo operacional", () => {
    const view = render(<MapCanvas />);
    const map = runtime.maps[0];
    expect(map.options).toMatchObject({ center: [-46.955, -23.121], zoom: 12, minZoom: 5, maxZoom: 19 });
    expect(map.options.style).toBe(OPERATIONAL_RASTER_STYLE);
    expect(runtime.workerUrls).toEqual([MAP_CONFIG.workerUrl]);
    expect(map.handlers.get("load")?.size).toBe(1);
    expect(map.handlers.get("style.load")?.size).toBe(1);
    expect(map.handlers.get("error")?.size).toBe(1);
    act(() => map.emit("load"));
    expect(map.fitBounds).not.toHaveBeenCalled();
    view.unmount();
    expect(map.removed).toBe(true);
    expect(map.handlers.size).toBe(0);
  });

  it("instala a AOI persistente e a enquadra ao carregar uma geometria existente", () => {
    useAnalysisStore.getState().setGeometry(polygon, "pasted");
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.fill)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.outline)).toBe(true);
    expect([...map.layers.keys()].indexOf(AOI_LAYER_IDS.fill)).toBeGreaterThan([...map.layers.keys()].indexOf("openstreetmap-base"));
    expect(map.fitBounds).toHaveBeenCalledOnce();
  });

  it("enquadra apos concluir o desenho e marca a area como pendente", () => {
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    const draw = runtime.draws.at(-1)!;
    draw.snapshot = [{ id: "new", geometry: polygon }];
    draw.mode = "polygon";
    act(() => draw.emit("finish"));
    expect(map.fitBounds).toHaveBeenCalledOnce();
    expect(useAnalysisStore.getState()).toMatchObject({ geometry: polygon, isGeometryDirty: true, selectedTool: "navigate" });
    expect(runtime.polygonModeOptions[0]).toMatchObject({
      showCoordinatePoints: true,
      styles: { outlineWidth: 6, coordinatePointWidth: 10, closingPointWidth: 11 },
    });
  });

  it("enquadra apos aplicar GeoJSON e pelo controle explicito", () => {
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    act(() => {
      useAnalysisStore.getState().setGeometry(polygon, "pasted");
      useAnalysisStore.getState().requestGeometryFit();
    });
    expect(map.fitBounds).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Enquadrar geometria" }));
    expect(map.fitBounds).toHaveBeenCalledTimes(2);
  });

  it("nao recria o mapa quando a geometria muda", () => {
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => useAnalysisStore.getState().setGeometry(polygon, "pasted"));
    expect(runtime.maps).toHaveLength(1);
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
  });

  it("sincroniza a AOI mesmo enquanto tiles raster ainda estao carregando", () => {
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    map.loaded = false;
    act(() => useAnalysisStore.getState().setGeometry(polygon, "drawn"));
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.fill)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.outline)).toBe(true);
  });

  it("exibe falha em qualquer erro do mapa e permite tentar novamente", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("error", { error: new Error("tile indisponivel") }));
    expect(screen.getByRole("alert")).toHaveTextContent("Não foi possível carregar a base cartográfica.");
    expect(consoleError).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(map.setStyle).toHaveBeenCalledWith(OPERATIONAL_RASTER_STYLE);
    expect(screen.getByRole("status")).toHaveTextContent("Carregando base cartográfica...");
    act(() => map.emit("style.load"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("atualiza a AOI apos editar e a remove ao limpar", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    const draw = runtime.draws.at(-1)!;
    const edited: PolygonGeometry = { ...polygon, coordinates: [[[-46.963, -23.109], ...polygon.coordinates[0].slice(1)]] };
    draw.snapshot = [{ id: "active-aoi", geometry: edited }];
    draw.mode = "select";
    act(() => draw.emit("change"));
    expect(useAnalysisStore.getState().geometry).toEqual(edited);
    act(() => useAnalysisStore.getState().clearGeometry());
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(false);
  });

  it("mantem a AOI entre tabs, nao renderiza card grande no poligono e exibe pill discreta", () => {
    const state = useAnalysisStore.getState();
    state.setGeometry(polygon, "drawn");
    const revision = useAnalysisStore.getState().geometryRevision;
    state.applyGeometryValidation({
      valid: true,
      geometry_type: "Polygon",
      area_square_meters: 4500,
      centroid: { longitude: -46.961, latitude: -23.108 },
      bounding_box: [-46.962, -23.109, -46.96, -23.107],
      estimated_sentinel_pixels: 45,
      warnings: [],
    }, revision);
    const result = {
      analysis_id: "6d7ba572-321d-4a27-9f0f-9fcbd5ecab62",
      status: "completed",
      analysis_period: { start_date: "2026-07-11", end_date: "2026-08-11", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
      recommendation: { decision: "nao_cortar", confidence: "high", experimental: true, summary: "Sem intervenção indicada.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
      aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
    } satisfies AnalysisResponse;

    const { container } = render(<MapCanvas result={result} />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));
    act(() => useAnalysisStore.getState().setField("activeTab", "result"));
    act(() => useAnalysisStore.getState().setField("activeTab", "area"));

    // Polígono continua visível no mapa com a cor semântica do resultado
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(map.layers.get(AOI_LAYER_IDS.outline)?.paint?.["line-color"]).toBe("#27865b");

    // SEM card grande no polígono / sem popup MapLibre permanente
    expect(runtime.popups).toHaveLength(0);
    expect(container.querySelector(".map-result-popup")).toBeNull();

    // SEM badge redundante "Área selecionada · ..."
    expect(screen.queryByText(/Área selecionada ·/)).not.toBeInTheDocument();
    expect(container.querySelector(".map-aoi-floating-label")).toBeNull();

    // Pill discreta no canto superior direito abaixo do controle automático
    const manualPill = screen.getByTestId("manual-result-pill");
    expect(manualPill).toBeInTheDocument();
    expect(manualPill).toHaveTextContent("Manual · NÃO CORTAR");
    expect(manualPill).toHaveClass("manual-result-pill--no-cut");
    expect(manualPill).toHaveAttribute("data-decision", "nao_cortar");
  });

  it("analise manual com recomendacao CORTAR exibe pill vermelha e sem card grande", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    const result = {
      analysis_id: "c047a001-321d-4a27-9f0f-9fcbd5ecab62",
      status: "completed",
      analysis_period: { start_date: "2026-07-11", end_date: "2026-08-11", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
      recommendation: { decision: "cortar", confidence: "medium", experimental: true, summary: "Intervenção indicada.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
      aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
    } satisfies AnalysisResponse;

    const { container } = render(<MapCanvas result={result} />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Sem card grande
    expect(runtime.popups).toHaveLength(0);
    expect(container.querySelector(".map-result-popup")).toBeNull();
    expect(screen.queryByText(/Área selecionada ·/)).not.toBeInTheDocument();

    // Pill discreta vermelha
    const manualPill = screen.getByTestId("manual-result-pill");
    expect(manualPill).toHaveTextContent("Manual · CORTAR");
    expect(manualPill).toHaveClass("manual-result-pill--cut");
  });

  it("analise manual com recomendacao INCONCLUSIVO exibe pill ambar e sem card grande", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    const result = {
      analysis_id: "inc-321d-4a27-9f0f-9fcbd5ecab62",
      status: "completed",
      analysis_period: { start_date: "2026-07-11", end_date: "2026-08-11", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
      recommendation: { decision: "inconclusivo", confidence: "low", experimental: true, summary: "Inconclusivo.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
      aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
    } satisfies AnalysisResponse;

    const { container } = render(<MapCanvas result={result} />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    expect(runtime.popups).toHaveLength(0);
    expect(container.querySelector(".map-result-popup")).toBeNull();

    const manualPill = screen.getByTestId("manual-result-pill");
    expect(manualPill).toHaveTextContent("Manual · INCONCLUSIVO");
    expect(manualPill).toHaveClass("manual-result-pill--inconclusive");
  });

  it("analise manual com fusao multissensor prioriza decisao do Sentinel-2 na pill", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    const fusionResult = {
      analysis_id: "fus-321d-4a27-9f0f-9fcbd5ecab62",
      status: "completed",
      analysis_period: { start_date: "2026-07-11", end_date: "2026-08-11", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
      recommendation: { decision: "cortar", confidence: "high", experimental: true, summary: "S2 cortar.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
      multisource: {
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
          fusion_reason: "temporal_mix",
          sentinel1_temporal_status: "mixed",
          experimental: true,
          operationally_authorized: false,
          experimental_fusion_evaluated: true,
          experimental_fusion_evaluable: true,
          fusion_not_evaluable_reason: null,
        },
      },
      aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
    } satisfies AnalysisResponse;

    render(<MapCanvas result={fusionResult} />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // Pill deve refletir prioridade Sentinel-2 (CORTAR), divergência de Sentinel-1 permanece interna
    const manualPill = screen.getByTestId("manual-result-pill");
    expect(manualPill).toHaveTextContent("Manual · CORTAR");
    expect(manualPill).toHaveClass("manual-result-pill--cut");
    expect(manualPill).not.toHaveTextContent("INCONCLUSIVO");
  });

  it("analise automatica nao exibe a pill de analise manual", () => {
    useAnalysisStore.getState().setGeometry(polygon, "drawn");
    const autoResult = {
      analysis_id: "auto-321d-4a27-9f0f-9fcbd5ecab62",
      status: "completed",
      analysis_trigger: "automatic_viewport",
      analysis_period: { start_date: "2026-07-11", end_date: "2026-08-11", timezone: "America/Sao_Paulo", strategy: "previous_calendar_month" },
      recommendation: { decision: "nao_cortar", confidence: "high", experimental: true, summary: "Auto.", reasons: [], blocking_reasons: [], limitations: [], metrics: {} },
      aoi: {}, summary: {}, timeseries: [], scenes: [], artifacts: {}, warnings: [], errors: [],
    } satisfies AnalysisResponse;

    render(<MapCanvas result={autoResult} />);
    const map = runtime.maps[0];
    act(() => map.emit("load"));

    // A pill "Manual · ..." não deve aparecer para análise automática
    expect(screen.queryByTestId("manual-result-pill")).not.toBeInTheDocument();
  });

  it("nao conserva listeners de uma montagem descartada pelo Strict Mode", () => {
    const view = render(<StrictMode><MapCanvas /></StrictMode>);
    expect(runtime.maps).toHaveLength(2);
    expect(runtime.maps[0].removed).toBe(true);
    expect(runtime.maps[0].handlers.size).toBe(0);
    expect(runtime.maps.filter((map) => !map.removed)).toHaveLength(1);
    expect(runtime.maps[1].handlers.get("load")?.size).toBe(1);
    expect(runtime.maps[1].handlers.get("style.load")?.size).toBe(1);
    view.unmount();
    expect(runtime.maps[1].handlers.size).toBe(0);
  });
});
