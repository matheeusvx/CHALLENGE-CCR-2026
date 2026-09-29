import { create } from "zustand";
import { AUTO_ANALYSIS_CONFIG } from "@/lib/map/config";
import type { AnalysisResponse, AutomaticAnalysisResponse, ViewportBounds } from "@/lib/schemas/analyses";

export type AutoAnalysisUIStatus =
  | "disabled"
  | "zoom_required"
  | "invalid_viewport"
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
  setEnabled: (enabled: boolean) => void;
  setUiStatus: (uiStatus: AutoAnalysisUIStatus, reason?: string | null) => void;
  setAnalysisStarted: (spatialKey: string | null, canonicalBounds?: ViewportBounds | null) => void;
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
      ...(!enabled ? { currentSpatialKey: null, canonicalBounds: null, result: null, isCacheHit: false } : {}),
    });
  },

  setUiStatus: (uiStatus, reason = null) => set({ uiStatus, reason }),

  setAnalysisStarted: (spatialKey, canonicalBounds = null) =>
    set({
      uiStatus: "analyzing",
      currentSpatialKey: spatialKey,
      canonicalBounds,
      reason: null,
    }),

  setAnalysisResult: (response) => {
    const isCache = response.status === "cache_hit" || response.cache_hit;
    set({
      uiStatus: isCache ? "cache_hit" : "completed",
      currentSpatialKey: response.spatial_key ?? null,
      canonicalBounds: response.canonical_bounds ?? null,
      result: response.result ?? null,
      isCacheHit: isCache,
      reason: response.reason ?? null,
    });
  },

  setFailed: (reason = null, canonicalBounds) =>
    set((state) => ({
      uiStatus: "failed",
      reason: reason ?? "pipeline_failed",
      canonicalBounds: canonicalBounds !== undefined ? canonicalBounds : state.canonicalBounds,
    })),

  clearResult: () =>
    set({
      currentSpatialKey: null,
      canonicalBounds: null,
      result: null,
      isCacheHit: false,
      reason: null,
      uiStatus: "idle",
    }),
}));
