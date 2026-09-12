import { create } from "zustand";
import type { AnalysisResponse, GeometryValidation } from "@/lib/schemas/analyses";
import type { AlertMapTarget } from "@/lib/schemas/alerts";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, type MapStyleId, type MapViewport } from "@/lib/map/config";
import type { PolygonGeometry } from "@/lib/map/geometry";

export type GeometrySource = "drawn" | "pasted" | "uploaded" | "predefined";
export type MapTool = "navigate" | "draw" | "edit";
export type WorkspaceTab = "area" | "result";

export type CurrentAnalysisResult = {
  response: AnalysisResponse;
  geometryRevision: number;
};

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
  alertTarget: AlertMapTarget | null;
  alertFitRequestId: number;
  activeTab: WorkspaceTab;
  currentResult: CurrentAnalysisResult | null;
  setGeometry: (geometry: PolygonGeometry, source: GeometrySource) => void;
  clearGeometry: () => void;
  resetAnalysisSession: () => void;
  applyAnalysisResult: (response: AnalysisResponse, revision: number) => void;
  applyAutomaticResult: (response: AnalysisResponse, geometry?: PolygonGeometry | null) => void;
  restoreHistoricalAnalysis: (
    geometry: PolygonGeometry | null,
    response: AnalysisResponse,
    validation?: GeometryValidation,
    mapTarget?: AlertMapTarget,
  ) => void;
  focusAlertTarget: (target: AlertMapTarget) => void;
  applyGeometryValidation: (validation: GeometryValidation, revision: number) => void;
  setSelectedTool: (tool: MapTool) => void;
  setActiveMapStyle: (style: MapStyleId) => void;
  setMapViewport: (viewport: MapViewport) => void;
  requestGeometryFit: () => void;
  setField: <K extends AnalysisField>(key: K, value: AnalysisState[K]) => void;
};

type AnalysisField =
  | "geometryText"
  | "activeTab";

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
  alertTarget: null,
  alertFitRequestId: 0,
  activeTab: "area",
  currentResult: null,
  setGeometry: (geometry, source) =>
    set((state) => ({
      geometry,
      geometryRevision: state.geometryRevision + 1,
      geometrySource: source,
      geometryValidation: null,
      isGeometryDirty: true,
      selectedTool: state.selectedTool,
      lastValidatedGeometryRevision: null,
      lastValidatedAt: null,
      activeTab: "area",
      currentResult: null,
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
      geometryText: "",
      activeTab: "area",
      currentResult: null,
    })),
  resetAnalysisSession: () =>
    set((state) => ({
      geometry: null,
      geometryRevision: state.geometryRevision + 1,
      geometrySource: null,
      geometryValidation: null,
      isGeometryDirty: false,
      selectedTool: "navigate",
      lastValidatedGeometryRevision: null,
      lastValidatedAt: null,
      geometryText: "",
      activeTab: "area",
      currentResult: null,
    })),
  applyAnalysisResult: (response, revision) =>
    set((state) =>
      revision === state.geometryRevision &&
      Boolean(state.geometry) &&
      state.geometryValidation?.valid === true &&
      state.lastValidatedGeometryRevision === revision
        ? {
            currentResult: { response, geometryRevision: revision },
            activeTab: "result",
          }
        : {},
    ),
  applyAutomaticResult: (response, geometry) =>
    set((state) => {
      const geometryRevision = state.geometryRevision + 1;
      return {
        geometry: geometry !== undefined ? geometry : state.geometry,
        geometryRevision,
        activeTab: "result",
        currentResult: { response, geometryRevision },
      };
    }),
  restoreHistoricalAnalysis: (geometry, response, validation, mapTarget) =>
    set((state) => {
      const geometryRevision = state.geometryRevision + 1;
      const validated = validation?.valid === true;
      return {
        geometry,
        geometryRevision,
        geometrySource: geometry ? "predefined" : null,
        geometryValidation: validation ?? null,
        isGeometryDirty: false,
        selectedTool: "navigate",
        lastValidatedGeometryRevision: validated ? geometryRevision : null,
        lastValidatedAt: validated ? new Date().toISOString() : null,
        geometryText: geometry ? JSON.stringify(geometry, null, 2) : "",
        activeTab: "result",
        currentResult: { response, geometryRevision },
        alertTarget: mapTarget ?? (geometry ? {
          geometry,
          bounds: null,
          centroid: null,
          road_ref: null,
          road_name: null,
          section_id: null,
        } : null),
        alertFitRequestId: geometry || mapTarget ? state.alertFitRequestId + 1 : state.alertFitRequestId,
      };
    }),
  focusAlertTarget: (target) =>
    set((state) => {
      let polyGeometry: PolygonGeometry | null = null;
      if (target.geometry) {
        if (target.geometry.type === "Polygon" && Array.isArray(target.geometry.coordinates)) {
          polyGeometry = target.geometry as unknown as PolygonGeometry;
        } else if (
          target.geometry.type === "Feature" &&
          typeof target.geometry.geometry === "object" &&
          target.geometry.geometry !== null &&
          (target.geometry.geometry as { type?: string }).type === "Polygon"
        ) {
          polyGeometry = (target.geometry as { geometry: PolygonGeometry }).geometry;
        }
      }

      const nextGeometryRevision = polyGeometry ? state.geometryRevision + 1 : state.geometryRevision;

      return {
        geometry: polyGeometry ?? state.geometry,
        geometryRevision: nextGeometryRevision,
        geometrySource: polyGeometry ? "predefined" : state.geometrySource,
        geometryValidation: null,
        isGeometryDirty: false,
        selectedTool: "navigate",
        lastValidatedGeometryRevision: null,
        lastValidatedAt: null,
        geometryText: polyGeometry ? JSON.stringify(polyGeometry, null, 2) : state.geometryText,
        activeTab: "area",
        currentResult: null,
        alertTarget: target,
        alertFitRequestId: state.alertFitRequestId + 1,
      };
    }),
  applyGeometryValidation: (validation, revision) =>
    set((state) =>
      revision === state.geometryRevision
        ? {
            geometryValidation: validation,
            lastValidatedGeometryRevision: revision,
            lastValidatedAt: new Date().toISOString(),
            isGeometryDirty: false,
            selectedTool: validation.valid ? "navigate" : state.selectedTool,
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

export function getCurrentAnalysisResponse(state: AnalysisState): AnalysisResponse | undefined {
  return state.currentResult?.geometryRevision === state.geometryRevision
    ? state.currentResult.response
    : undefined;
}
