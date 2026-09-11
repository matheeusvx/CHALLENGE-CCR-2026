import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { PolygonGeometry } from "@/lib/map/geometry";
import type { AnalysisResponse, GeometryValidation } from "@/lib/schemas/analyses";

export type HistoryEntry = {
  /** Identificador da análise (analysis_id da resposta). */
  id: string;
  /** Momento em que a análise foi registrada no histórico (ISO). */
  savedAt: string;
  response: AnalysisResponse;
  geometry: PolygonGeometry;
  geometryValidation?: GeometryValidation;
  road?: { id?: string; ref?: string; name?: string } | null;
};

const MAX_ENTRIES = 100;

type HistoryState = {
  entries: HistoryEntry[];
  addEntry: (
    response: AnalysisResponse,
    geometry: PolygonGeometry,
    validation?: GeometryValidation,
    road?: { id?: string; ref?: string; name?: string } | null,
  ) => void;
  removeEntry: (id: string) => void;
  clear: () => void;
};

export const useHistoryStore = create<HistoryState>()(
  persist(
    (set) => ({
      entries: [],
      addEntry: (response, geometry, geometryValidation, road) =>
        set((state) => {
          const entry: HistoryEntry = {
            id: response.analysis_id,
            savedAt: new Date().toISOString(),
            response,
            geometry,
            geometryValidation,
            road: road ?? (response as { road?: { id?: string; ref?: string; name?: string } }).road ?? null,
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
