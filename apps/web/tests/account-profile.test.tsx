import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useOperatorProfileStore } from "@/stores/operator-profile-store";
import { useHistoryStore, type HistoryEntry } from "@/stores/history-store";
import {
  computeProductivityStats,
  getRepresentativeRoad,
  getEntryAreaM2,
  filterEntriesByPeriod,
  buildProductivityCsvContent,
} from "@/lib/analytics/productivity";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { AccountView } from "@/components/views/account-view";
import { SettingsView } from "@/components/views/settings-view";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function createMockZone(zone_id: string, road_ref: string) {
  return {
    zone_id,
    road_ref,
    recommendation: "cortar" as const,
    geometry: {},
    area_m2: 1000,
    confidence: "high" as const,
    analysis_quality: "high" as const,
    reasons: ["Vegetação"],
    start_distance_m: 0,
    end_distance_m: 100,
  };
}

function createMockEntry(overrides: {
  id: string;
  savedAt?: string;
  decision?: "cortar" | "nao_cortar" | "inconclusivo";
  confidence?: "high" | "medium" | "low";
  selected_area_m2?: number;
  effective_analysis_area_m2?: number;
  road_refs?: string[];
}): HistoryEntry {
  const decision = overrides.decision ?? "cortar";
  const confidence = overrides.confidence ?? "high";

  return {
    id: overrides.id,
    savedAt: overrides.savedAt ?? new Date().toISOString(),
    geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] },
    response: {
      analysis_id: overrides.id,
      status: "completed",
      analysis_period: {
        start_date: "2026-03-01",
        end_date: "2026-03-31",
        timezone: "America/Sao_Paulo",
        strategy: "explicit",
      },
      recommendation: {
        decision,
        confidence,
        experimental: false,
        summary: "Análise operacional",
        reasons: ["Vegetação detectada"],
        blocking_reasons: [],
        limitations: [],
        metrics: {},
      },
      selected_area_m2: overrides.selected_area_m2 ?? 4000,
      effective_analysis_area_m2: overrides.effective_analysis_area_m2 ?? 3800,
      spatial_segmentation: overrides.road_refs
        ? {
            status: "available",
            experimental: false,
            section_length_m: 100,
            effective_coverage_pct: 100,
            zones: overrides.road_refs.map((ref, idx) => createMockZone(`z${idx + 1}`, ref)),
          }
        : undefined,
      aoi: {},
      summary: {},
      timeseries: [],
      scenes: [],
      artifacts: {},
      warnings: [],
      errors: [],
    },
  };
}

const mockAnalysisEntry1 = createMockEntry({
  id: "ana-001",
  savedAt: new Date().toISOString(),
  decision: "cortar",
  confidence: "high",
  selected_area_m2: 4000,
  road_refs: ["SP-065", "SP-065"],
});

const mockAnalysisEntry2 = createMockEntry({
  id: "ana-002",
  savedAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
  decision: "nao_cortar",
  confidence: "medium",
  selected_area_m2: 3000,
  road_refs: ["SP-330"],
});

const mockAnalysisEntry3 = createMockEntry({
  id: "ana-003",
  savedAt: new Date(Date.now() - 40 * 24 * 60 * 60 * 1000).toISOString(),
  decision: "inconclusivo",
  confidence: "low",
  selected_area_m2: 2000,
});

describe("Operator Profile Store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useOperatorProfileStore.getState().resetProfile();
  });

  it("inicializa com os dados padrão do operador mock", () => {
    const state = useOperatorProfileStore.getState();
    expect(state.name).toBe("Rafael Ferreira");
    expect(state.email).toBe("rafael.ferreira@motiva.com.br");
    expect(state.role).toBe("Analista de operações rodoviárias");
    expect(state.avatarDataUrl).toBeNull();
  });

  it("permite atualizar o nome e email do operador", () => {
    useOperatorProfileStore.getState().updateProfile({
      name: "Mariana Souza",
      email: "mariana.souza@grupoccr.com.br",
    });

    const state = useOperatorProfileStore.getState();
    expect(state.name).toBe("Mariana Souza");
    expect(state.email).toBe("mariana.souza@grupoccr.com.br");
  });

  it("permite atualizar e remover a foto de perfil em base64", () => {
    const dummyBase64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
    useOperatorProfileStore.getState().setAvatar(dummyBase64);
    expect(useOperatorProfileStore.getState().avatarDataUrl).toBe(dummyBase64);

    useOperatorProfileStore.getState().setAvatar(null);
    expect(useOperatorProfileStore.getState().avatarDataUrl).toBeNull();
  });

  it("restaura os dados padrão ao chamar resetProfile", () => {
    useOperatorProfileStore.getState().updateProfile({ name: "Outro Nome", email: "outro@teste.com" });
    useOperatorProfileStore.getState().resetProfile();

    expect(useOperatorProfileStore.getState().name).toBe("Rafael Ferreira");
    expect(useOperatorProfileStore.getState().email).toBe("rafael.ferreira@motiva.com.br");
  });
});

describe("Pure Analytics Utility (Productivity)", () => {
  it("getRepresentativeRoad deduplica referências e seleciona a rodovia de maior frequência com desempate alfabético", () => {
    // 2 zonas com SP-065 -> SP-065
    expect(getRepresentativeRoad(mockAnalysisEntry1)).toBe("SP-065");

    // 1 zona com SP-330 -> SP-330
    expect(getRepresentativeRoad(mockAnalysisEntry2)).toBe("SP-330");

    // Sem zonas espaciais -> null
    expect(getRepresentativeRoad(mockAnalysisEntry3)).toBeNull();

    // Empate de frequências entre SP-330 e SP-065: ordem alfabética escolhe SP-065
    const tieEntry = createMockEntry({
      id: "tie",
      road_refs: ["SP-330", "SP-065"],
    });
    expect(getRepresentativeRoad(tieEntry)).toBe("SP-065");
  });

  it("getEntryAreaM2 extrai área correta de selected_area_m2 ou effective_analysis_area_m2", () => {
    expect(getEntryAreaM2(mockAnalysisEntry1)).toBe(4000);
    expect(getEntryAreaM2(mockAnalysisEntry2)).toBe(3000);
  });

  it("filterEntriesByPeriod filtra corretamente por 7d, 30d e all", () => {
    const list = [mockAnalysisEntry1, mockAnalysisEntry2, mockAnalysisEntry3];

    const last7d = filterEntriesByPeriod(list, "7d");
    expect(last7d).toHaveLength(2);

    const last30d = filterEntriesByPeriod(list, "30d");
    expect(last30d).toHaveLength(2);

    const all = filterEntriesByPeriod(list, "all");
    expect(all).toHaveLength(3);
  });

  it("computeProductivityStats lida com histórico vazio", () => {
    const stats = computeProductivityStats([]);
    expect(stats.totalAnalyses).toBe(0);
    expect(stats.totalAreaM2).toBe(0);
    expect(stats.averageAreaM2).toBe(0);
    expect(stats.mostAnalyzedRoad).toBeNull();
    expect(stats.mostAnalyzedRoadCount).toBe(0);
    expect(stats.roadDistribution).toEqual([]);
    expect(stats.timeline).toEqual([]);
    expect(stats.decisionPercentages.cortar).toBe(0);
  });

  it("computeProductivityStats calcula métricas consolidadas e distribuições reais", () => {
    const list = [mockAnalysisEntry1, mockAnalysisEntry2, mockAnalysisEntry3];
    const stats = computeProductivityStats(list, "all");

    expect(stats.totalAnalyses).toBe(3);
    expect(stats.totalAreaM2).toBe(9000); // 4000 + 3000 + 2000
    expect(stats.averageAreaM2).toBe(3000);
    expect(stats.decisions.cortar).toBe(1);
    expect(stats.decisions.nao_cortar).toBe(1);
    expect(stats.decisions.inconclusivo).toBe(1);
    expect(stats.decisionPercentages.cortar).toBe(33);
    expect(stats.decisionPercentages.nao_cortar).toBe(33);
    expect(stats.decisionPercentages.inconclusivo).toBe(33);

    // Rodovias identificadas: SP-065 (1) e SP-330 (1) de 2 análises com rodovia
    expect(stats.roadDistribution).toHaveLength(2);
    expect(stats.mostAnalyzedRoadCount).toBe(1);
  });

  it("buildProductivityCsvContent gera CSV com cabeçalho, separador de ponto e vírgula e BOM UTF-8", () => {
    const list = [mockAnalysisEntry1];
    const csv = buildProductivityCsvContent(list, "all");

    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain("analysis_id;data;decisão;confiança;área_m2;rodovia;período_da_análise");
    expect(csv).toContain("ana-001;");
    expect(csv).toContain("cortar;high;4000;SP-065;2026-03-01 a 2026-03-31");
  });
});

describe("Sidebar Operator Profile Footer & Popover", () => {
  beforeEach(() => {
    useOperatorProfileStore.getState().resetProfile();
  });

  it("renderiza o rodapé do operador com nome, e-mail e avatar", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    expect(screen.getByText("Rafael Ferreira")).toBeInTheDocument();
    expect(screen.getByText("rafael.ferreira@motiva.com.br")).toBeInTheDocument();
    expect(screen.getByText("RF")).toBeInTheDocument(); // iniciais
  });

  it("abre o menu popover ao clicar no perfil e exibe as opções 'Minha conta', 'Configurações' e 'Sair'", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const trigger = screen.getByTestId("operator-profile-trigger");
    expect(screen.queryByTestId("operator-popover-menu")).toBeNull();

    // Abre popover
    fireEvent.click(trigger);
    expect(screen.getByTestId("operator-popover-menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /minha conta/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /configurações/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /sair/i })).toBeInTheDocument();
  });

  it("navega para 'account' ao selecionar 'Minha conta' e fecha o menu", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    fireEvent.click(screen.getByTestId("operator-profile-trigger"));
    const accountItem = screen.getByRole("menuitem", { name: /minha conta/i });
    fireEvent.click(accountItem);

    expect(onNavigate).toHaveBeenCalledWith("account");
    expect(screen.queryByTestId("operator-popover-menu")).toBeNull();
  });

  it("fecha o menu popover com a tecla Escape", () => {
    render(<AppSidebar activeView="analysis" onNavigate={vi.fn()} />);

    fireEvent.click(screen.getByTestId("operator-profile-trigger"));
    expect(screen.getByTestId("operator-popover-menu")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("operator-popover-menu")).toBeNull();
  });

  it("abre o modal de confirmação de logout ao clicar em 'Sair' e fecha no cancelamento", () => {
    render(<AppSidebar activeView="analysis" onNavigate={vi.fn()} />);

    fireEvent.click(screen.getByTestId("operator-profile-trigger"));
    fireEvent.click(screen.getByRole("menuitem", { name: /sair/i }));

    expect(screen.getByText("Encerrar sessão neste dispositivo?")).toBeInTheDocument();
    expect(screen.getByText(/modo de demonstração local/i)).toBeInTheDocument();

    // Cancelar
    fireEvent.click(screen.getByRole("button", { name: /cancelar/i }));
    expect(screen.queryByText("Encerrar sessão neste dispositivo?")).toBeNull();
  });

  it("confirmação de logout fecha o modal e mantém os dados locais preservados", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    fireEvent.click(screen.getByTestId("operator-profile-trigger"));
    fireEvent.click(screen.getByRole("menuitem", { name: /sair/i }));

    const confirmBtn = screen.getByTestId("confirm-logout-btn");
    fireEvent.click(confirmBtn);

    expect(screen.queryByText("Encerrar sessão neste dispositivo?")).toBeNull();
    // Confirma que os dados do perfil permanecem
    expect(useOperatorProfileStore.getState().name).toBe("Rafael Ferreira");
  });
});

describe("AccountView Component", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useOperatorProfileStore.getState().resetProfile();
    useHistoryStore.setState({ entries: [] });
  });

  it("renderiza o cabeçalho 'Minha conta' e os dados do operador", () => {
    render(<AccountView onNavigate={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Minha conta" })).toBeInTheDocument();
    expect(screen.getByText("Rafael Ferreira")).toBeInTheDocument();
    expect(screen.getByText("Analista de operações rodoviárias")).toBeInTheDocument();
    expect(screen.getByText("CCR Motiva")).toBeInTheDocument();
    expect(screen.getByText("rafael.ferreira@motiva.com.br")).toBeInTheDocument();
  });

  it("permite alternar para o modo de edição e valida nome e e-mail", async () => {
    render(<AccountView onNavigate={vi.fn()} />);

    const editBtn = screen.getByRole("button", { name: /editar perfil/i });
    fireEvent.click(editBtn);

    const nameInput = screen.getByLabelText(/nome completo/i);
    const emailInput = screen.getByLabelText(/e-mail corporativo/i);

    // Limpar nome e submeter
    fireEvent.change(nameInput, { target: { value: "" } });
    fireEvent.submit(screen.getByTestId("profile-edit-form"));

    expect(screen.getByText("O nome deve ter pelo menos 2 caracteres.")).toBeInTheDocument();

    // E-mail inválido
    fireEvent.change(nameInput, { target: { value: "Carlos Mendes" } });
    fireEvent.change(emailInput, { target: { value: "email-invalido" } });
    fireEvent.submit(screen.getByTestId("profile-edit-form"));

    expect(screen.getByText("Informe um endereço de e-mail válido.")).toBeInTheDocument();

    // Corrigir e salvar com sucesso
    fireEvent.change(emailInput, { target: { value: "carlos.mendes@grupoccr.com.br" } });
    fireEvent.submit(screen.getByTestId("profile-edit-form"));

    await waitFor(() => {
      expect(useOperatorProfileStore.getState().name).toBe("Carlos Mendes");
      expect(useOperatorProfileStore.getState().email).toBe("carlos.mendes@grupoccr.com.br");
      expect(screen.getByRole("status")).toHaveTextContent("Perfil atualizado com sucesso.");
    });
  });

  it("exibe estado vazio de produtividade quando não há histórico", () => {
    const onNavigate = vi.fn();
    render(<AccountView onNavigate={onNavigate} />);

    expect(
      screen.getByText("Você ainda não possui análises registradas neste navegador.")
    ).toBeInTheDocument();

    const newAnalysisBtn = screen.getByRole("button", { name: /nova análise/i });
    fireEvent.click(newAnalysisBtn);
    expect(onNavigate).toHaveBeenCalledWith("analysis");
  });

  it("exibe KPIs e gráficos consolidados quando há histórico salvo", () => {
    useHistoryStore.setState({ entries: [mockAnalysisEntry1, mockAnalysisEntry2] });
    render(<AccountView onNavigate={vi.fn()} />);

    // Total de análises: 2
    expect(screen.getByText("2")).toBeInTheDocument();
    // Rodovia mais analisada: SP-065 (ou similar)
    expect(screen.getAllByText(/SP-065/)[0]).toBeInTheDocument();
    // Botão de exportação visível
    expect(screen.getByRole("button", { name: /baixar relatório/i })).toBeInTheDocument();
  });

  it("permite filtrar por período (7d, 30d, all)", () => {
    useHistoryStore.setState({ entries: [mockAnalysisEntry1, mockAnalysisEntry2, mockAnalysisEntry3] });
    render(<AccountView onNavigate={vi.fn()} />);

    const filter30d = screen.getByRole("radio", { name: /últimos 30 dias/i });
    fireEvent.click(filter30d);
    expect(filter30d).toHaveAttribute("aria-checked", "true");

    const filterAll = screen.getByRole("radio", { name: /todo período/i });
    fireEvent.click(filterAll);
    expect(filterAll).toHaveAttribute("aria-checked", "true");
  });
});

describe("SettingsView Conta Card Navigation", () => {
  it("renderiza o card 'Conta' com botão 'Abrir minha conta' que navega para 'account'", () => {
    const onNavigate = vi.fn();
    renderWithQuery(<SettingsView onNavigate={onNavigate} />);

    expect(screen.getByText("Conta")).toBeInTheDocument();
    expect(
      screen.getByText(/gerencie seu perfil, preferências da conta e produtividade/i)
    ).toBeInTheDocument();

    const openAccountBtn = screen.getByRole("button", { name: /abrir minha conta/i });
    expect(openAccountBtn).toBeInTheDocument();

    fireEvent.click(openAccountBtn);
    expect(onNavigate).toHaveBeenCalledWith("account");
  });
});
