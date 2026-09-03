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

export const ONBOARDING_TOTAL_STEPS = 8;

type OnboardingState = {
  /** Whether the user has completed (or skipped) the onboarding at least once. */
  onboardingCompleted: boolean;
  /** Whether the tour overlay is currently active. */
  tourActive: boolean;
  /** Current step index (0-based, 0..7). */
  currentStep: number;

  startTour: () => void;
  nextStep: () => void;
  prevStep: () => void;
  completeTour: () => void;
  skipTour: () => void;
  /** Clears the completed flag so the tour can be restarted. */
  resetOnboarding: () => void;
};

export const useOnboardingStore = create<OnboardingState>((set) => ({
  onboardingCompleted: readCompleted(),
  tourActive: false,
  currentStep: 0,

  startTour: () => set({ tourActive: true, currentStep: 0 }),

  nextStep: () =>
    set((state) => {
      if (state.currentStep >= ONBOARDING_TOTAL_STEPS - 1) {
        // Last step — complete
        persistCompleted(true);
        return { tourActive: false, currentStep: 0, onboardingCompleted: true };
      }
      return { currentStep: state.currentStep + 1 };
    }),

  prevStep: () =>
    set((state) => ({
      currentStep: Math.max(0, state.currentStep - 1),
    })),

  completeTour: () => {
    persistCompleted(true);
    set({ tourActive: false, currentStep: 0, onboardingCompleted: true });
  },

  skipTour: () => {
    persistCompleted(true);
    set({ tourActive: false, currentStep: 0, onboardingCompleted: true });
  },

  resetOnboarding: () => {
    persistCompleted(false);
    set({ onboardingCompleted: false, currentStep: 0 });
  },
}));
