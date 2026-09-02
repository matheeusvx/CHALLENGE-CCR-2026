import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  installOrUpdateZonesLayer,
  removeZonesLayer,
  installZoneClickInteraction,
  ZONES_LAYER_IDS,
} from "@/components/map/layers/spatial-zones-layer";
import { MapLegend } from "@/components/map/map-legend";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import {
  spatialZoneSchema,
  spatialSegmentationSchema,
  type AnalysisResponse,
  type SpatialZone,
} from "@/lib/schemas/analyses";
import { getAoiVisualState } from "@/lib/map/aoi-visual-state";

// ---------------------------------------------------------------------------
// Mock MapLibre Map for layer testing
// ---------------------------------------------------------------------------

class LayerMapMock {
  sources = new Map<string, { setData: ReturnType<typeof vi.fn>; data: unknown }>();
  layers = new Map<string, { id: string; type?: string; paint?: Record<string, unknown> }>();
  handlers = new Map<string, Array<{ layerId?: string; fn: (e: unknown) => void }>>();
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
  emit(event: string, e: unknown) {
    const list = this.handlers.get(event) ?? [];
    list.forEach((h) => h.fn(e));
  }
}

// ---------------------------------------------------------------------------
// Test Data
// ---------------------------------------------------------------------------

const sampleZone1: SpatialZone = {
  zone_id: "zone-1",
  recommendation: "cortar",
  geometry: {
    type: "Polygon",
    coordinates: [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]],
  },
  area_m2: 1250.5,
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
  area_m2: 1300.0,
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
  area_m2: 980.2,
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
    decision: "cortar",
    confidence: "high",
    experimental: false,
    summary: "Recomendacao experimental de corte baseada no historico local.",
    reasons: ["current_percentile_at_or_above_high_threshold"],
    blocking_reasons: [],
    limitations: [],
    metrics: {},
  },
  selected_area_m2: 3530.7,
  effective_analysis_area_m2: 3334.0,
  effective_analysis_pct: 94.43,
  aoi: { area_square_meters: 3530.7 },
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
      effective_coverage_pct: 94.42,
      zones: [sampleZone1, sampleZone2, sampleZone3],
    };
    const parsed = spatialSegmentationSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.zones).toHaveLength(3);
      expect(parsed.data.status).toBe("available");
    }
  });

  it("valida schema de SpatialSegmentation com status not_applicable", () => {
    const payload = {
      status: "not_applicable",
      experimental: false,
      section_length_m: 50,
      effective_coverage_pct: null,
      zones: [],
    };
    const parsed = spatialSegmentationSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.status).toBe("not_applicable");
      expect(parsed.data.zones).toHaveLength(0);
    }
  });
});

describe("Spatial Segmentation - Map Layer", () => {
  it("instala camadas de fill e outline com source GeoJSON", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1, sampleZone2]);

    expect(map.sources.has(ZONES_LAYER_IDS.source)).toBe(true);
    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(true);
    expect(map.layers.has(ZONES_LAYER_IDS.outline)).toBe(true);

    const fillLayer = map.getLayer(ZONES_LAYER_IDS.fill);
    expect(fillLayer?.type).toBe("fill");

    const outlineLayer = map.getLayer(ZONES_LAYER_IDS.outline);
    expect(outlineLayer?.type).toBe("line");
  });

  it("atualiza source existente quando chamada com novas zonas", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1]);
    const source = map.getSource(ZONES_LAYER_IDS.source)!;

    installOrUpdateZonesLayer(map as never, [sampleZone1, sampleZone2, sampleZone3]);
    expect(source.setData).toHaveBeenCalledOnce();
  });

  it("remove camadas e source quando zones array é vazio", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1]);
    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(true);

    installOrUpdateZonesLayer(map as never, []);
    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(false);
    expect(map.layers.has(ZONES_LAYER_IDS.outline)).toBe(false);
    expect(map.sources.has(ZONES_LAYER_IDS.source)).toBe(false);
  });

  it("removeZonesLayer limpa todas as camadas", () => {
    const map = new LayerMapMock();
    installOrUpdateZonesLayer(map as never, [sampleZone1]);
    removeZonesLayer(map as never);

    expect(map.layers.has(ZONES_LAYER_IDS.fill)).toBe(false);
    expect(map.layers.has(ZONES_LAYER_IDS.outline)).toBe(false);
    expect(map.sources.has(ZONES_LAYER_IDS.source)).toBe(false);
  });

  it("interação de clique registra e limpa eventos no mapa", () => {
    const map = new LayerMapMock();
    const interaction = installZoneClickInteraction(map as never);

    expect(map.handlers.get("click")).toHaveLength(1);
    expect(map.handlers.get("mouseenter")).toHaveLength(1);
    expect(map.handlers.get("mouseleave")).toHaveLength(1);

    // Hover effect
    map.emit("mouseenter", {});
    expect(map.canvas.style.cursor).toBe("pointer");

    map.emit("mouseleave", {});
    expect(map.canvas.style.cursor).toBe("");

    // Dispose
    interaction.dispose();
    expect(map.handlers.get("click")).toHaveLength(0);
    expect(map.handlers.get("mouseenter")).toHaveLength(0);
    expect(map.handlers.get("mouseleave")).toHaveLength(0);
  });
});

describe("Spatial Segmentation - MapLegend", () => {
  const aoiState = getAoiVisualState({ editing: false, dirty: false, validation: "valid", recommendation: "cortar" });

  it("exibe legenda das zonas quando hasZones=true", () => {
    render(<MapLegend mapStyle="operational" aoiState={aoiState} hasGeometry={true} hasZones={true} />);

    expect(screen.getByText("CORTAR")).toBeInTheDocument();
    expect(screen.getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(screen.getByText("INCONCLUSIVO")).toBeInTheDocument();
  });

  it("não exibe legenda das zonas quando hasZones=false", () => {
    render(<MapLegend mapStyle="operational" aoiState={aoiState} hasGeometry={true} hasZones={false} />);

    expect(screen.queryByText("CORTAR")).not.toBeInTheDocument();
    expect(screen.queryByText("NÃO CORTAR")).not.toBeInTheDocument();
    expect(screen.queryByText("INCONCLUSIVO")).not.toBeInTheDocument();
  });
});

describe("Spatial Segmentation - AnalysisResultSidebar", () => {
  it("renderiza resumo de segmentação quando status === 'available'", () => {
    const resultWithZones: AnalysisResponse = {
      ...baseAnalysisResponse,
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: 94.42,
        zones: [sampleZone1, sampleZone2, sampleZone3],
      },
    };

    render(<AnalysisResultSidebar result={resultWithZones} onRetry={() => {}} />);

    // Check that the summary is present
    const summary = screen.getByLabelText("Segmentação espacial");
    expect(summary).toBeInTheDocument();
    expect(within(summary).getByText("Segmentação espacial")).toBeInTheDocument();
    expect(within(summary).getByText("3")).toBeInTheDocument();
    expect(within(summary).getByText("zonas identificadas")).toBeInTheDocument();

    // 1 zone out of 3 = 33% each
    expect(within(summary).getByText("CORTAR")).toBeInTheDocument();
    expect(within(summary).getByText("NÃO CORTAR")).toBeInTheDocument();
    expect(within(summary).getByText("INCONCLUSIVO")).toBeInTheDocument();
    expect(within(summary).getAllByText("33%")).toHaveLength(3);
  });

  it("calcula percentuais corretos com distribuição desigual de zonas", () => {
    const resultUnequal: AnalysisResponse = {
      ...baseAnalysisResponse,
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: 90.0,
        zones: [
          sampleZone1, // cortar
          sampleZone1, // cortar
          sampleZone2, // nao_cortar
          sampleZone3, // inconclusivo
        ], // 4 total: 50% cortar, 25% nao_cortar, 25% inconclusivo
      },
    };

    render(<AnalysisResultSidebar result={resultUnequal} onRetry={() => {}} />);

    const summary = screen.getByLabelText("Segmentação espacial");
    expect(within(summary).getByText("4")).toBeInTheDocument();
    expect(within(summary).getByText("zonas identificadas")).toBeInTheDocument();
    expect(within(summary).getByText("50%")).toBeInTheDocument();
    expect(within(summary).getAllByText("25%")).toHaveLength(2);
  });

  it("não renderiza resumo de segmentação quando status === 'not_applicable'", () => {
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

    expect(screen.queryByText("Segmentação espacial")).not.toBeInTheDocument();
    expect(screen.queryByText("zonas identificadas")).not.toBeInTheDocument();
    // Global recommendation still rendered
    expect(screen.getByText("CORTAR")).toBeInTheDocument();
    expect(screen.getByText("Confiança")).toBeInTheDocument();
  });

  it("não renderiza resumo de segmentação quando spatial_segmentation é ausente", () => {
    render(<AnalysisResultSidebar result={baseAnalysisResponse} onRetry={() => {}} />);

    expect(screen.queryByText("Segmentação espacial")).not.toBeInTheDocument();
    expect(screen.queryByText("zonas identificadas")).not.toBeInTheDocument();
    // Global recommendation still rendered
    expect(screen.getByText("CORTAR")).toBeInTheDocument();
  });

  it("não renderiza resumo quando status === 'available' mas zones é vazio", () => {
    const resultEmptyZones: AnalysisResponse = {
      ...baseAnalysisResponse,
      spatial_segmentation: {
        status: "available",
        experimental: true,
        section_length_m: 50,
        effective_coverage_pct: null,
        zones: [],
      },
    };

    render(<AnalysisResultSidebar result={resultEmptyZones} onRetry={() => {}} />);

    expect(screen.queryByText("Segmentação espacial")).not.toBeInTheDocument();
  });
});
