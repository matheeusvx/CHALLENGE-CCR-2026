import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { ValidationView } from "@/components/views/validation-view";
import { SampleDetailModal } from "@/components/validation/sample-detail-modal";
import { SaveValidationSampleModal } from "@/components/validation/save-validation-sample-modal";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import { ApiError } from "@/lib/api/client";
import * as validationApi from "@/lib/api/validation";
import type {
  ValidationSampleDetail,
  ValidationSampleList,
  ValidationSummary,
} from "@/lib/schemas/validation";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

vi.mock("@/lib/api/validation");
vi.mock("@/lib/api/analyses", () => ({
  getHealth: vi.fn().mockResolvedValue({ status: "ok", service: "motiva-api", version: "0.1.0" }),
  validateGeometry: vi.fn(),
  runAnalysis: vi.fn(),
}));
vi.mock("@/components/map/analysis-map", () => ({
  AnalysisMap: () => <div data-testid="analysis-map" />,
}));
vi.mock("@/components/guia/guia-widget", () => ({
  GuiaWidget: () => <div data-testid="guia-widget">gu.ia Assistente</div>,
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return function TestWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

const emptySummary: ValidationSummary = {
  total_samples: 0,
  target_total: 30,
  remaining_total: 30,
  target_per_class: 6,
  counts_by_vegetation_class: {
    low_grass: 0,
    tall_dense_grass: 0,
    shrub: 0,
    tree: 0,
    mixed: 0,
  },
  by_vegetation_class: {
    low_grass: { count: 0, target: 6, remaining: 6, statistics: {} },
    tall_dense_grass: { count: 0, target: 6, remaining: 6, statistics: {} },
    shrub: { count: 0, target: 6, remaining: 6, statistics: {} },
    tree: { count: 0, target: 6, remaining: 6, statistics: {} },
    mixed: { count: 0, target: 6, remaining: 6, statistics: {} },
  },
  counts_by_maintenance_truth: { cut: 0, no_cut: 0, uncertain: 0 },
};

const populatedSummary: ValidationSummary = {
  total_samples: 3,
  target_total: 30,
  remaining_total: 27,
  target_per_class: 6,
  counts_by_vegetation_class: {
    low_grass: 1,
    tall_dense_grass: 0,
    shrub: 2,
    tree: 0,
    mixed: 0,
  },
  by_vegetation_class: {
    low_grass: {
      count: 1,
      target: 6,
      remaining: 5,
      statistics: {
        sentinel2: {
          current_ndvi_median: { count: 1, median: 0.35, mean: 0.35, min: 0.35, max: 0.35 },
        },
        sentinel1: {
          canonical_vv_sigma0_db: { count: 1, median: -12.4, mean: -12.4, min: -12.4, max: -12.4 },
          canonical_vh_sigma0_db: { count: 1, median: -18.2, mean: -18.2, min: -18.2, max: -18.2 },
          canonical_vh_minus_vv_db: { count: 1, median: -5.8, mean: -5.8, min: -5.8, max: -5.8 },
        },
      },
    },
    tall_dense_grass: { count: 0, target: 6, remaining: 6, statistics: {} },
    shrub: {
      count: 2,
      target: 6,
      remaining: 4,
      statistics: {
        sentinel2: {
          current_ndvi_median: { count: 2, median: 0.58, mean: 0.58, min: 0.52, max: 0.64 },
        },
        sentinel1: {
          canonical_vv_sigma0_db: { count: 2, median: -9.5, mean: -9.5, min: -10.1, max: -8.9 },
          canonical_vh_sigma0_db: { count: 2, median: -15.1, mean: -15.1, min: -15.5, max: -14.7 },
          canonical_vh_minus_vv_db: { count: 2, median: -5.6, mean: -5.6, min: -5.8, max: -5.4 },
        },
      },
    },
    tree: { count: 0, target: 6, remaining: 6, statistics: {} },
    mixed: { count: 0, target: 6, remaining: 6, statistics: {} },
  },
  counts_by_maintenance_truth: { cut: 2, no_cut: 1, uncertain: 0 },
};

const sampleRow = {
  sample_id: "c1a1b2c3-d4e5-4f6a-b7c8-d9e0f1a2b3c4",
  analysis_id: "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
  schema_version: 1,
  created_at: "2026-09-04T18:30:00Z",
  vegetation_class: "shrub" as const,
  maintenance_truth: "cut" as const,
  validation_source: "field_inspection" as const,
  reference_date: "2026-09-04",
  notes: "Vegetação arbustiva densa.",
  selected_area_m2: 4500,
  s2_decision: "cortar",
  s2_confidence: "high",
  s2_ndvi_mean: 0.58,
  s2_ndvi_median: 0.58,
  s2_current_percentile: 82,
  s1_status: "available",
  s1_quality: 92,
  s1_coverage: 96,
  s1_canonical_relative_orbit: 53,
  s1_canonical_observation_count: 5,
  s1_vv_sigma0_linear: 0.04,
  s1_vh_sigma0_linear: 0.01,
  s1_vv_sigma0_db: -13.9794,
  s1_vh_sigma0_db: -20.0,
  s1_vh_minus_vv_db: -6.0206,
  s1_vh_vv_sigma0_ratio: 0.25,
};

const sampleRowWithNulls = {
  sample_id: "c2a2b2c3-d4e5-4f6a-b7c8-d9e0f1a2b3c4",
  analysis_id: "a2b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
  schema_version: 1,
  created_at: "2026-09-04T19:00:00Z",
  vegetation_class: "low_grass" as const,
  maintenance_truth: "no_cut" as const,
  validation_source: "visual_inspection" as const,
  reference_date: "2026-09-03",
  notes: null,
  selected_area_m2: null,
  s2_decision: null,
  s2_confidence: null,
  s2_ndvi_mean: null,
  s2_ndvi_median: null,
  s2_current_percentile: null,
  s1_status: null,
  s1_quality: null,
  s1_coverage: null,
  s1_canonical_relative_orbit: null,
  s1_canonical_observation_count: null,
  s1_vv_sigma0_linear: null,
  s1_vh_sigma0_linear: null,
  s1_vv_sigma0_db: null,
  s1_vh_sigma0_db: null,
  s1_vh_minus_vv_db: null,
  s1_vh_vv_sigma0_ratio: null,
};

const sampleDetail: ValidationSampleDetail = {
  ...sampleRow,
  snapshot: {
    schema_version: 1,
    sample_id: sampleRow.sample_id,
    analysis_id: sampleRow.analysis_id,
    created_at: sampleRow.created_at,
    aoi: {
      selected_area_m2: 4500,
      effective_analysis_area_m2: 4200,
      effective_analysis_pct: 93.3,
    },
    sentinel2: {
      analysis_status: "completed",
      recommendation_decision: "cortar",
      recommendation_confidence: "high",
      observation_count: 6,
      current_ndvi_mean: 0.58,
      current_ndvi_median: 0.58,
      current_percentile: 82,
      recent_trend: "increasing",
      recent_trend_status: "increasing",
      analysis_quality_status: "high",
      vegetation_fraction: 0.75,
    },
    sentinel1: {
      source_status: "available",
      quality: 92,
      coverage: 96,
      canonical_relative_orbit: 53,
      canonical_observation_count: 5,
      temporal_comparability: "single_orbit",
      radiometric_calibration_status: "calibrated",
      canonical_metrics: {
        vv_sigma0_median_db: -13.9794,
        vh_sigma0_median_db: -20.0,
        vh_minus_vv_db_median: -6.0206,
      },
    },
    ground_truth: {
      vegetation_class: "shrub",
      maintenance_truth: "cut",
      validation_source: "field_inspection",
      reference_date: "2026-09-04",
      notes: "Vegetação arbustiva densa.",
    },
  },
};

const sampleList: ValidationSampleList = {
  items: [sampleRow, sampleRowWithNulls],
  total: 2,
  limit: 100,
  offset: 0,
};

const dummyAnalysisResult: AnalysisResponse = {
  analysis_id: "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
  status: "completed",
  analysis_period: {
    start_date: "2026-07-01",
    end_date: "2026-08-01",
    timezone: "America/Sao_Paulo",
    strategy: "previous_calendar_month",
  },
  recommendation: {
    decision: "cortar",
    confidence: "high",
    experimental: true,
    summary: "Recomendação experimental de corte.",
    reasons: ["ndvi_high"],
    blocking_reasons: [],
    limitations: [],
    metrics: {},
  },
  selected_area_m2: 4500,
  effective_analysis_area_m2: 4200,
  effective_analysis_pct: 93,
  aoi: { source: "geojson_inline" },
  summary: { analysis_quality: { status: "high", score: 90 } },
  timeseries: [],
  scenes: [],
  artifacts: {},
  warnings: [],
  errors: [],
};

describe("Validation Module — Navegação e Sidebar", () => {
  it("sidebar substitui 'Validação' por 'Alertas' e 'Validação' não aparece na navegação", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    expect(screen.queryByRole("button", { name: "Validação" })).toBeNull();
    expect(screen.getByRole("button", { name: "Alertas" })).toBeInTheDocument();
    expect(screen.getByTestId("guia-widget")).toBeInTheDocument();

    const buttons = screen.getAllByRole("button");
    const labels = buttons.map((b) => b.textContent);
    const alertsIndex = labels.findIndex((l) => l?.includes("Alertas"));
    const sourcesIndex = labels.findIndex((l) => l?.includes("Fontes de dados"));
    expect(alertsIndex).toBeLessThan(sourcesIndex);
    expect(screen.queryByRole("button", { name: "Configurações" })).toBeNull();
  });
});

describe("ValidationView — Estrutura, Progresso e Tabela", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(validationApi.getValidationExportUrl).mockReturnValue("http://localhost:8000/api/validation-export");
  });

  it("renderiza estado vazio quando total_samples == 0 com botão 'Nova análise'", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(emptySummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });

    const onStartNewAnalysis = vi.fn();
    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={onStartNewAnalysis} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByText("Nenhuma amostra registrada")).toBeInTheDocument();
    });

    expect(screen.getByText("Base de amostras de referência para avaliação do Sentinel-1 e Sentinel-2.")).toBeInTheDocument();
    expect(screen.getByText(/Os dados desta área não alteram a recomendação operacional/)).toBeInTheDocument();

    const cta = screen.getByRole("button", { name: /Nova análise/ });
    fireEvent.click(cta);
    expect(onStartNewAnalysis).toHaveBeenCalled();
  });

  it("renderiza progresso do dataset (X / 30) e cards por classe com counts da API", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(populatedSummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue(sampleList);

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByText("Progresso do dataset")).toBeInTheDocument();
    });

    const remainingPill = screen.getByText(/restantes/);
    expect(remainingPill).toHaveTextContent(/27\s*restantes/);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");

    // Cards por classe
    expect(screen.getAllByText("Gramado baixo").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Arbusto").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Árvores").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Área mista").length).toBeGreaterThanOrEqual(1);
  });

  it("exibe bloco de estatísticas descritivas com aviso de não regra operacional", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(populatedSummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue(sampleList);

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByText("Comparativo por classe")).toBeInTheDocument();
    });

    expect(screen.getByText("Estatísticas descritivas. Não representam regra operacional.")).toBeInTheDocument();
  });

  it("renderiza tabela de amostras e exibe '—' para campos nulos", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(populatedSummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue(sampleList);

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByRole("table", { name: /Amostras de validação registradas/ })).toBeInTheDocument();
    });

    // Confirma linhas
    const rows = screen.getAllByRole("row");
    expect(rows.length).toBe(3); // header + 2 amostras

    // Segunda amostra possui vários nulls, que devem ser exibidos como "—"
    const secondDataRow = rows[2];
    const dashes = within(secondDataRow).getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(4);
  });

  it("atualiza query com filtros sem recarregar a página", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(populatedSummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue(sampleList);

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Tipo de vegetação:")).toBeInTheDocument();
    });

    const vegFilter = screen.getByLabelText("Tipo de vegetação:");
    fireEvent.change(vegFilter, { target: { value: "shrub" } });

    await waitFor(() => {
      expect(validationApi.getValidationSamples).toHaveBeenCalledWith(
        expect.objectContaining({ vegetation_class: "shrub" }),
      );
    });
  });

  it("botão 'Exportar CSV' dispara o download via URL da API", async () => {
    vi.mocked(validationApi.getValidationSummary).mockResolvedValue(populatedSummary);
    vi.mocked(validationApi.getValidationSamples).mockResolvedValue(sampleList);

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ValidationView onStartNewAnalysis={() => {}} />
      </Wrapper>,
    );

    const exportButton = screen.getByRole("button", { name: /Exportar CSV/ });
    expect(exportButton).toBeInTheDocument();
  });
});

describe("SampleDetailModal — Detalhes da Amostra", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("abre modal com grupos GROUND TRUTH, AOI, SENTINEL-2 e SENTINEL-1", async () => {
    vi.mocked(validationApi.getValidationSample).mockResolvedValue(sampleDetail);

    const onClose = vi.fn();
    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <SampleDetailModal sampleId={sampleDetail.sample_id} onClose={onClose} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Detalhe da amostra de validação" })).toBeInTheDocument();
      expect(screen.getByText("Ground truth (Referência de campo)")).toBeInTheDocument();
    });

    expect(screen.getByText("Área de interesse (AOI)")).toBeInTheDocument();
    expect(screen.getByText("Sentinel-2 (Óptico)")).toBeInTheDocument();
    expect(screen.getByText("Sentinel-1 (Radar SAR)")).toBeInTheDocument();

    expect(screen.getByText("Vegetação arbustiva densa.")).toBeInTheDocument();
    expect(screen.getByText(sampleDetail.analysis_id)).toBeInTheDocument();

    const closeBtn = screen.getByRole("button", { name: "Fechar detalhe da amostra" });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Cadastro de Amostra — Sidebar e Modal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("botão 'Salvar para validação' aparece no AnalysisResultSidebar quando há resultado válido", () => {
    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <AnalysisResultSidebar result={dummyAnalysisResult} onRetry={() => {}} />
      </Wrapper>,
    );

    const saveBtn = screen.getByRole("button", { name: /Salvar para validação/ });
    expect(saveBtn).toBeInTheDocument();
  });

  it("abre SaveValidationSampleModal com campos obrigatórios e sem métricas científicas", () => {
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const Wrapper = createWrapper();

    render(
      <Wrapper>
        <SaveValidationSampleModal
          analysisId={dummyAnalysisResult.analysis_id}
          isOpen={true}
          onClose={onClose}
          onSaved={onSaved}
        />
      </Wrapper>,
    );

    expect(screen.getByRole("heading", { name: "Salvar amostra de validação" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Tipo de vegetação/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Necessidade real de manutenção/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Fonte da validação/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Data da referência/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Observações/)).toBeInTheDocument();

    // Garante que não pede métricas científicas
    expect(screen.queryByLabelText(/NDVI/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/sigma0/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/VV/)).not.toBeInTheDocument();
  });

  it("envia payload correto no submit e trata sucesso", async () => {
    vi.mocked(validationApi.createValidationSample).mockResolvedValue(sampleDetail);

    const onClose = vi.fn();
    const onSaved = vi.fn();
    const Wrapper = createWrapper();

    render(
      <Wrapper>
        <SaveValidationSampleModal
          analysisId={dummyAnalysisResult.analysis_id}
          isOpen={true}
          onClose={onClose}
          onSaved={onSaved}
        />
      </Wrapper>,
    );

    const submitBtn = screen.getByRole("button", { name: /Salvar amostra/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(validationApi.createValidationSample).toHaveBeenCalledWith(
        expect.objectContaining({
          analysis_id: dummyAnalysisResult.analysis_id,
          vegetation_class: "low_grass",
          maintenance_truth: "no_cut",
          validation_source: "visual_inspection",
        }),
      );
      expect(onSaved).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("exibe mensagem amigável em caso de erro 409 (duplicidade)", async () => {
    vi.mocked(validationApi.createValidationSample).mockRejectedValue(
      new ApiError("VALIDATION_SAMPLE_EXISTS", "Esta analise ja possui uma amostra de validacao registrada.", 409),
    );

    const onClose = vi.fn();
    const onSaved = vi.fn();
    const Wrapper = createWrapper();

    render(
      <Wrapper>
        <SaveValidationSampleModal
          analysisId={dummyAnalysisResult.analysis_id}
          isOpen={true}
          onClose={onClose}
          onSaved={onSaved}
        />
      </Wrapper>,
    );

    const submitBtn = screen.getByRole("button", { name: /Salvar amostra/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText("Esta análise já foi registrada como amostra de validação.")).toBeInTheDocument();
    });
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("exibe mensagem amigável em caso de erro 404 (análise indisponível)", async () => {
    vi.mocked(validationApi.createValidationSample).mockRejectedValue(
      new ApiError("ANALYSIS_NOT_AVAILABLE", "A analise precisa estar disponivel para ser registrada; execute-a novamente.", 404),
    );

    const onClose = vi.fn();
    const onSaved = vi.fn();
    const Wrapper = createWrapper();

    render(
      <Wrapper>
        <SaveValidationSampleModal
          analysisId={dummyAnalysisResult.analysis_id}
          isOpen={true}
          onClose={onClose}
          onSaved={onSaved}
        />
      </Wrapper>,
    );

    const submitBtn = screen.getByRole("button", { name: /Salvar amostra/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(
        screen.getByText(
          "Esta análise não está mais disponível para registro. Execute a análise novamente e salve a nova execução.",
        ),
      ).toBeInTheDocument();
    });
    expect(onSaved).not.toHaveBeenCalled();
  });
});
