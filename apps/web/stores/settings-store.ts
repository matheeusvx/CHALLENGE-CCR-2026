import { create } from "zustand";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "motiva.theme";
const DEFAULT_PREFERENCE: ResolvedTheme = "dark";

function readStoredPreference(): ThemePreference {
  if (typeof window === "undefined") return DEFAULT_PREFERENCE;
  const value = window.localStorage.getItem(THEME_STORAGE_KEY);
  return value === "light" || value === "dark" || value === "system" ? value : DEFAULT_PREFERENCE;
}

type SettingsState = {
  themePreference: ThemePreference;
  notificationsEnabled: boolean;
  setThemePreference: (preference: ThemePreference) => void;
  setNotificationsEnabled: (enabled: boolean) => void;
};

export const useSettingsStore = create<SettingsState>((set) => ({
  themePreference: readStoredPreference(),
  notificationsEnabled: true,
  setThemePreference: (themePreference) => {
    if (typeof window !== "undefined") window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    set({ themePreference });
  },
  setNotificationsEnabled: (notificationsEnabled) => set({ notificationsEnabled }),
}));

/** Resolve a preferência (inclusive "system") para um tema concreto. */
export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== "system") return preference;
  if (typeof window === "undefined") return DEFAULT_PREFERENCE;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
