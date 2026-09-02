import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  installOrUpdateZonesLayer,
  removeZonesLayer,
  installZoneClickInteraction,
  ZONES_LAYER_IDS,
} from "@/components/map/layers/spatial-zones-layer";
import { installOrUpdateAoiLayer, AOI_LAYER_IDS } from "@/components/map/layers/aoi-layer";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import { RecommendationPanel } from "@/components/analysis/recommendation-panel";
import {
  spatialZoneSchema,
  spatialSegmentationSchema,
  type AnalysisResponse,
  type SpatialZone,
} from "@/lib/schemas/analyses";
import { calculateZoneAreaStats } from "@/lib/utils/recommendation";
import { getAoiVisualState } from "@/lib/map/aoi-visual-state";
import type { PolygonGeometry } from "@/lib/map/geometry";

// ---------------------------------------------------------------------------
// Mock MapLibre Map for layer testing
// ---------------------------------------------------------------------------

class LayerMapMock {
  sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
  layers = new Map<string, { id: string; type?: string; paint?: Record<string, unknown> }>();
  handlers = new Map<string, Array<{ layerId?: string; fn: (e: unknown) => void }>>();
  featureStates = new Map<string, Record<string, unknown>>();
  canvas = { style: { cursor: "" } };

  addSource(id: string, value: { data: unknown }) {
    this.sources.set(id, { data: value.data, setData: vi.fn() });
  }
  getSource(id: string) {
    return this.sources.get(id);
  }
  removeSource(id: string) {
    this.sources.delete(id);
  }
  addLayer(layer: { id: string; type?: string; paint?: Record<string, unknown> }) {
    this.layers.set(layer.id, layer);
  }
  getLayer(id: string) {
    return this.layers.get(id);
  }
  removeLayer(id: string) {
    this.layers.delete(id);
  }
  moveLayer(id: string) {
    const layer = this.layers.get(id);
    if (layer) {
      this.layers.delete(id);
      this.layers.set(id, layer);
    }
  }
  setPaintProperty(id: string, key: string, value: unknown) {
    const layer = this.layers.get(id);
    if (layer) {
      layer.paint = { ...layer.paint, [key]: value };
    }
  }
  setFeatureState(target: { source: string; id: string | number }, state: Record<string, unknown>) {
    const key = `${target.source}:${target.id}`;
    this.featureStates.set(key, { ...this.featureStates.get(key), ...state });
  }
  getFeatureState(target: { source: string; id: string | number }) {
    return this.featureStates.get(`${target.source}:${target.id}`) ?? {};
  }
  on(event: string, layerOrFn: string | ((e: unknown) => void), maybeFn?: (e: unknown) => void) {
    const layerId = typeof layerOrFn === "string" ? layerOrFn : undefined;
    const fn = typeof layerOrFn === "function" ? layerOrFn : maybeFn!;
    const list = this.handlers.get(event) ?? [];
    list.push({ layerId, fn });
    this.handlers.set(event, list);
  }
  off(event: string, layerOrFn: string | ((e: unknown) => void), maybeFn?: (e: unknown) => void) {
    const layerId = typeof layerOrFn === "string" ? layerOrFn : undefined;
    const fn = typeof layerOrFn === "function" ? layerOrFn : maybeFn!;
    const list = this.handlers.get(event) ?? [];
    this.handlers.set(
      event,
      list.filter((h) => h.layerId !== layerId || h.fn !== fn),
    );
  }
  getCanvas() {
    return this.canvas;
  }
  getStyle() {
    return {};
  }
  emit(event: string, e: unknown) {
    const list = this.handlers.get(event) ?? [];
    list.forEach((h) => h.fn(e));
  }
}

// ---------------------------------------------------------------------------
// Test Data
// ---------------------------------------------------------------------------

const samplePolygon: PolygonGeometry = {
  type: "Polygon",
  coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
};

const sampleZone1: SpatialZone = {
  zone_id: "zone-1",
  recommendation: "cortar",
  geometry: {
    type: "Polygon",
    coordinates: [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]],
  },
  area_m2: 5000.0,
  confidence: "high",
  analysis_quality: "high",
  reasons: ["current_percentile_at_or_above_high_threshold"],
  start_distance_m: 0,
  end_distance_m: 50,
  road_ref: "SP-330",
};

const sampleZone2: SpatialZone = {
  zone_id: "zone-2",
  recommendation: "nao_cortar",
  geometry: {
    type: "Polygon",
    coordinates: [[[0.01, 0], [0.02, 0], [0.02, 0.01], [0.01, 0.01], [0.01, 0]]],
  },
  area_m2: 3000.0,
  confidence: "medium",
  analysis_quality: "medium",
  reasons: ["current_percentile_below_or_equal_50"],
  start_distance_m: 50,
  end_distance_m: 100,
  road_ref: "SP-330",
};

const sampleZone3: SpatialZone = {
  zone_id: "zone-3",
  recommendation: "inconclusivo",
  geometry: {
    type: "Polygon",
    coordinates: [[[0.02, 0], [0.03, 0], [0.03, 0.01], [0.02, 0.01], [0.02, 0]]],
  },
  area_m2: 2000.0,
  confidence: "low",
  analysis_quality: "low",
  reasons: ["insufficient_observations"],
  start_distance_m: 100,
  end_distance_m: 150,
  road_ref: "SP-330",
};

const baseAnalysisResponse: AnalysisResponse = {
  analysis_id: "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  status: "completed",
  analysis_period: {
    start_date: "2026-07-01",
    end_date: "2026-07-31",
    timezone: "America/Sao_Paulo",
    strategy: "previous_calendar_month",
  },
  recommendation: {
    decision: "nao_cortar",
    confidence: "high",
    experimental: false,
    summary: "Vegetacao abaixo do nivel historico alto sem crescimento acelerado.",
    reasons: ["current_percentile_below_or_equal_50"],
    blocking_reasons: [],
    limitations: [],
    metrics: {},
  },
  selected_area_m2: 10000.0,
  effective_analysis_area_m2: 9500.0,
  effective_analysis_pct: 95.0,
  aoi: { area_square_meters: 10000.0 },
  summary: { analysis_quality: { status: "high" } },
  timeseries: [],
  scenes: [],
  artifacts: {},
  warnings: [],
  errors: [],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Spatial Segmentation - Schemas", () => {
  it("valida schema de SpatialZone individual", () => {
    const parsed = spatialZoneSchema.safeParse(sampleZone1);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.zone_id).toBe("zone-1");
      expect(parsed.data.recommendation).toBe("cortar");
      expect(parsed.data.road_ref).toBe("SP-330");
    }
  });

  it("valida schema de SpatialSegmentation com status available", () => {
    const payload = {
      status: "available",
      experimental: true,
      section_length_m: 50,
      effective_coverage_pct: 95.0,
      zones: [sampleZone1, sampleZone2, sampleZone3],
    };
    const parsed = spatialSegmentationSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
  });
});

describe("Spatial Segmentation - Area-Weighted Calculations", () => {
  it("calcula percentuais por área em vez de quantidade de zonas", () => {
    // 6 zones scenario:
    // 1 CORTAR of 5000m²
    // 2 NÃO CORTAR of 1500m² each = 3000m²
    // 3 INCONCLUSIVO of 666.67m² each = 2000m²
    // Total = 10000m²
    // Zone count: 1 (17%), 2 (33%), 3 (50%)
    // Area weight: 50% CORTAR, 30% NÃO CORTAR, 20% INCONCLUSIVO
    const sixZones: SpatialZone[] = [
      { ...sampleZone1, zone_id: "z1", area_m2: 5000.0, recommendation: "cortar" },
      { ...sampleZone2, zone_id: "z2", area_m2: 1500.0, recommendation: "nao_cortar" },
      { ...sampleZone2, zone_id: "z3", area_m2: 1500.0, recommendation: "nao_cortar" },
      { ...sampleZone3, zone_id: "z4", area_m2: 666.67, recommendation: "inconclusivo" },
      { ...sampleZone3, zone_id: "z5", area_m2: 666.67, recommendation: "inconclusivo" },
      { ...sampleZone3, zone_id: "z6", area_m2: 666.66, recommendation: "inconclusivo" },
    ];

    const stats = calculateZoneAreaStats(sixZones, 95.0);

    expect(stats.totalZones).toBe(6);
    expect(stats.totalArea).toBeCloseTo(10000, 0);

    // Area-weighted percentages
    expect(Math.round(stats.cutPct)).toBe(50); // NOT 17%
    expect(Math.round(stats.noCutPct)).toBe(30); // NOT 33%
    expect(Math.round(stats.inconclusivePct)).toBe(20); // NOT 50%

    expect(stats.cutCount).toBe(1);
    expect(stats.noCutCount).toBe(2);
    expect(stats.inconclusiveCount).toBe(3);
  });

  it("lida com lista vazia de zonas sem divisão por zero", () => {
    const stats = calculateZoneAreaStats([]);
    expect(stats.totalZones).toBe(0);
    expect(stats.totalArea).toBe(0);
    expect(stats.cutPct).toBe(0);
    expect(stats.noCutPct).toBe(0);
    expect(stats.inconclusivePct).toBe(0);
  });
});

describe("Spatial Segmentation - Map Layers & Visual Hierarchy", () => {
  const visualState = getAoiVisualState({ editing: false, dirty: false, validation: "valid", recommendation: "nao_cortar" });

  it("torna o preenchimento da AOI transparente quando há zonas de segmentação ativas", () => {
    const map = new LayerMapMock();
    installOrUpdateAoiLayer(map as never, samplePolygon, visualState, { hasActiveZones: true });

    const fillLayer = map.getLayer(AOI_LAYER_IDS.fill);
    expect(fillLayer?.paint?.["fill-opacity"]).toBe(0);

    const outlineLayer = map.getLayer(AOI_LAYER_IDS.outline);
    expect(outlineLayer?.paint?.["line-width"]).toBe(2.5);
    expect(outlineLayer?.paint?.["line-opacity"]).toBe(0.45);
  });

  it("mantém o preenchimento normal da AOI quando NÃO há zonas ativas", () => {
    const map = new LayerMapMock();
    installOrUpdateAoiLayer(map as never, samplePolygon, visualState, { hasActiveZones: false });

    const fillLayer = map.getLayer(AOI_LAYER_IDS.fill);
    expect(fillLayer?.paint?.["fill-opacity"]).toBe(visualState.fillOpacity);
  });

  it("instala e remove camadas de zonas com cores de preenchimento e contorno", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1, sampleZone2, sampleZone3]);

    expect(map.sources.has(ZONES_LAYER_IDS.source)).toBe(true);
    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(true);
    expect(map.layers.has(ZONES_LAYER_IDS.outline)).toBe(true);

    removeZonesLayer(map as never);
    expect(map.sources.has(ZONES_LAYER_IDS.source)).toBe(false);
    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(false);
    expect(map.layers.has(ZONES_LAYER_IDS.outline)).toBe(false);
  });

  it("gerencia hover state e cursor de ponteiro nas zonas", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1]);
    const interaction = installZoneClickInteraction(map as never);

    // Mouseenter
    map.emit("mouseenter", { features: [{ id: "zone-1", properties: sampleZone1 }] });
    expect(map.canvas.style.cursor).toBe("pointer");
    expect(map.getFeatureState({ source: ZONES_LAYER_IDS.source, id: "zone-1" })).toEqual({ hover: true });

    // Mouseleave
    map.emit("mouseleave", {});
    expect(map.canvas.style.cursor).toBe("");
    expect(map.getFeatureState({ source: ZONES_LAYER_IDS.source, id: "zone-1" })).toEqual({ hover: false });

    interaction.dispose();
  });
});

describe("Spatial Segmentation - AnalysisResultSidebar Presentation", () => {
  it("renderiza resumo de segmentação por área com cartões corretos e callout de intervenção localizada", () => {
    const resultWithZones: AnalysisResponse = {
      ...baseAnalysisResponse,
      recommendation: {
        ...baseAnalysisResponse.recommendation,
        decision: "nao_cortar", // Global NÃO CORTAR
      },
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: 95.0,
        zones: [sampleZone1, sampleZone2, sampleZone3], // 50% cortar, 30% nao_cortar, 20% inconclusivo
      },
    };

    render(<AnalysisResultSidebar result={resultWithZones} onRetry={() => {}} />);

    // 1. Two levels: "Resultado consolidado" header
    expect(screen.getByText("Resultado consolidado")).toBeInTheDocument();
    expect(screen.getAllByText("NÃO CORTAR").length).toBeGreaterThanOrEqual(1);

    // 2. Callout for localized intervention when CORTAR zone exists
    expect(screen.getByText(/1 zona requer intervenção/)).toBeInTheDocument();
    expect(screen.getByText(/5\.000 m² · 50% da área segmentada/)).toBeInTheDocument();
    expect(screen.queryByText("Intervenção localizada identificada")).not.toBeInTheDocument();

    // 3. Spatial segmentation summary section
    const summary = screen.getByLabelText("Segmentação espacial");
    expect(summary).toBeInTheDocument();
    expect(within(summary).getByText("Resultado espacial")).toBeInTheDocument();
    expect(within(summary).getByText("Segmentação por zonas")).toBeInTheDocument();
    expect(within(summary).getByText("3")).toBeInTheDocument();

    // Area-weighted percentages in cards
    expect(within(summary).getByText("50%")).toBeInTheDocument(); // CORTAR (5000m² / 10000m²)
    expect(within(summary).getByText("30%")).toBeInTheDocument(); // NÃO CORTAR (3000m² / 10000m²)
    expect(within(summary).getByText("20%")).toBeInTheDocument(); // INCONCLUSIVO (2000m² / 10000m²)

    // Formatted m² areas in cards
    expect(within(summary).getByText("5.000 m²")).toBeInTheDocument();
    expect(within(summary).getByText("3.000 m²")).toBeInTheDocument();
    expect(within(summary).getByText("2.000 m²")).toBeInTheDocument();

    // Inconclusive explanation note
    expect(within(summary).getByText("Dados insuficientes para uma recomendação local")).toBeInTheDocument();

    // Effective coverage
    expect(within(summary).getByText("Cobertura efetiva")).toBeInTheDocument();
    expect(within(summary).getByText("95%")).toBeInTheDocument();
  });

  it("NÃO exibe alerta de intervenção localizada quando não há zonas CORTAR", () => {
    const resultNoCutZones: AnalysisResponse = {
      ...baseAnalysisResponse,
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: 95.0,
        zones: [sampleZone2, sampleZone3], // Only NÃO CORTAR and INCONCLUSIVO
      },
    };

    render(<AnalysisResultSidebar result={resultNoCutZones} onRetry={() => {}} />);

    expect(screen.queryByText(/requer intervenção/)).not.toBeInTheDocument();
  });

  it("preserva recomendação global normal sem erros quando status === 'not_applicable'", () => {
    const resultNotApplicable: AnalysisResponse = {
      ...baseAnalysisResponse,
      spatial_segmentation: {
        status: "not_applicable",
        experimental: false,
        section_length_m: 50,
        effective_coverage_pct: null,
        zones: [],
      },
    };

    render(<AnalysisResultSidebar result={resultNotApplicable} onRetry={() => {}} />);

    expect(screen.getByText("Recomendação")).toBeInTheDocument();
    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.queryByText("Segmentação espacial")).not.toBeInTheDocument();
    expect(screen.queryByText(/requer intervenção/)).not.toBeInTheDocument();
  });
});

describe("Spatial Segmentation - RecommendationPanel Callout", () => {
  it("exibe callout de intervenção localizada quando recomendação global é NÃO CORTAR mas existem zonas locais CORTAR", () => {
    const resultMixed: AnalysisResponse = {
      ...baseAnalysisResponse,
      recommendation: {
        ...baseAnalysisResponse.recommendation,
        decision: "nao_cortar",
      },
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: 95.0,
        zones: [sampleZone1, sampleZone2],
      },
    };

    render(<RecommendationPanel result={resultMixed} />);

    expect(screen.getByText("Resultado consolidado")).toBeInTheDocument();
    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.getByText(/1 zona requer intervenção/)).toBeInTheDocument();
    expect(screen.getByText(/5\.000 m² · 63% da área segmentada/)).toBeInTheDocument();
  });
});
