import { create } from "zustand";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "motiva.theme";
export const SIDEBAR_STORAGE_KEY = "motiva.sidebar_collapsed";
const DEFAULT_PREFERENCE: ResolvedTheme = "dark";
const DEFAULT_SIDEBAR_COLLAPSED = true;

function readStoredPreference(): ThemePreference {
  if (typeof window === "undefined") return DEFAULT_PREFERENCE;
  const value = window.localStorage.getItem(THEME_STORAGE_KEY);
  return value === "light" || value === "dark" || value === "system" ? value : DEFAULT_PREFERENCE;
}

function readStoredSidebarCollapsed(): boolean {
  if (typeof window === "undefined") return DEFAULT_SIDEBAR_COLLAPSED;
  try {
    const value = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (value === null) return DEFAULT_SIDEBAR_COLLAPSED;
    return value === "true";
  } catch {
    return DEFAULT_SIDEBAR_COLLAPSED;
  }
}

type SettingsState = {
  themePreference: ThemePreference;
  notificationsEnabled: boolean;
  sidebarCollapsed: boolean;
  sidebarHydrated: boolean;
  hydrateSidebarPreference: () => void;
  setThemePreference: (preference: ThemePreference) => void;
  setNotificationsEnabled: (enabled: boolean) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
};

export const useSettingsStore = create<SettingsState>((set, get) => ({
  themePreference: readStoredPreference(),
  notificationsEnabled: true,
  // SSR and the first client render must share this deterministic snapshot.
  // The stored preference is applied explicitly after React mounts.
  sidebarCollapsed: DEFAULT_SIDEBAR_COLLAPSED,
  sidebarHydrated: false,
  hydrateSidebarPreference: () => {
    if (get().sidebarHydrated) return;
    set({
      sidebarCollapsed: readStoredSidebarCollapsed(),
      sidebarHydrated: true,
    });
  },
  setThemePreference: (themePreference) => {
    if (typeof window !== "undefined") window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    set({ themePreference });
  },
  setNotificationsEnabled: (notificationsEnabled) => set({ notificationsEnabled }),
  setSidebarCollapsed: (sidebarCollapsed) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(sidebarCollapsed));
      } catch {
        // ignore storage errors
      }
    }
    set({ sidebarCollapsed, sidebarHydrated: true });
  },
  toggleSidebar: () => {
    const next = !useSettingsStore.getState().sidebarCollapsed;
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      } catch {
        // ignore storage errors
      }
    }
    set({ sidebarCollapsed: next, sidebarHydrated: true });
  },
}));

/** Resolve a preferência (inclusive "system") para um tema concreto. */
export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== "system") return preference;
  if (typeof window === "undefined") return DEFAULT_PREFERENCE;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
