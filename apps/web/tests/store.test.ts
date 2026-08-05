import { beforeEach, describe, expect, it } from "vitest";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import type { PolygonGeometry } from "@/lib/map/geometry";

const polygon: PolygonGeometry = { type: "Polygon", coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]] };
const validation = { valid: true as const, geometry_type: "Polygon", area_square_meters: 4500, centroid: { longitude: -46.961, latitude: -23.108 }, bounding_box: [-46.962, -23.109, -46.96, -23.107], estimated_sentinel_pixels: 45, warnings: [] };

beforeEach(() => useAnalysisStore.setState({ geometry: null, geometryRevision: 0, geometrySource: null, geometryValidation: null, isGeometryDirty: false, lastValidatedGeometryRevision: null, lastValidatedAt: null }));

describe("estado da geometria", () => {
  it("marca uma geometria nova como alterada", () => { useAnalysisStore.getState().setGeometry(polygon, "drawn"); expect(useAnalysisStore.getState().isGeometryDirty).toBe(true); });
  it("a validacao da revisao atual habilita a analise", () => { const store = useAnalysisStore.getState(); store.setGeometry(polygon, "drawn"); const revision = useAnalysisStore.getState().geometryRevision; store.applyGeometryValidation(validation, revision); expect(isCurrentGeometryValidated(useAnalysisStore.getState())).toBe(true); });
  it("uma edicao invalida a validacao anterior", () => { const store = useAnalysisStore.getState(); store.setGeometry(polygon, "drawn"); store.applyGeometryValidation(validation, useAnalysisStore.getState().geometryRevision); const edited: PolygonGeometry = { ...polygon, coordinates: [[[-46.963, -23.109], ...polygon.coordinates[0].slice(1)]] }; store.setGeometry(edited, "drawn"); expect(isCurrentGeometryValidated(useAnalysisStore.getState())).toBe(false); expect(useAnalysisStore.getState().geometryValidation).toBeNull(); });
  it("ignora uma resposta de validacao atrasada", () => { const store = useAnalysisStore.getState(); store.setGeometry(polygon, "drawn"); const oldRevision = useAnalysisStore.getState().geometryRevision; store.setGeometry(polygon, "pasted"); store.applyGeometryValidation(validation, oldRevision); expect(useAnalysisStore.getState().geometryValidation).toBeNull(); });
  it("limpa geometria e metadados de validacao", () => { const store = useAnalysisStore.getState(); store.setGeometry(polygon, "uploaded"); store.clearGeometry(); expect(useAnalysisStore.getState().geometry).toBeNull(); expect(useAnalysisStore.getState().geometrySource).toBeNull(); });
});
