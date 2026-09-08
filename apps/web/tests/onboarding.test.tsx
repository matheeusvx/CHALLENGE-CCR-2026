import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useOnboardingStore, ONBOARDING_TOTAL_STEPS } from "@/stores/onboarding-store";
import { THEME_STORAGE_KEY } from "@/stores/settings-store";
import { ROAD_PREFS_STORAGE_KEY } from "@/stores/road-color-store";
import { OnboardingTour, TOUR_STEPS } from "@/components/layout/onboarding-tour";
import { AppSidebar } from "@/components/layout/app-sidebar";

// ---------------------------------------------------------------------------
// Minimal OnboardingTour renderer for unit tests.
// Tests store logic + DOM integration without full MapLibre.
// ---------------------------------------------------------------------------

function TourTestHarness() {
  const store = useOnboardingStore();
  return (
    <div>
      <span data-testid="tour-active">{String(store.tourActive)}</span>
      <span data-testid="current-step">{store.currentStep}</span>
      <span data-testid="completed">{String(store.onboardingCompleted)}</span>
      <span data-testid="operator-menu-open">{String(store.operatorMenuOpen)}</span>
      {store.tourActive && (
        <div data-testid="tour-dialog" role="dialog" aria-modal="true">
          <span data-testid="step-number">
            {store.currentStep + 1} de {ONBOARDING_TOTAL_STEPS}
          </span>
          {store.currentStep > 0 && (
            <button data-testid="prev-btn" onClick={store.prevStep}>
              Anterior
            </button>
          )}
          {store.currentStep < ONBOARDING_TOTAL_STEPS - 1 ? (
            <button data-testid="next-btn" onClick={store.nextStep}>
              Próximo
            </button>
          ) : (
            <button data-testid="complete-btn" onClick={store.completeTour}>
              Começar agora
            </button>
          )}
          <button data-testid="skip-btn" onClick={store.skipTour}>
            Pular tutorial
          </button>
        </div>
      )}
      <button data-testid="start-btn" onClick={store.startTour}>
        Start
      </button>
      <button data-testid="reset-btn" onClick={store.resetOnboarding}>
        Reset
      </button>
    </div>
  );
}

describe("Onboarding Store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useOnboardingStore.setState({
      onboardingCompleted: false,
      tourActive: false,
      currentStep: 0,
      operatorMenuOpen: false,
    });
  });

  it("primeiro acesso mostra tutorial (onboardingCompleted=false)", () => {
    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("false");
    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("tour-active").textContent).toBe("true");
    expect(screen.getByTestId("tour-dialog")).toBeInTheDocument();
  });

  it("onboardingCompleted=true NÃO mostra tutorial automaticamente", () => {
    window.localStorage.setItem("motiva.onboarding", "true");
    useOnboardingStore.setState({ onboardingCompleted: true, tourActive: false });
    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(screen.queryByTestId("tour-dialog")).not.toBeInTheDocument();
  });

  it("botão Próximo avança etapa", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("0");
    fireEvent.click(screen.getByTestId("next-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("1");
  });

  it("botão Anterior volta etapa", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    fireEvent.click(screen.getByTestId("next-btn"));
    fireEvent.click(screen.getByTestId("next-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("2");
    fireEvent.click(screen.getByTestId("prev-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("1");
  });

  it("Anterior está oculto na primeira etapa", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("0");
    expect(screen.queryByTestId("prev-btn")).not.toBeInTheDocument();
  });

  it("Pular tutorial persiste onboardingCompleted=true e fecha menu", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    useOnboardingStore.getState().setOperatorMenuOpen(true);
    fireEvent.click(screen.getByTestId("skip-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(screen.getByTestId("operator-menu-open").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");
  });

  it("Concluir na última etapa persiste onboardingCompleted=true", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      fireEvent.click(screen.getByTestId("next-btn"));
    }
    expect(screen.getByTestId("current-step").textContent).toBe(String(ONBOARDING_TOTAL_STEPS - 1));
    expect(screen.queryByTestId("next-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("complete-btn")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("complete-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");
  });

  it("persistência após reload: tutorial NÃO aparece se completed=true", () => {
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().completeTour();
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");

    useOnboardingStore.setState({
      onboardingCompleted: window.localStorage.getItem("motiva.onboarding") === "true",
      tourActive: false,
      currentStep: 0,
    });

    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.queryByTestId("tour-dialog")).not.toBeInTheDocument();
  });

  it("Refazer tutorial reinicia na etapa 1", () => {
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().completeTour();

    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("true");

    fireEvent.click(screen.getByTestId("reset-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("false");

    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("tour-active").textContent).toBe("true");
    expect(screen.getByTestId("current-step").textContent).toBe("0");
  });

  it("nenhuma outra configuração é resetada ao resetar onboarding", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    window.localStorage.setItem(
      ROAD_PREFS_STORAGE_KEY,
      JSON.stringify({ colors: { "SP-330": "#ff0000" }, visibility: {} }),
    );
    window.localStorage.setItem("motiva.onboarding", "true");

    useOnboardingStore.getState().resetOnboarding();

    expect(window.localStorage.getItem("motiva.onboarding")).toBe("false");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY)).toContain("#ff0000");
  });

  it("prevStep não vai abaixo de 0", () => {
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().prevStep();
    expect(useOnboardingStore.getState().currentStep).toBe(0);
  });

  it("nextStep na última etapa completa o tour automaticamente", () => {
    useOnboardingStore.getState().startTour();
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      useOnboardingStore.getState().nextStep();
    }
    useOnboardingStore.getState().nextStep();
    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
  });

  it("goToStep respeita os limites entre 0 e ONBOARDING_TOTAL_STEPS - 1", () => {
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().goToStep(5);
    expect(useOnboardingStore.getState().currentStep).toBe(5);

    useOnboardingStore.getState().goToStep(-10);
    expect(useOnboardingStore.getState().currentStep).toBe(0);

    useOnboardingStore.getState().goToStep(99);
    expect(useOnboardingStore.getState().currentStep).toBe(ONBOARDING_TOTAL_STEPS - 1);
  });
});

describe("TOUR_STEPS Definição dos 10 Passos", () => {
  it("TOUR_STEPS tem exatamente 10 etapas", () => {
    expect(TOUR_STEPS).toHaveLength(10);
    expect(ONBOARDING_TOTAL_STEPS).toBe(10);
  });

  it("cobre todos os 10 tópicos exigidos na ordem correta", () => {
    // 1. Boas-vindas
    expect(TOUR_STEPS[0].title).toMatch(/Bem-vindo/i);
    expect(TOUR_STEPS[0].target).toBeNull();
    expect(TOUR_STEPS[0].placement).toBe("center");
    expect(TOUR_STEPS[0].text).toMatch(/Sentinel/i);

    // 2. Mapa / Navegação
    expect(TOUR_STEPS[1].title).toMatch(/Mapa operacional/i);
    expect(TOUR_STEPS[1].target).toBe("map");

    // 3. Análise Automática
    expect(TOUR_STEPS[2].title).toMatch(/Análise automática/i);
    expect(TOUR_STEPS[2].target).toBe("auto-analysis");
    // Não deve conter termos técnicos proibidos
    expect(TOUR_STEPS[2].text).not.toMatch(/tile/i);
    expect(TOUR_STEPS[2].text).not.toMatch(/spatial_key/i);

    // 4. Análise Manual
    expect(TOUR_STEPS[3].title).toMatch(/Análise manual/i);
    expect(TOUR_STEPS[3].target).toBe("drawing-tools");

    // 5. Resultado
    expect(TOUR_STEPS[4].title).toMatch(/Resultado/i);
    expect(TOUR_STEPS[4].target).toBe("result-panel");
    expect(TOUR_STEPS[4].text).toMatch(/multissensor/i);

    // 6. Histórico
    expect(TOUR_STEPS[5].title).toMatch(/Histórico/i);
    expect(TOUR_STEPS[5].target).toBe("nav-history");

    // 7. gu.ia
    expect(TOUR_STEPS[6].title).toMatch(/gu\.ia/i);
    expect(TOUR_STEPS[6].target).toBe("guia-widget");
    expect(TOUR_STEPS[6].text).toMatch(/assistente virtual/i);

    // 8. Conta / Perfil
    expect(TOUR_STEPS[7].title).toMatch(/Conta/i);
    expect(TOUR_STEPS[7].target).toBe("operator-profile");
    expect(TOUR_STEPS[7].text).toMatch(/perfil/i);

    // 9. Configurações
    expect(TOUR_STEPS[8].title).toMatch(/Configurações/i);
    expect(TOUR_STEPS[8].target).toBe("operator-menu-settings");
    expect(TOUR_STEPS[8].autoOpenMenu).toBe("operator");

    // 10. Finalização
    expect(TOUR_STEPS[9].title).toMatch(/Pronto para começar/i);
    expect(TOUR_STEPS[9].target).toBeNull();
    expect(TOUR_STEPS[9].placement).toBe("center");
  });
});

describe("OnboardingTour Componente e Interação", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    useOnboardingStore.setState({
      onboardingCompleted: false,
      tourActive: false,
      currentStep: 0,
      operatorMenuOpen: false,
    });
  });

  it("executa a sequência completa de 10 passos pelo UI real", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 0 });

    render(<OnboardingTour onNavigate={onNavigate} />);

    // Passo 1: Boas-vindas
    expect(screen.getByRole("dialog", { name: TOUR_STEPS[0].title })).toBeInTheDocument();
    expect(screen.getByText("1 de 10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Anterior/i })).not.toBeInTheDocument();

    // Avança passo a passo até o final
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      act(() => {
        fireEvent.click(screen.getByRole("button", { name: /Próximo/i }));
      });
      act(() => {
        vi.advanceTimersByTime(100);
      });
      expect(screen.getByText(`${i + 2} de 10`)).toBeInTheDocument();
    }

    // Passo 10: Finalização com CTA "Começar agora"
    expect(screen.getByRole("dialog", { name: TOUR_STEPS[9].title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Começar agora/i })).toBeInTheDocument();

    // Clica para concluir
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Começar agora/i }));
    });
    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
  });

  it("abre automaticamente o menu do operador no passo 9 (Configurações) e fecha no passo 10", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 7 }); // Passo 8 (Conta)

    render(<OnboardingTour onNavigate={onNavigate} />);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);

    // Avança para o passo 9 (Configurações)
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Próximo/i }));
    });
    expect(useOnboardingStore.getState().currentStep).toBe(8);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(true);

    // Avança para o passo 10 (Finalização)
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Próximo/i }));
    });
    expect(useOnboardingStore.getState().currentStep).toBe(9);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);
  });

  it("fecha o menu do operador ao voltar do passo 9 para o passo 8", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 8 }); // Passo 9 (Configurações)

    render(<OnboardingTour onNavigate={onNavigate} />);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(true);

    // Clica em Anterior para voltar ao passo 8
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Anterior/i }));
    });
    expect(useOnboardingStore.getState().currentStep).toBe(7);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);
  });

  it("botão Pular tutorial fecha o tour e limpa o menu aberto", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 8 }); // Passo 9

    render(<OnboardingTour onNavigate={onNavigate} />);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(true);

    // Clica em Pular tutorial no footer
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Pular tutorial" }));
    });
    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);
  });

  it("botão Fechar (X) no cabeçalho encerra o tutorial e limpa o estado", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 8 });

    render(<OnboardingTour onNavigate={onNavigate} />);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(true);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Fechar tutorial" }));
    });
    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);
  });

  it("tecla Escape pula o tutorial e limpa qualquer estado transitório", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, currentStep: 8 });

    render(<OnboardingTour onNavigate={onNavigate} />);

    const dialog = screen.getByRole("dialog");
    act(() => {
      fireEvent.keyDown(dialog, { key: "Escape" });
    });

    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
    expect(useOnboardingStore.getState().operatorMenuOpen).toBe(false);
  });

  it("não quebra quando target não está montado no DOM (fallback centralizado)", () => {
    const onNavigate = vi.fn();
    // Inicia no passo 3 (auto-analysis) sem nenhum elemento no DOM
    useOnboardingStore.setState({ tourActive: true, currentStep: 2 });

    expect(() => {
      render(<OnboardingTour onNavigate={onNavigate} />);
    }).not.toThrow();

    expect(screen.getByRole("dialog", { name: TOUR_STEPS[2].title })).toBeInTheDocument();
  });

  it("AppSidebar reage a operatorMenuOpen da store durante o tour", () => {
    const onNavigate = vi.fn();
    useOnboardingStore.setState({ tourActive: true, operatorMenuOpen: false });

    render(<AppSidebar activeView="analysis" onNavigate={onNavigate} />);
    expect(screen.queryByTestId("operator-popover-menu")).not.toBeInTheDocument();

    // Ativa abertura pelo tour
    act(() => {
      useOnboardingStore.setState({ operatorMenuOpen: true });
    });
    expect(screen.getByTestId("operator-popover-menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Configurações/i })).toHaveAttribute(
      "data-tour",
      "operator-menu-settings",
    );

    // Desativa fechamento pelo tour
    act(() => {
      useOnboardingStore.setState({ operatorMenuOpen: false });
    });
    expect(screen.queryByTestId("operator-popover-menu")).not.toBeInTheDocument();
  });
});
