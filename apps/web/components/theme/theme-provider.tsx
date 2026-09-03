"use client";

import { useEffect, type ReactNode } from "react";
import { resolveTheme, useSettingsStore } from "@/stores/settings-store";

/**
 * Aplica o tema resolvido ao elemento <html> via data-theme e mantém a
 * sincronia com a preferência do usuário. O tema inicial já é definido por
 * um script inline no <body> (ver layout) para evitar flash na primeira pintura.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const preference = useSettingsStore((state) => state.themePreference);

  useEffect(() => {
    const apply = () => {
      document.documentElement.setAttribute("data-theme", resolveTheme(preference));
    };
    apply();
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preference]);

  return children;
}
