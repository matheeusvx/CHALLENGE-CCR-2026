import { create } from "zustand";

export type GuiaDisplayMode = "floating" | "docked" | "expanded";

export const GUIA_DISPLAY_MODE_STORAGE_KEY = "motiva-guia-display-mode";

const DEFAULT_MODE: GuiaDisplayMode = "floating";

export function readStoredDisplayMode(): GuiaDisplayMode {
  if (typeof window === "undefined") return DEFAULT_MODE;
  try {
    const value = window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY);
    if (value === "floating" || value === "docked" || value === "expanded") {
      return value;
    }
  } catch {
    // Ignora erros de leitura de storage
  }
  return DEFAULT_MODE;
}

export type GuiaState = {
  displayMode: GuiaDisplayMode;
  isOpen: boolean;
  setDisplayMode: (mode: GuiaDisplayMode) => void;
  setIsOpen: (isOpen: boolean) => void;
  toggleOpen: () => void;
  resetStore: () => void;
};

export const useGuiaStore = create<GuiaState>((set) => ({
  displayMode: readStoredDisplayMode(),
  isOpen: false,
  setDisplayMode: (displayMode) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(GUIA_DISPLAY_MODE_STORAGE_KEY, displayMode);
      } catch {
        // Ignora erros de escrita de storage
      }
    }
    set({ displayMode });
  },
  setIsOpen: (isOpen) => set({ isOpen }),
  toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),
  resetStore: () => set({ displayMode: readStoredDisplayMode(), isOpen: false }),
}));
