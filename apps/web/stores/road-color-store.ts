import { create } from "zustand";

export type RoadMetadata = {
  key: string;
  name: string;
  ref: string | null;
  defaultColor: string;
};

export const MOTIVA_ROADS: RoadMetadata[] = [
  { key: "SP-330", name: "Rodovia Anhanguera", ref: "SP-330", defaultColor: "#9333ea" },
  { key: "SP-348", name: "Rodovia dos Bandeirantes", ref: "SP-348", defaultColor: "#16a34a" },
  { key: "SP-021", name: "Rodoanel Mário Covas - Trecho Oeste", ref: "SP-021", defaultColor: "#2563eb" },
  { key: "SP-280", name: "Rodovia Presidente Castello Branco", ref: "SP-280", defaultColor: "#dc2626" },
  { key: "SP-270", name: "Rodovia Raposo Tavares", ref: "SP-270", defaultColor: "#ea580c" },
  { key: "SP-300", name: "Rodovia Dom Gabriel Paulino Bueno Couto", ref: "SP-300", defaultColor: "#0d9488" },
  { key: "SP-075", name: "Rodovia Senador José Ermírio de Moraes (Castelinho)", ref: "SP-075", defaultColor: "#d946ef" },
  { key: "SP-079", name: "Raimundo Antunes Soares / Padre Guilherme Hovel-Svd / Tenente Celestino Américo", ref: "SP-079", defaultColor: "#0284c7" },
  { key: "SP-340", name: "Campinas–Mococa", ref: "SP-340", defaultColor: "#c026d3" },
  { key: "SP-342", name: "Mogi Guaçu–Águas da Prata", ref: "SP-342", defaultColor: "#be123c" },
  { key: "SP-344", name: "Aguaí–Vargem Grande do Sul", ref: "SP-344", defaultColor: "#b45309" },
  { key: "SP-350", name: "Casa Branca–São José do Rio Pardo", ref: "SP-350", defaultColor: "#4d7c0f" },
  { key: "SP-215", name: "Vargem Grande do Sul–Casa Branca", ref: "SP-215", defaultColor: "#15803d" },
  { key: "SP-250", name: "Bunjiro Nakao / José de Carvalho / Nestor Fogaça", ref: "SP-250", defaultColor: "#a21caf" },
  { key: "SP-264", name: "Rodovia João Leme dos Santos / Francisco José Ayub", ref: "SP-264", defaultColor: "#1d4ed8" },
  { key: "SP-127", name: "Rodovia SP-127", ref: "SP-127", defaultColor: "#b91c1c" },
  { key: "SP-255", name: "Rodovia João Mellão", ref: "SP-255", defaultColor: "#c2410c" },
  { key: "SP-258", name: "Rodovia Francisco Alves Negrão", ref: "SP-258", defaultColor: "#0f766e" },
  { key: "SPI-102/330", name: "Rodovia Adalberto Panzan", ref: "SPI-102/330", defaultColor: "#4338ca" },
  { key: "SPA-053/280", name: "Rodovia Prefeito Livio Tagliassachi", ref: "SPA-053/280", defaultColor: "#be185d" },
  { key: "SPA-103/079", name: "Rodovia Doutor Miguel Affonso Ferreira de Castilho", ref: "SPA-103/079", defaultColor: "#0369a1" },
  { key: "SPA-104/079", name: "Rodovia João Guimarães", ref: "SPA-104/079", defaultColor: "#a16207" },
  { key: "SPA-160/250", name: "Rodovia José de Almeida Rosa", ref: "SPA-160/250", defaultColor: "#86198f" },
  { key: "SPI-091/270", name: "Rodovia Doutor Celso Charuri", ref: "SPI-091/270", defaultColor: "#b45309" },
  { key: "AV-ANTONIO-FALCI", name: "Avenida Antonio Falci - Ibiúna", ref: null, defaultColor: "#475569" },
];

// ---------------------------------------------------------------------------
// Storage – v2 schema persists both colors and visibility together.
// On first load we migrate any existing v1 (colors-only) data gracefully.
// ---------------------------------------------------------------------------

export const ROAD_PREFS_STORAGE_KEY = "motiva-road-prefs-v2";
/** Legacy key written by the previous implementation – used for migration. */
const LEGACY_COLORS_KEY = "motiva-road-colors-v1";
export const FALLBACK_ROAD_COLOR = "#64748b";

type PersistedPrefs = {
  colors: Record<string, string>;
  visibility: Record<string, boolean>;
};

function defaultPersistedPrefs(): PersistedPrefs {
  return { colors: {}, visibility: {} };
}

function readPersistedPrefs(): PersistedPrefs {
  if (typeof window === "undefined") return defaultPersistedPrefs();

  // Try reading v2 first
  try {
    const raw = window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (
        parsed &&
        typeof parsed === "object" &&
        typeof parsed.colors === "object" &&
        typeof parsed.visibility === "object"
      ) {
        return { colors: parsed.colors ?? {}, visibility: parsed.visibility ?? {} };
      }
    }
  } catch {
    // fall through to migration / defaults
  }

  // Migration: if the old v1 key exists, lift colors forward and remove it.
  try {
    const legacyRaw = window.localStorage.getItem(LEGACY_COLORS_KEY);
    if (legacyRaw) {
      const legacyColors = JSON.parse(legacyRaw);
      if (legacyColors && typeof legacyColors === "object") {
        console.info("[road-color-store] Migrando preferências de cores do formato v1 para v2.");
        window.localStorage.removeItem(LEGACY_COLORS_KEY);
        const migrated: PersistedPrefs = { colors: legacyColors, visibility: {} };
        window.localStorage.setItem(ROAD_PREFS_STORAGE_KEY, JSON.stringify(migrated));
        return migrated;
      }
    }
  } catch {
    // ignore – safe default below
  }

  return defaultPersistedPrefs();
}

function persistPrefs(prefs: PersistedPrefs): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ROAD_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // ignore storage errors
  }
}

// ---------------------------------------------------------------------------
// Derived state helpers
// ---------------------------------------------------------------------------

function computeActiveColors(customColors: Record<string, string>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const road of MOTIVA_ROADS) {
    result[road.key] = customColors[road.key] ?? road.defaultColor;
  }
  return result;
}

function computeActiveVisibility(customVisibility: Record<string, boolean>): Record<string, boolean> {
  const result: Record<string, boolean> = {};
  for (const road of MOTIVA_ROADS) {
    result[road.key] = customVisibility[road.key] !== false; // default = true
  }
  return result;
}

// ---------------------------------------------------------------------------
// Zustand store
// ---------------------------------------------------------------------------

export type RoadColorState = {
  /** Custom color overrides: only entries that differ from the default. */
  customColors: Record<string, string>;
  /** Merged active color map (default + overrides) for all known roads. */
  activeColors: Record<string, string>;
  /** Custom visibility overrides: only entries explicitly set to false. */
  customVisibility: Record<string, boolean>;
  /** Merged visibility map (default = true + overrides) for all known roads. */
  activeVisibility: Record<string, boolean>;

  // Color actions
  setRoadColor: (roadKey: string, color: string) => void;
  resetRoadColor: (roadKey: string) => void;

  // Visibility actions
  setRoadVisible: (roadKey: string, visible: boolean) => void;
  toggleRoadVisibility: (roadKey: string) => void;
  showAllRoads: () => void;
  hideAllRoads: () => void;

  // Global reset
  resetAllDefaults: () => void;
};

export const useRoadColorStore = create<RoadColorState>((set, get) => {
  const initial = readPersistedPrefs();

  const save = (customColors: Record<string, string>, customVisibility: Record<string, boolean>) => {
    persistPrefs({ colors: customColors, visibility: customVisibility });
  };

  return {
    customColors: initial.colors,
    activeColors: computeActiveColors(initial.colors),
    customVisibility: initial.visibility,
    activeVisibility: computeActiveVisibility(initial.visibility),

    // -------- Color actions --------
    setRoadColor: (roadKey, color) =>
      set((state) => {
        const nextCustom = { ...state.customColors, [roadKey]: color };
        save(nextCustom, state.customVisibility);
        return { customColors: nextCustom, activeColors: computeActiveColors(nextCustom) };
      }),

    resetRoadColor: (roadKey) =>
      set((state) => {
        const nextCustom = { ...state.customColors };
        delete nextCustom[roadKey];
        save(nextCustom, state.customVisibility);
        return { customColors: nextCustom, activeColors: computeActiveColors(nextCustom) };
      }),

    // -------- Visibility actions --------
    setRoadVisible: (roadKey, visible) =>
      set((state) => {
        // Store only false values to keep the persisted object small.
        const nextVis = { ...state.customVisibility };
        if (visible) {
          delete nextVis[roadKey]; // default is visible → no need to store
        } else {
          nextVis[roadKey] = false;
        }
        save(state.customColors, nextVis);
        return { customVisibility: nextVis, activeVisibility: computeActiveVisibility(nextVis) };
      }),

    toggleRoadVisibility: (roadKey) => {
      const current = get().activeVisibility[roadKey] !== false;
      get().setRoadVisible(roadKey, !current);
    },

    showAllRoads: () =>
      set((state) => {
        save(state.customColors, {});
        return { customVisibility: {}, activeVisibility: computeActiveVisibility({}) };
      }),

    hideAllRoads: () =>
      set((state) => {
        const allHidden: Record<string, boolean> = {};
        for (const road of MOTIVA_ROADS) allHidden[road.key] = false;
        save(state.customColors, allHidden);
        return { customVisibility: allHidden, activeVisibility: computeActiveVisibility(allHidden) };
      }),

    // -------- Global reset --------
    resetAllDefaults: () =>
      set(() => {
        persistPrefs({ colors: {}, visibility: {} });
        return {
          customColors: {},
          activeColors: computeActiveColors({}),
          customVisibility: {},
          activeVisibility: computeActiveVisibility({}),
        };
      }),
  };
});
