import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { PolygonGeometry } from "@/lib/map/geometry";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

export type HistoryEntry = {
  /** Identificador da análise (analysis_id da resposta). */
  id: string;
  /** Momento em que a análise foi registrada no histórico (ISO). */
  savedAt: string;
  response: AnalysisResponse;
  geometry: PolygonGeometry;
};

const MAX_ENTRIES = 100;

type HistoryState = {
  entries: HistoryEntry[];
  addEntry: (response: AnalysisResponse, geometry: PolygonGeometry) => void;
  removeEntry: (id: string) => void;
  clear: () => void;
};

export const useHistoryStore = create<HistoryState>()(
  persist(
    (set) => ({
      entries: [],
      addEntry: (response, geometry) =>
        set((state) => {
          const entry: HistoryEntry = {
            id: response.analysis_id,
            savedAt: new Date().toISOString(),
            response,
            geometry,
          };
          const withoutDuplicate = state.entries.filter((item) => item.id !== entry.id);
          return { entries: [entry, ...withoutDuplicate].slice(0, MAX_ENTRIES) };
        }),
      removeEntry: (id) => set((state) => ({ entries: state.entries.filter((item) => item.id !== id) })),
      clear: () => set({ entries: [] }),
    }),
    {
      name: "motiva.history",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ entries: state.entries }),
    },
  ),
);
