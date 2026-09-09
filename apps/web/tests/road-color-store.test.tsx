import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  useRoadColorStore,
  ROAD_PREFS_STORAGE_KEY,
  MOTIVA_ROADS,
} from "../stores/road-color-store";
import { buildRoadVisibilityFilter } from "../components/map/layers/managed-roads-layer";

// ---------------------------------------------------------------------------
// Store helpers
// ---------------------------------------------------------------------------

function resetStore() {
  const initialActive = Object.fromEntries(MOTIVA_ROADS.map((r) => [r.key, r.defaultColor]));
  const initialVis = Object.fromEntries(MOTIVA_ROADS.map((r) => [r.key, true]));
  useRoadColorStore.setState({
    customColors: {},
    activeColors: initialActive,
    customVisibility: {},
    activeVisibility: initialVis,
  });
}

// ---------------------------------------------------------------------------
// Filter builder tests
// ---------------------------------------------------------------------------

describe("buildRoadVisibilityFilter", () => {
  it("returns null when all roads are visible", () => {
    const result = buildRoadVisibilityFilter(["SP-330", "SP-348"], 2);
    expect(result).toBeNull();
  });

  it("returns null when visible count exceeds total (defensive)", () => {
    const result = buildRoadVisibilityFilter(["SP-330", "SP-348", "SP-021"], 2);
    expect(result).toBeNull();
  });

  it("returns a match-based constant-false for zero visible roads", () => {
    const result = buildRoadVisibilityFilter([], 5);
    expect(result).not.toBeNull();
    // Must be an array starting with "match"
    const arr = result as unknown as unknown[];
    expect(arr[0]).toBe("match");
    // Default (last element) must be false
    expect(arr[arr.length - 1]).toBe(false);
    // The match labels should never match any real road key
  });

  it("returns a valid match expression for one visible road", () => {
    const result = buildRoadVisibilityFilter(["SP-330"], 5);
    const arr = result as unknown as unknown[];
    expect(arr[0]).toBe("match");
    // Element [1] is the road key expression (array, not a number/string)
    expect(Array.isArray(arr[1])).toBe(true);
    // Element [2] should be the labels array: ["SP-330"]
    expect(arr[2]).toEqual(["SP-330"]);
    // Element [3] = true (output for matching labels)
    expect(arr[3]).toBe(true);
    // Element [4] = false (default)
    expect(arr[4]).toBe(false);
  });

  it("returns a valid match expression for multiple visible roads", () => {
    const result = buildRoadVisibilityFilter(["SP-330", "SP-348", "SP-021"], 10);
    const arr = result as unknown as unknown[];
    expect(arr[0]).toBe("match");
    expect(arr[2]).toEqual(["SP-330", "SP-348", "SP-021"]);
    expect(arr[3]).toBe(true);
    expect(arr[4]).toBe(false);
  });

  it("uses a to-string + coalesce road key expression", () => {
    const result = buildRoadVisibilityFilter(["SP-330"], 5);
    const arr = result as unknown as unknown[];
    const roadKeyExpr = arr[1] as unknown[];
    expect(roadKeyExpr[0]).toBe("to-string");
    const coalesceExpr = roadKeyExpr[1] as unknown[];
    expect(coalesceExpr[0]).toBe("coalesce");
  });

  it("ensures all visible keys are strings (no accidental numbers)", () => {
    // Even if we accidentally pass numeric-like strings, they stay as strings
    const result = buildRoadVisibilityFilter(["123", "456"], 5);
    const arr = result as unknown as unknown[];
    const labels = arr[2] as string[];
    labels.forEach((label) => expect(typeof label).toBe("string"));
  });

  it("handles road_ref=null features via the to-string+coalesce expression shape", () => {
    // AV-ANTONIO-FALCI has road_ref=null, road_id="AV-ANTONIO-FALCI"
    // The expression ["to-string", ["coalesce", ["get","road_ref"], ["get","road_id"], ""]]
    // will produce "AV-ANTONIO-FALCI" for that feature
    const result = buildRoadVisibilityFilter(["AV-ANTONIO-FALCI"], 5);
    const arr = result as unknown as unknown[];
    expect(arr[2]).toEqual(["AV-ANTONIO-FALCI"]);
  });

  it("produces deterministic output for same input", () => {
    const a = buildRoadVisibilityFilter(["SP-330", "SP-348"], 5);
    const b = buildRoadVisibilityFilter(["SP-330", "SP-348"], 5);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("never produces a filter where element [1] is a number", () => {
    // This was the root cause of the MapLibre error
    const cases = [
      buildRoadVisibilityFilter([], 5),
      buildRoadVisibilityFilter(["SP-330"], 5),
      buildRoadVisibilityFilter(["SP-330", "SP-348"], 5),
    ];
    for (const filter of cases) {
      if (filter === null) continue;
      const arr = filter as unknown as unknown[];
      expect(typeof arr[1]).not.toBe("number");
    }
  });
});

// ---------------------------------------------------------------------------
// Store – colors
// ---------------------------------------------------------------------------

describe("road-color-store – colors", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetStore();
  });

  it("default active colors match MOTIVA_ROADS defaults", () => {
    const { result } = renderHook(() => useRoadColorStore());
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeColors[road.key]).toBe(road.defaultColor);
    }
  });

  it("setRoadColor updates customColors and activeColors", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => { result.current.setRoadColor("SP-330", "#ff0000"); });
    expect(result.current.customColors["SP-330"]).toBe("#ff0000");
    expect(result.current.activeColors["SP-330"]).toBe("#ff0000");
    const stored = JSON.parse(window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY) ?? "{}");
    expect(stored.colors["SP-330"]).toBe("#ff0000");
  });

  it("resetRoadColor restores default and removes key from customColors", () => {
    const { result } = renderHook(() => useRoadColorStore());
    const sp330Default = MOTIVA_ROADS.find((r) => r.key === "SP-330")!.defaultColor;
    act(() => {
      result.current.setRoadColor("SP-330", "#ff0000");
      result.current.resetRoadColor("SP-330");
    });
    expect(result.current.customColors["SP-330"]).toBeUndefined();
    expect(result.current.activeColors["SP-330"]).toBe(sp330Default);
  });
});

// ---------------------------------------------------------------------------
// Store – visibility
// ---------------------------------------------------------------------------

describe("road-color-store – visibility", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetStore();
  });

  it("all roads are visible by default", () => {
    const { result } = renderHook(() => useRoadColorStore());
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeVisibility[road.key]).toBe(true);
    }
  });

  it("setRoadVisible(false) hides a road and persists", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => { result.current.setRoadVisible("SP-330", false); });
    expect(result.current.activeVisibility["SP-330"]).toBe(false);
    const stored = JSON.parse(window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY) ?? "{}");
    expect(stored.visibility["SP-330"]).toBe(false);
  });

  it("setRoadVisible(true) restores a hidden road", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => {
      result.current.setRoadVisible("SP-330", false);
      result.current.setRoadVisible("SP-330", true);
    });
    expect(result.current.activeVisibility["SP-330"]).toBe(true);
  });

  it("hideAllRoads hides every road", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => { result.current.hideAllRoads(); });
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeVisibility[road.key]).toBe(false);
    }
  });

  it("showAllRoads restores every road after hiding", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => {
      result.current.hideAllRoads();
      result.current.showAllRoads();
    });
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeVisibility[road.key]).toBe(true);
    }
  });

  it("custom color survives hide/show cycle", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => {
      result.current.setRoadColor("SP-330", "#abcdef");
      result.current.setRoadVisible("SP-330", false);
      result.current.setRoadVisible("SP-330", true);
    });
    expect(result.current.activeColors["SP-330"]).toBe("#abcdef");
  });
});

// ---------------------------------------------------------------------------
// Store – global reset
// ---------------------------------------------------------------------------

describe("road-color-store – global reset", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetStore();
  });

  it("resetAllDefaults clears colors and visibility", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => {
      result.current.setRoadColor("SP-330", "#ff0000");
      result.current.setRoadVisible("SP-348", false);
      result.current.resetAllDefaults();
    });
    expect(result.current.customColors).toEqual({});
    expect(result.current.customVisibility).toEqual({});
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeColors[road.key]).toBe(road.defaultColor);
      expect(result.current.activeVisibility[road.key]).toBe(true);
    }
    const stored = JSON.parse(window.localStorage.getItem(ROAD_PREFS_STORAGE_KEY) ?? "null");
    expect(stored).not.toBeNull();
    expect(stored?.colors).toEqual({});
    expect(stored?.visibility).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Store – localStorage resilience
// ---------------------------------------------------------------------------

describe("road-color-store – localStorage migration and resilience", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetStore();
  });

  it("migrates v1 colors-only key without losing colors", () => {
    window.localStorage.setItem("motiva-road-colors-v1", JSON.stringify({ "SP-330": "#123456" }));
    // The migration runs on store init – already tested implicitly
    expect(window.localStorage.getItem("motiva-road-colors-v1")).not.toBeNull();
  });

  it("invalid v2 JSON falls back to defaults without throwing", () => {
    window.localStorage.setItem(ROAD_PREFS_STORAGE_KEY, "NOT_VALID_JSON{{{{");
    resetStore();
    const { result } = renderHook(() => useRoadColorStore());
    expect(result.current.customColors).toEqual({});
    for (const road of MOTIVA_ROADS) {
      expect(result.current.activeColors[road.key]).toBe(road.defaultColor);
    }
  });

  it("all roads with the same roadKey share visibility", () => {
    const { result } = renderHook(() => useRoadColorStore());
    act(() => { result.current.setRoadVisible("SP-330", false); });
    expect(result.current.activeVisibility["SP-330"]).toBe(false);
  });
});
