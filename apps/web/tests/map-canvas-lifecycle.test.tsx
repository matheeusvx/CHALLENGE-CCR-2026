import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PolygonGeometry } from "@/lib/map/geometry";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, MAP_STYLES } from "@/lib/map/config";
import { useAnalysisStore } from "@/stores/analysis-store";

type MapMockShape = {
  options: Record<string, unknown>;
  handlers: Map<string, Set<(...args: never[]) => void>>;
  sources: Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>;
  layers: Map<string, { id: string; paint?: Record<string, unknown> }>;
  fitBounds: ReturnType<typeof vi.fn>;
  setStyle: ReturnType<typeof vi.fn>;
  removed: boolean;
  emit: (event: string) => void;
};
type DrawMockShape = {
  handlers: Map<string, Set<() => void>>;
  snapshot: Array<{ id?: string; geometry: PolygonGeometry }>;
  mode: string;
  stopped: boolean;
  emit: (event: string) => void;
};

const runtime = vi.hoisted(() => ({ maps: [] as MapMockShape[], draws: [] as DrawMockShape[] }));

vi.mock("maplibre-gl", () => {
  class MapMock {
    options: Record<string, unknown>;
    handlers = new Map<string, Set<(...args: never[]) => void>>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
    layers = new Map<string, { id: string; paint?: Record<string, unknown> }>();
    fitBounds = vi.fn();
    removed = false;
    loaded = false;
    canvas = document.createElement("canvas");
    setStyle = vi.fn((style: string) => { this.options.style = style; this.sources.clear(); this.layers.clear(); this.loaded = false; });
    constructor(options: Record<string, unknown>) { this.options = options; runtime.maps.push(this); }
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
    emit(event: string) { if (event === "style.load") this.loaded = true; this.handlers.get(event)?.forEach((handler) => handler()); }
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
    isStyleLoaded() { return this.loaded; }
    getCenter() { const [lng, lat] = this.options.center as [number, number]; return { lng, lat }; }
    getZoom() { return this.options.zoom as number; }
    getBearing() { return this.options.bearing as number; }
    getPitch() { return this.options.pitch as number; }
    remove() { this.removed = true; this.handlers.clear(); }
  }
  return {
    Map: MapMock,
    NavigationControl: class {},
    Popup: class {
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
    TerraDrawPolygonMode: class {},
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
    expect(map.options).toMatchObject({ style: MAP_STYLES.operational.styleUrl, center: [-46.955, -23.121], zoom: 12, minZoom: 5, maxZoom: 19 });
    expect(map.handlers.get("style.load")?.size).toBe(1);
    view.unmount();
    expect(map.removed).toBe(true);
    expect(map.handlers.size).toBe(0);
  });

  it("instala a AOI persistente e a enquadra ao carregar uma geometria existente", () => {
    useAnalysisStore.getState().setGeometry(polygon, "pasted");
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.fill)).toBe(true);
    expect(map.layers.has(AOI_LAYER_IDS.outline)).toBe(true);
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
    expect(screen.getByText("Aguardando validacao")).toBeInTheDocument();
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

  it("troca a base sem perder geometria e reinstala os overlays no style.load", () => {
    useAnalysisStore.getState().setGeometry(polygon, "pasted");
    render(<MapCanvas />);
    const map = runtime.maps[0];
    act(() => map.emit("style.load"));
    const firstDraw = runtime.draws.at(-1)!;
    fireEvent.click(screen.getByRole("button", { name: "Terreno" }));
    expect(map.setStyle).toHaveBeenCalledWith(MAP_STYLES.terrain.styleUrl);
    expect(firstDraw.stopped).toBe(true);
    expect(useAnalysisStore.getState().geometry).toEqual(polygon);
    act(() => map.emit("style.load"));
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(runtime.draws.at(-1)?.snapshot[0].geometry).toEqual(polygon);
    expect(map.handlers.get("style.load")?.size).toBe(1);
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

  it("nao conserva listeners de uma montagem descartada pelo Strict Mode", () => {
    const view = render(<StrictMode><MapCanvas /></StrictMode>);
    expect(runtime.maps).toHaveLength(2);
    expect(runtime.maps[0].removed).toBe(true);
    expect(runtime.maps[0].handlers.size).toBe(0);
    expect(runtime.maps[1].handlers.get("style.load")?.size).toBe(1);
    view.unmount();
    expect(runtime.maps[1].handlers.size).toBe(0);
  });
});
