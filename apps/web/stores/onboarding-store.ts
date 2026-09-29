import { create } from "zustand";

const STORAGE_KEY = "motiva.onboarding";

function readCompleted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function persistCompleted(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // ignore storage errors
  }
}

export const ONBOARDING_TOTAL_STEPS = 10;

type OnboardingState = {
  /** Whether the user has completed (or skipped) the onboarding at least once. */
  onboardingCompleted: boolean;
  /** Whether the tour overlay is currently active. */
  tourActive: boolean;
  /** Current step index (0-based, 0..9). */
  currentStep: number;
  /** Whether the operator profile popover menu should be open during the tour. */
  operatorMenuOpen: boolean;
  /** Whether the sidebar should be temporarily expanded during specific tour steps. */
  tourSidebarExpanded: boolean;

  startTour: () => void;
  nextStep: () => void;
  prevStep: () => void;
  goToStep: (step: number) => void;
  completeTour: () => void;
  skipTour: () => void;
  /** Clears the completed flag so the tour can be restarted. */
  resetOnboarding: () => void;
  setOperatorMenuOpen: (open: boolean) => void;
  setTourSidebarExpanded: (expanded: boolean) => void;
};

export const useOnboardingStore = create<OnboardingState>((set) => ({
  onboardingCompleted: readCompleted(),
  tourActive: false,
  currentStep: 0,
  operatorMenuOpen: false,
  tourSidebarExpanded: false,

  startTour: () => set({ tourActive: true, currentStep: 0, operatorMenuOpen: false, tourSidebarExpanded: false }),

  nextStep: () =>
    set((state) => {
      if (state.currentStep >= ONBOARDING_TOTAL_STEPS - 1) {
        // Last step — complete
        persistCompleted(true);
        return { tourActive: false, currentStep: 0, onboardingCompleted: true, operatorMenuOpen: false, tourSidebarExpanded: false };
      }
      return { currentStep: state.currentStep + 1 };
    }),

  prevStep: () =>
    set((state) => ({
      currentStep: Math.max(0, state.currentStep - 1),
    })),

  goToStep: (step) =>
    set({
      currentStep: Math.max(0, Math.min(ONBOARDING_TOTAL_STEPS - 1, step)),
    }),

  completeTour: () => {
    persistCompleted(true);
    set({ tourActive: false, currentStep: 0, onboardingCompleted: true, operatorMenuOpen: false, tourSidebarExpanded: false });
  },

  skipTour: () => {
    persistCompleted(true);
    set({ tourActive: false, currentStep: 0, onboardingCompleted: true, operatorMenuOpen: false, tourSidebarExpanded: false });
  },

  resetOnboarding: () => {
    persistCompleted(false);
    set({ onboardingCompleted: false, currentStep: 0, operatorMenuOpen: false, tourSidebarExpanded: false });
  },

  setOperatorMenuOpen: (operatorMenuOpen) => set({ operatorMenuOpen }),
  setTourSidebarExpanded: (tourSidebarExpanded) => set({ tourSidebarExpanded }),
}));
