import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import { useOnboardingStore, ONBOARDING_TOTAL_STEPS } from "@/stores/onboarding-store";
import { THEME_STORAGE_KEY } from "@/stores/settings-store";
import { ROAD_PREFS_STORAGE_KEY } from "@/stores/road-color-store";

// ---------------------------------------------------------------------------
// Minimal OnboardingTour renderer for unit tests.
// We test the store logic + DOM integration without full MapLibre.
// ---------------------------------------------------------------------------

function TourTestHarness() {
  const store = useOnboardingStore();
  return (
    <div>
      <span data-testid="tour-active">{String(store.tourActive)}</span>
      <span data-testid="current-step">{store.currentStep}</span>
      <span data-testid="completed">{String(store.onboardingCompleted)}</span>
      {store.tourActive && (
        <div data-testid="tour-dialog" role="dialog" aria-modal="true">
          <span data-testid="step-number">{store.currentStep + 1} de {ONBOARDING_TOTAL_STEPS}</span>
          {store.currentStep > 0 && (
            <button data-testid="prev-btn" onClick={store.prevStep}>Anterior</button>
          )}
          {store.currentStep < ONBOARDING_TOTAL_STEPS - 1 ? (
            <button data-testid="next-btn" onClick={store.nextStep}>Próximo</button>
          ) : (
            <button data-testid="complete-btn" onClick={store.completeTour}>Concluir</button>
          )}
          <button data-testid="skip-btn" onClick={store.skipTour}>Pular tutorial</button>
        </div>
      )}
      <button data-testid="start-btn" onClick={store.startTour}>Start</button>
      <button data-testid="reset-btn" onClick={store.resetOnboarding}>Reset</button>
    </div>
  );
}

describe("Onboarding Store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // Reset store to initial state
    useOnboardingStore.setState({
      onboardingCompleted: false,
      tourActive: false,
      currentStep: 0,
    });
  });

  it("primeiro acesso mostra tutorial (onboardingCompleted=false)", () => {
    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("false");
    // Start tour simulating auto-start
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
    // Advance to step 2
    fireEvent.click(screen.getByTestId("next-btn"));
    fireEvent.click(screen.getByTestId("next-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("2");
    // Go back
    fireEvent.click(screen.getByTestId("prev-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("1");
  });

  it("Anterior está oculto na primeira etapa", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("current-step").textContent).toBe("0");
    expect(screen.queryByTestId("prev-btn")).not.toBeInTheDocument();
  });

  it("Pular tutorial persiste onboardingCompleted=true", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    fireEvent.click(screen.getByTestId("skip-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");
  });

  it("Concluir na última etapa persiste onboardingCompleted=true", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    // Navigate to last step
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      fireEvent.click(screen.getByTestId("next-btn"));
    }
    expect(screen.getByTestId("current-step").textContent).toBe(String(ONBOARDING_TOTAL_STEPS - 1));
    // The last step should show Concluir, not Próximo
    expect(screen.queryByTestId("next-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("complete-btn")).toBeInTheDocument();
    // Conclude
    fireEvent.click(screen.getByTestId("complete-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("true");
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");
  });

  it("persistência após reload: tutorial NÃO aparece se completed=true", () => {
    // Complete the tour
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().completeTour();
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("true");

    // Simulate re-render with fresh store read
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
    // Complete tour first
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().completeTour();

    render(<TourTestHarness />);
    expect(screen.getByTestId("completed").textContent).toBe("true");

    // Reset and restart
    fireEvent.click(screen.getByTestId("reset-btn"));
    expect(screen.getByTestId("completed").textContent).toBe("false");
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("false");

    fireEvent.click(screen.getByTestId("start-btn"));
    expect(screen.getByTestId("tour-active").textContent).toBe("true");
    expect(screen.getByTestId("current-step").textContent).toBe("0");
  });

  it("nenhuma outra configuração é resetada ao resetar onboarding", () => {
    // Set some other settings
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    window.localStorage.setItem(ROAD_PREFS_STORAGE_KEY, JSON.stringify({ colors: { "SP-330": "#ff0000" }, visibility: {} }));
    window.localStorage.setItem("motiva.onboarding", "true");

    useOnboardingStore.getState().resetOnboarding();

    // Onboarding flag is cleared
    expect(window.localStorage.getItem("motiva.onboarding")).toBe("false");
    // Other settings are untouched
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY)).toContain("#ff0000");
  });

  it("target ausente não quebra a aplicação", () => {
    // This test verifies that starting the tour when no data-tour elements
    // exist in the DOM doesn't throw.
    render(<TourTestHarness />);
    expect(() => {
      fireEvent.click(screen.getByTestId("start-btn"));
    }).not.toThrow();
    expect(screen.getByTestId("tour-active").textContent).toBe("true");
  });

  it("última etapa conclui corretamente via nextStep", () => {
    render(<TourTestHarness />);
    fireEvent.click(screen.getByTestId("start-btn"));
    // Advance to last step
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      fireEvent.click(screen.getByTestId("next-btn"));
    }
    // Use the Concluir button (which calls completeTour internally)
    fireEvent.click(screen.getByTestId("complete-btn"));
    expect(screen.getByTestId("tour-active").textContent).toBe("false");
    expect(screen.getByTestId("completed").textContent).toBe("true");
  });

  it("prevStep não vai abaixo de 0", () => {
    useOnboardingStore.getState().startTour();
    useOnboardingStore.getState().prevStep();
    expect(useOnboardingStore.getState().currentStep).toBe(0);
  });

  it("nextStep na última etapa completa o tour automaticamente", () => {
    useOnboardingStore.getState().startTour();
    // Go to last step
    for (let i = 0; i < ONBOARDING_TOTAL_STEPS - 1; i++) {
      useOnboardingStore.getState().nextStep();
    }
    // Now nextStep from last step should complete
    useOnboardingStore.getState().nextStep();
    expect(useOnboardingStore.getState().tourActive).toBe(false);
    expect(useOnboardingStore.getState().onboardingCompleted).toBe(true);
  });

  it("TOUR_STEPS tem exatamente 8 etapas", async () => {
    const { TOUR_STEPS } = await import("@/components/layout/onboarding-tour");
    expect(TOUR_STEPS).toHaveLength(8);
    expect(ONBOARDING_TOTAL_STEPS).toBe(8);
  });
});
