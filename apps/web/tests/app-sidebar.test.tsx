import { render, screen, fireEvent, act } from "@testing-library/react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { AppShell } from "@/components/layout/app-shell";
import { useSettingsStore, SIDEBAR_STORAGE_KEY } from "@/stores/settings-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useOperatorProfileStore } from "@/stores/operator-profile-store";
import { useGuiaStore } from "@/stores/guia-store";
import { TOUR_STEPS } from "@/components/layout/onboarding-tour";

describe("AppSidebar — Redesign Completo (Pontos A até AD)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    useSettingsStore.setState({
      sidebarCollapsed: true, // Padrão para novos operadores
      sidebarHydrated: true,
      themePreference: "dark",
      notificationsEnabled: true,
    });
    useOnboardingStore.setState({
      tourActive: false,
      currentStep: 0,
      operatorMenuOpen: false,
      tourSidebarExpanded: false,
    });
    useOperatorProfileStore.getState().resetProfile();
    useGuiaStore.setState({
      isOpen: false,
      displayMode: "floating",
    });
  });

  it.each([
    { persisted: "false", expectedClass: "sidebar--expanded", expectedLabel: "Recolher barra lateral" },
    { persisted: "true", expectedClass: "sidebar--collapsed", expectedLabel: "Expandir barra lateral" },
  ])(
    "SSR e primeiro render cliente ficam collapsed antes de aplicar preferência $persisted",
    async ({ persisted, expectedClass, expectedLabel }) => {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, persisted);
      useSettingsStore.setState({
        sidebarCollapsed: true,
        sidebarHydrated: false,
      });
      useOnboardingStore.setState({
        onboardingCompleted: true,
        tourActive: false,
        tourSidebarExpanded: false,
      });

      const queryClient = new QueryClient({
        defaultOptions: { queries: { staleTime: Number.POSITIVE_INFINITY } },
      });
      queryClient.setQueryData(["health"], { status: "ok" });
      const ui = (
        <QueryClientProvider client={queryClient}>
          <AppShell>
            <div>Conteúdo</div>
          </AppShell>
        </QueryClientProvider>
      );
      const serverMarkup = renderToString(ui);
      expect(serverMarkup).toContain("app-shell sidebar--collapsed");
      expect(serverMarkup).toContain("Expandir barra lateral");
      expect(serverMarkup).not.toContain("app-shell sidebar--expanded");

      const host = document.createElement("div");
      host.innerHTML = serverMarkup;
      document.body.appendChild(host);
      expect(host.querySelector(".app-shell")).toHaveClass("sidebar--collapsed");

      const recoverableErrors: unknown[] = [];
      let root: Root | undefined;
      await act(async () => {
        root = hydrateRoot(host, ui, {
          onRecoverableError: (error) => recoverableErrors.push(error),
        });
        await Promise.resolve();
      });

      expect(recoverableErrors).toEqual([]);
      expect(host.querySelector(".app-shell")).toHaveClass(expectedClass);
      expect(host.querySelector(".sidebar")).toHaveClass(
        persisted === "false" ? "is-expanded" : "is-collapsed",
      );
      expect(host.querySelector("[data-testid='sidebar-toggle-btn']")).toHaveAttribute(
        "aria-label",
        expectedLabel,
      );

      await act(async () => root?.unmount());
      host.remove();
    },
  );

  // -------------------------------------------------------------------------
  // A) Sidebar inicia collapsed conforme default
  // -------------------------------------------------------------------------
  it("A) inicia collapsed por padrão para novos operadores", () => {
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const sidebar = container.querySelector(".sidebar");
    expect(sidebar).toHaveClass("is-collapsed");
    expect(sidebar).not.toHaveClass("is-expanded");

    const toggleBtn = screen.getByTestId("sidebar-toggle-btn");
    expect(toggleBtn).toHaveAttribute("aria-expanded", "false");
    expect(toggleBtn).toHaveAttribute("aria-label", "Expandir barra lateral");
  });

  // -------------------------------------------------------------------------
  // B) Expandir funciona
  // -------------------------------------------------------------------------
  it("B) expande ao clicar no botão de toggle", () => {
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const toggleBtn = screen.getByTestId("sidebar-toggle-btn");
    fireEvent.click(toggleBtn);

    const sidebar = container.querySelector(".sidebar");
    expect(sidebar).toHaveClass("is-expanded");
    expect(sidebar).not.toHaveClass("is-collapsed");
    expect(toggleBtn).toHaveAttribute("aria-expanded", "true");
    expect(toggleBtn).toHaveAttribute("aria-label", "Recolher barra lateral");
  });

  // -------------------------------------------------------------------------
  // C) Recolher funciona
  // -------------------------------------------------------------------------
  it("C) recolhe ao clicar novamente no botão de toggle", () => {
    useSettingsStore.setState({ sidebarCollapsed: false });
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const toggleBtn = screen.getByTestId("sidebar-toggle-btn");
    expect(container.querySelector(".sidebar")).toHaveClass("is-expanded");

    fireEvent.click(toggleBtn);
    expect(container.querySelector(".sidebar")).toHaveClass("is-collapsed");
    expect(toggleBtn).toHaveAttribute("aria-expanded", "false");
  });

  // -------------------------------------------------------------------------
  // D) Preferência é persistida
  // -------------------------------------------------------------------------
  it("D) persiste a preferência de expansão/recolhimento no localStorage", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const toggleBtn = screen.getByTestId("sidebar-toggle-btn");
    // Expande
    fireEvent.click(toggleBtn);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("false");

    // Recolhe
    fireEvent.click(toggleBtn);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("true");
  });

  // -------------------------------------------------------------------------
  // E) Reload restaura preferência
  // -------------------------------------------------------------------------
  it("E) restaura a preferência persistida de expandido ao inicializar", () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, "false");
    // Simula reload reinicializando a store
    useSettingsStore.setState({
      sidebarCollapsed: window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true",
    });

    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    expect(container.querySelector(".sidebar")).toHaveClass("is-expanded");
  });

  // -------------------------------------------------------------------------
  // F, G, H, I) Navegação entre views funciona corretamente
  // -------------------------------------------------------------------------
  it("F) navega para 'Painel' ao clicar no item correspondente", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="history" onNavigate={onNavigate} />);

    const btn = screen.getByRole("button", { name: "Painel" });
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("analysis");
  });

  it("G) navega para 'Histórico' ao clicar no item correspondente", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const btn = screen.getByRole("button", { name: "Histórico" });
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("history");
  });

  it("H) navega para 'Fontes de dados' ao clicar no item correspondente", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const btn = screen.getByRole("button", { name: "Fontes de dados" });
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("sources");
  });

  it("I) navega para 'Alertas' ao clicar no item correspondente e 'Validação' não aparece na navegação", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const btn = screen.getByRole("button", { name: "Alertas" });
    expect(screen.queryByRole("button", { name: "Validação" })).toBeNull();
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("alerts");
  });

  // -------------------------------------------------------------------------
  // J, K) Exclusividade e fidelidade do item ativo
  // -------------------------------------------------------------------------
  it("J) somente um item de navegação fica ativo por vez", () => {
    const onNavigate = vi.fn();
    const { rerender } = render(<AppSidebar activeView="history" onNavigate={onNavigate} />);

    const historyBtn = screen.getByRole("button", { name: "Histórico" });
    const analysisBtn = screen.getByRole("button", { name: "Painel" });
    const alertsBtn = screen.getByRole("button", { name: "Alertas" });
    const sourcesBtn = screen.getByRole("button", { name: "Fontes de dados" });

    expect(historyBtn).toHaveClass("active");
    expect(analysisBtn).not.toHaveClass("active");
    expect(alertsBtn).not.toHaveClass("active");
    expect(sourcesBtn).not.toHaveClass("active");

    // K) Active state acompanha a view real
    rerender(<AppSidebar activeView="alerts" onNavigate={onNavigate} />);
    expect(historyBtn).not.toHaveClass("active");
    expect(alertsBtn).toHaveClass("active");
  });

  // -------------------------------------------------------------------------
  // L) Tooltips aparecem em collapsed
  // -------------------------------------------------------------------------
  it("L) possui tooltips com role='tooltip' em todos os itens no modo collapsed", () => {
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const tooltips = container.querySelectorAll(".sidebar-tooltip[role='tooltip']");
    // Toggle, Painel, Histórico, Alertas, Fontes, gu.ia, Operador = 7 tooltips
    expect(tooltips.length).toBeGreaterThanOrEqual(7);

    const tooltipTexts = Array.from(tooltips).map((t) => t.textContent?.trim());
    expect(tooltipTexts).toContain("Expandir barra lateral");
    expect(tooltipTexts).toContain("Painel");
    expect(tooltipTexts).toContain("Histórico");
    expect(tooltipTexts).toContain("Alertas");
    expect(tooltipTexts).toContain("Fontes de dados");
    expect(tooltipTexts).toContain("gu.ia — Assistente virtual");
    expect(tooltipTexts.some((txt) => txt?.includes("Rafael Ferreira"))).toBe(true);
  });

  // -------------------------------------------------------------------------
  // M) Labels aparecem em expanded
  // -------------------------------------------------------------------------
  it("M) exibe labels visíveis e logotipo completo no modo expanded", () => {
    useSettingsStore.setState({ sidebarCollapsed: false });
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    expect(container.querySelector(".brand-copy")).toBeInTheDocument();
    expect(container.querySelector(".brand-copy")).toHaveTextContent("Motiva");
    expect(container.querySelector(".brand-copy")).toHaveTextContent("Faixa Verde");

    const labels = container.querySelectorAll(".nav-item-label");
    const labelTexts = Array.from(labels).map((l) => l.textContent?.trim());
    expect(labelTexts).toEqual(["Painel", "Histórico", "Alertas", "Fontes de dados"]);
  });

  it("M2) exibe badge com active_count e formata '99+' se > 99", () => {
    const onNavigate = vi.fn();
    const { rerender } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={4} />);

    const badge = screen.getByTestId("alerts-badge");
    expect(badge).toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "Alertas 4" })).toBeInTheDocument();

    rerender(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={120} />);
    expect(screen.getByTestId("alerts-badge")).toHaveTextContent("99+");
    expect(screen.getByRole("button", { name: "Alertas 99+" })).toBeInTheDocument();
  });

  it("M3) indicador animado de pulso aparece quando active_count > 0 e desaparece quando active_count == 0", () => {
    const onNavigate = vi.fn();
    // 1. zero alertas -> indicador ausente
    const { rerender } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={0} />);
    expect(screen.queryByTestId("alerts-pulse-dot")).toBeNull();

    // 2. active_count > 0 -> indicador visível
    rerender(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={3} />);
    const pulseDot = screen.getByTestId("alerts-pulse-dot");
    expect(pulseDot).toBeInTheDocument();
    expect(pulseDot).toHaveClass("nav-alert-pulse");

    // 3. atualização do active_count remove/adiciona indicador corretamente
    // Remove quando volta a zero
    rerender(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={0} />);
    expect(screen.queryByTestId("alerts-pulse-dot")).toBeNull();

    // Adiciona novamente quando > 0
    rerender(<AppSidebar activeView="analysis" onNavigate={onNavigate} activeCount={15} />);
    expect(screen.getByTestId("alerts-pulse-dot")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // N, O)'u.ia continua abrindo e preserva modos
  // -------------------------------------------------------------------------
  it("N, O) botão gu.ia abre/fecha o assistente e preserva o estado da store", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const guiaBtn = screen.getByRole("button", { name: /abrir assistente gu\.?ia/i });
    expect(useGuiaStore.getState().isOpen).toBe(false);

    fireEvent.click(guiaBtn);
    expect(useGuiaStore.getState().isOpen).toBe(true);

    fireEvent.click(guiaBtn);
    expect(useGuiaStore.getState().isOpen).toBe(false);
  });

  // -------------------------------------------------------------------------
  // P, Q, R, S, T) Menu da conta, popover, perfile logout
  // -------------------------------------------------------------------------
  it("P, Q, R, S, T) abre popover da conta, navega para Perfil/Configurações, dispara modal de Logout e não é cortado", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const profileTrigger = screen.getByTestId("operator-profile-trigger");
    expect(screen.queryByTestId("operator-popover-menu")).toBeNull();

    // P) Abre popover
    fireEvent.click(profileTrigger);
    const popover = screen.getByTestId("operator-popover-menu");
    expect(popover).toBeInTheDocument();

    // T) Popover dentro do rodapé
    expect(popover).toHaveAttribute("role", "menu");

    // Q) Minha conta
    const accountBtn = screen.getByRole("menuitem", { name: /minha conta/i });
    fireEvent.click(accountBtn);
    expect(onNavigate).toHaveBeenCalledWith("account");
    expect(screen.queryByTestId("operator-popover-menu")).toBeNull();

    // R) Configurações
    fireEvent.click(profileTrigger);
    const settingsBtn = screen.getByRole("menuitem", { name: /configurações/i });
    fireEvent.click(settingsBtn);
    expect(onNavigate).toHaveBeenCalledWith("settings");

    // S) Logout e modal
    fireEvent.click(profileTrigger);
    const logoutBtn = screen.getByRole("menuitem", { name: /sair/i });
    fireEvent.click(logoutBtn);

    expect(screen.getByRole("dialog", { name: /encerrar sessão neste dispositivo\?/i })).toBeInTheDocument();
    const cancelBtn = screen.getByRole("button", { name: /cancelar/i });
    fireEvent.click(cancelBtn);
    expect(screen.queryByRole("dialog", { name: /encerrar sessão neste dispositivo\?/i })).toBeNull();
  });

  // -------------------------------------------------------------------------
  // U, V, W, X, Y) Integração com o Tutorial Onboarding
  // -------------------------------------------------------------------------
  it("U, V, W, X, Y) integra targets do tutorial, suporta expansão temporária e restaura estado", () => {
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    // U) Target Histórico
    expect(container.querySelector('[data-tour="nav-history"]')).toBeInTheDocument();
    // V) Target gu.ia
    expect(container.querySelector('[data-tour="guia-widget"]')).toBeInTheDocument();
    // W) Target perfil
    expect(container.querySelector('[data-tour="operator-profile"]')).toBeInTheDocument();

    // X) Etapa Configurações no TOUR_STEPS
    const settingsStep = TOUR_STEPS.find((s) => s.target === "operator-menu-settings");
    expect(settingsStep).toBeDefined();
    expect(settingsStep?.autoOpenMenu).toBe("operator");
    expect(settingsStep?.expandSidebar).toBe(true);

    // Simula tutorial ativando a expansão temporária
    act(() => {
      useOnboardingStore.setState({
        tourActive: true,
        tourSidebarExpanded: true,
        operatorMenuOpen: true,
      });
    });

    // Sidebar expande temporariamente mesmo que sidebarCollapsed seja true
    expect(container.querySelector(".sidebar")).toHaveClass("is-expanded");
    expect(container.querySelector('[data-tour="operator-menu-settings"]')).toBeInTheDocument();

    // Y) Encerramento do tutorial restaura estado collapsed original sem alterar store persistida
    act(() => {
      useOnboardingStore.setState({
        tourActive: false,
        tourSidebarExpanded: false,
        operatorMenuOpen: false,
      });
    });

    expect(container.querySelector(".sidebar")).toHaveClass("is-collapsed");
    expect(useSettingsStore.getState().sidebarCollapsed).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Z, AA) MapLibre resize e ausência de horizontal overflow
  // -------------------------------------------------------------------------
  it("Z, AA) mantém container e navegação preparados para auto-resize sem overflow", () => {
    const onNavigate = vi.fn();
    const { container } = render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    const nav = container.querySelector("nav");
    expect(nav).toBeInTheDocument();
    expect(nav).toHaveAttribute("data-tour", "navigation");
  });

  // -------------------------------------------------------------------------
  // AB, AC, AD) Acessibilidade (teclado, aria-expanded, aria-current)
  // -------------------------------------------------------------------------
  it("AB, AC, AD) garante navegação acessível, aria-expanded e aria-current válidos", () => {
    const onNavigate = vi.fn();
    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);

    // AC) aria-expanded no toggle
    const toggleBtn = screen.getByTestId("sidebar-toggle-btn");
    expect(toggleBtn).toHaveAttribute("aria-expanded", "false");

    // AD) aria-current="page" no item ativo
    const analysisBtn = screen.getByRole("button", { name: "Painel" });
    const historyBtn = screen.getByRole("button", { name: "Histórico" });
    expect(analysisBtn).toHaveAttribute("aria-current", "page");
    expect(historyBtn).not.toHaveAttribute("aria-current");

    // AB) Escape fecha menu do operador
    const profileTrigger = screen.getByTestId("operator-profile-trigger");
    fireEvent.click(profileTrigger);
    expect(profileTrigger).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(profileTrigger).toHaveAttribute("aria-expanded", "false");
  });
});
