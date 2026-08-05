import { create } from "zustand";
import type { GeometryValidation } from "@/lib/schemas/analyses";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, type MapStyleId, type MapViewport } from "@/lib/map/config";
import type { PolygonGeometry } from "@/lib/map/geometry";

export type GeometrySource = "drawn" | "pasted" | "uploaded" | "predefined";
export type MapTool = "navigate" | "draw" | "edit";
export type WorkspaceTab = "area" | "parameters" | "result";

type AnalysisState = {
  geometry: PolygonGeometry | null;
  geometryRevision: number;
  geometrySource: GeometrySource | null;
  geometryValidation: GeometryValidation | null;
  isGeometryDirty: boolean;
  selectedTool: MapTool;
  activeMapStyle: MapStyleId;
  mapViewport: MapViewport;
  lastValidatedGeometryRevision: number | null;
  lastValidatedAt: string | null;
  geometryText: string;
  fitRequestId: number;
  activeTab: WorkspaceTab;
  startDate: string;
  endDate: string;
  maxCloudCover: number;
  maxScenes: number;
  minValidPixelPercentage: number;
  dailyAggregation: "best" | "median" | "none";
  setGeometry: (geometry: PolygonGeometry, source: GeometrySource) => void;
  clearGeometry: () => void;
  applyGeometryValidation: (validation: GeometryValidation, revision: number) => void;
  setSelectedTool: (tool: MapTool) => void;
  setActiveMapStyle: (style: MapStyleId) => void;
  setMapViewport: (viewport: MapViewport) => void;
  requestGeometryFit: () => void;
  setField: <K extends AnalysisField>(key: K, value: AnalysisState[K]) => void;
};

type AnalysisField =
  | "geometryText"
  | "activeTab"
  | "startDate"
  | "endDate"
  | "maxCloudCover"
  | "maxScenes"
  | "minValidPixelPercentage"
  | "dailyAggregation";

export const useAnalysisStore = create<AnalysisState>((set) => ({
  geometry: null,
  geometryRevision: 0,
  geometrySource: null,
  geometryValidation: null,
  isGeometryDirty: false,
  selectedTool: "navigate",
  activeMapStyle: DEFAULT_MAP_STYLE_ID,
  mapViewport: { ...MAP_CONFIG.initialViewport },
  lastValidatedGeometryRevision: null,
  lastValidatedAt: null,
  geometryText: "",
  fitRequestId: 0,
  activeTab: "area",
  startDate: "2026-05-01",
  endDate: "2026-08-04",
  maxCloudCover: 30,
  maxScenes: 12,
  minValidPixelPercentage: 70,
  dailyAggregation: "best",
  setGeometry: (geometry, source) =>
    set((state) => ({
      geometry,
      geometryRevision: state.geometryRevision + 1,
      geometrySource: source,
      geometryValidation: null,
      isGeometryDirty: true,
      selectedTool: state.selectedTool,
    })),
  clearGeometry: () =>
    set((state) => ({
      geometry: null,
      geometryRevision: state.geometryRevision + 1,
      geometrySource: null,
      geometryValidation: null,
      isGeometryDirty: false,
      selectedTool: "navigate",
      lastValidatedGeometryRevision: null,
      lastValidatedAt: null,
    })),
  applyGeometryValidation: (validation, revision) =>
    set((state) =>
      revision === state.geometryRevision
        ? {
            geometryValidation: validation,
            lastValidatedGeometryRevision: revision,
            lastValidatedAt: new Date().toISOString(),
            isGeometryDirty: false,
          }
        : {},
    ),
  setSelectedTool: (selectedTool) => set({ selectedTool }),
  setActiveMapStyle: (activeMapStyle) => set({ activeMapStyle }),
  setMapViewport: (mapViewport) => set({ mapViewport }),
  requestGeometryFit: () => set((state) => ({ fitRequestId: state.fitRequestId + 1 })),
  setField: (key, value) => set({ [key]: value } as unknown as Pick<AnalysisState, AnalysisField>),
}));

export function isCurrentGeometryValidated(state: AnalysisState): boolean {
  return Boolean(
    state.geometry &&
      state.geometryValidation?.valid &&
      state.geometryRevision === state.lastValidatedGeometryRevision,
  );
}
