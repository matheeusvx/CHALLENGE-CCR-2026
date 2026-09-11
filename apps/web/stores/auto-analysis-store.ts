import { create } from "zustand";
import { AUTO_ANALYSIS_CONFIG } from "@/lib/map/config";
import type { AnalysisResponse, AutomaticAnalysisResponse, ViewportBounds } from "@/lib/schemas/analyses";

export type AutoAnalysisUIStatus =
  | "disabled"
  | "zoom_required"
  | "invalid_viewport"
  | "road_context_required"
  | "road_not_found"
  | "road_ambiguous"
  | "idle"
  | "stabilizing"
  | "analyzing"
  | "cache_hit"
  | "completed"
  | "failed";

function readStoredAutoEnabled(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(AUTO_ANALYSIS_CONFIG.storageKey) === "true";
  } catch {
    return false;
  }
}

type AutoAnalysisState = {
  enabled: boolean;
  uiStatus: AutoAnalysisUIStatus;
  currentSpatialKey: string | null;
  canonicalBounds: ViewportBounds | null;
  result: AnalysisResponse | null;
  isCacheHit: boolean;
  reason: string | null;
  road: { id?: string; ref?: string; name?: string } | null;
  analyzedGeometry: Record<string, unknown> | null;
  centerline: Record<string, unknown> | null;
  sideAGeometry: Record<string, unknown> | null;
  sideBGeometry: Record<string, unknown> | null;
  spatialStrategy: string | null;
  setEnabled: (enabled: boolean) => void;
  setUiStatus: (uiStatus: AutoAnalysisUIStatus, reason?: string | null) => void;
  setAnalysisStarted: (
    spatialKey: string | null,
    canonicalBounds?: ViewportBounds | null,
    roadsideDetails?: {
      road?: { id?: string; ref?: string; name?: string } | null;
      analyzedGeometry?: Record<string, unknown> | null;
      centerline?: Record<string, unknown> | null;
      sideAGeometry?: Record<string, unknown> | null;
      sideBGeometry?: Record<string, unknown> | null;
      spatialStrategy?: string | null;
    },
  ) => void;
  setAnalysisResult: (response: AutomaticAnalysisResponse) => void;
  setFailed: (reason?: string | null, canonicalBounds?: ViewportBounds | null) => void;
  clearResult: () => void;
};

export const useAutoAnalysisStore = create<AutoAnalysisState>((set) => ({
  enabled: readStoredAutoEnabled(),
  uiStatus: readStoredAutoEnabled() ? "idle" : "disabled",
  currentSpatialKey: null,
  canonicalBounds: null,
  result: null,
  isCacheHit: false,
  reason: null,
  road: null,
  analyzedGeometry: null,
  centerline: null,
  sideAGeometry: null,
  sideBGeometry: null,
  spatialStrategy: null,

  setEnabled: (enabled) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(AUTO_ANALYSIS_CONFIG.storageKey, String(enabled));
      } catch {
        // ignore localStorage access issues
      }
    }
    set({
      enabled,
      uiStatus: enabled ? "idle" : "disabled",
      reason: null,
      ...(!enabled
        ? {
            currentSpatialKey: null,
            canonicalBounds: null,
            result: null,
            isCacheHit: false,
            road: null,
            analyzedGeometry: null,
            centerline: null,
            sideAGeometry: null,
            sideBGeometry: null,
            spatialStrategy: null,
          }
        : {}),
    });
  },

  setUiStatus: (uiStatus, reason = null) =>
    set(() => {
      const clearsGeometries = [
        "disabled",
        "zoom_required",
        "invalid_viewport",
        "road_context_required",
        "road_not_found",
        "road_ambiguous",
        "idle",
      ].includes(uiStatus);

      return {
        uiStatus,
        reason,
        ...(clearsGeometries
          ? {
              road: null,
              analyzedGeometry: null,
              centerline: null,
              sideAGeometry: null,
              sideBGeometry: null,
              canonicalBounds: null,
            }
          : {}),
      };
    }),

  setAnalysisStarted: (spatialKey, canonicalBounds = null, roadsideDetails) =>
    set({
      uiStatus: "analyzing",
      currentSpatialKey: spatialKey,
      canonicalBounds,
      reason: null,
      road: roadsideDetails?.road ?? null,
      analyzedGeometry: roadsideDetails?.analyzedGeometry ?? null,
      centerline: roadsideDetails?.centerline ?? null,
      sideAGeometry: roadsideDetails?.sideAGeometry ?? null,
      sideBGeometry: roadsideDetails?.sideBGeometry ?? null,
      spatialStrategy: roadsideDetails?.spatialStrategy ?? null,
    }),

  setAnalysisResult: (response) => {
    const isCache = response.status === "cache_hit" || response.cache_hit;
    const strategyName =
      typeof response.spatial_strategy === "string"
        ? response.spatial_strategy
        : typeof response.spatial_strategy === "object" && response.spatial_strategy !== null
          ? ((response.spatial_strategy as Record<string, unknown>).name as string) ?? null
          : response.analyzed_geometry
            ? "roadside"
            : null;

    set((state) => {
      const isRoadside = Boolean(
        strategyName?.startsWith("roadside") ||
        response.analyzed_geometry ||
        state.spatialStrategy?.startsWith("roadside"),
      );
      return {
        uiStatus: isCache ? "cache_hit" : "completed",
        currentSpatialKey: response.spatial_key ?? state.currentSpatialKey,
        canonicalBounds: response.canonical_bounds ?? null,
        result: response.result ?? null,
        isCacheHit: isCache,
        // A terminal success always replaces any failure from an older request.
        reason: null,
        road: response.road ?? (isRoadside ? state.road : null),
        analyzedGeometry: response.analyzed_geometry ?? (isRoadside ? state.analyzedGeometry : null),
        centerline: response.centerline ?? (isRoadside ? state.centerline : null),
        sideAGeometry: response.side_a_geometry ?? (isRoadside ? state.sideAGeometry : null),
        sideBGeometry: response.side_b_geometry ?? (isRoadside ? state.sideBGeometry : null),
        spatialStrategy: strategyName ?? (isRoadside ? state.spatialStrategy : null),
      };
    });
  },

  setFailed: (reason = null, canonicalBounds) =>
    set((state) => ({
      uiStatus: "failed",
      reason: reason ?? "pipeline_failed",
      result: null,
      isCacheHit: false,
      canonicalBounds: canonicalBounds !== undefined ? canonicalBounds : state.canonicalBounds,
      road: null,
      analyzedGeometry: null,
      centerline: null,
      sideAGeometry: null,
      sideBGeometry: null,
      spatialStrategy: null,
    })),

  clearResult: () =>
    set({
      currentSpatialKey: null,
      canonicalBounds: null,
      result: null,
      isCacheHit: false,
      reason: null,
      uiStatus: "idle",
      road: null,
      analyzedGeometry: null,
      centerline: null,
      sideAGeometry: null,
      sideBGeometry: null,
      spatialStrategy: null,
    }),
}));
