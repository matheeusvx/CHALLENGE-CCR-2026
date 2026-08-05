import { describe, expect, it, vi } from "vitest";
import { AOI_LAYER_IDS, createAoiFeatureCollection, installOrUpdateAoiLayer } from "@/components/map/layers/aoi-layer";
import { getAoiVisualState } from "@/lib/map/aoi-visual-state";
import type { PolygonGeometry } from "@/lib/map/geometry";

const polygon: PolygonGeometry = { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] };

class LayerMapMock {
  sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
  layers = new Map<string, { id: string; paint?: Record<string, unknown> }>();
  addSource(id: string, value: { data: unknown }) { this.sources.set(id, { data: value.data, setData: vi.fn() }); }
  getSource(id: string) { return this.sources.get(id); }
  removeSource(id: string) { this.sources.delete(id); }
  addLayer(layer: { id: string; paint?: Record<string, unknown> }) { this.layers.set(layer.id, layer); }
  getLayer(id: string) { return this.layers.get(id); }
  removeLayer(id: string) { this.layers.delete(id); }
  setPaintProperty(id: string, key: string, value: unknown) { const layer = this.layers.get(id); if (layer) layer.paint = { ...layer.paint, [key]: value }; }
}

describe("camada operacional da AOI", () => {
  it("cria fill, halo, outline, vertices e label a partir da geometria canonica", () => {
    const map = new LayerMapMock();
    installOrUpdateAoiLayer(map as never, polygon, getAoiVisualState({ editing: false, dirty: true, validation: null }));
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect([...map.layers.keys()]).toEqual(expect.arrayContaining([AOI_LAYER_IDS.fill, AOI_LAYER_IDS.halo, AOI_LAYER_IDS.outline, AOI_LAYER_IDS.vertices, AOI_LAYER_IDS.label]));
    expect(createAoiFeatureCollection(polygon, "Area").features.filter((feature) => feature.properties.feature_role === "vertex")).toHaveLength(4);
  });

  it("atualiza a fonte e o estilo sem duplicar camadas", () => {
    const map = new LayerMapMock();
    installOrUpdateAoiLayer(map as never, polygon, getAoiVisualState({ editing: false, dirty: true, validation: null }));
    const source = map.getSource(AOI_LAYER_IDS.source)!;
    installOrUpdateAoiLayer(map as never, polygon, getAoiVisualState({ editing: false, dirty: false, validation: "valid", recommendation: "nao_cortar" }));
    expect(source.setData).toHaveBeenCalledOnce();
    expect(map.layers).toHaveLength(5);
    expect(map.layers.get(AOI_LAYER_IDS.outline)?.paint?.["line-color"]).toBe("#27865b");
  });

  it("remove a AOI ao limpar e reinstala depois de uma troca de estilo", () => {
    const map = new LayerMapMock();
    const state = getAoiVisualState({ editing: false, dirty: false, validation: "valid" });
    installOrUpdateAoiLayer(map as never, polygon, state);
    installOrUpdateAoiLayer(map as never, null, state);
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(false);
    expect(map.layers).toHaveLength(0);
    installOrUpdateAoiLayer(map as never, polygon, state);
    expect(map.sources.has(AOI_LAYER_IDS.source)).toBe(true);
    expect(map.layers).toHaveLength(5);
  });
});
