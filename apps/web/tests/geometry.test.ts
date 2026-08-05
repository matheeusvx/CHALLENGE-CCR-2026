import { describe, expect, it } from "vitest";
import { calculateGeometryPreview, normalizeGeoJson, parseGeoJsonText } from "@/lib/map/geometry";
import type { PolygonGeometry } from "@/lib/map/geometry";

const polygon: PolygonGeometry = { type: "Polygon", coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]] };

describe("normalizacao GeoJSON", () => {
  it("aceita Polygon", () => expect(normalizeGeoJson(polygon)).toEqual(polygon));
  it("aceita Feature Polygon", () => expect(normalizeGeoJson({ type: "Feature", properties: { name: "AOI" }, geometry: polygon })).toEqual(polygon));
  it("aceita FeatureCollection com uma Feature Polygon", () => expect(normalizeGeoJson({ type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: polygon }] })).toEqual(polygon));
  it("rejeita FeatureCollection com mais de uma feicao", () => expect(() => normalizeGeoJson({ type: "FeatureCollection", features: [{ type: "Feature", geometry: polygon }, { type: "Feature", geometry: polygon }] })).toThrow("unico Polygon"));
  it("rejeita JSON invalido", () => expect(() => parseGeoJsonText("{")).toThrow("JSON valido"));
  it("rejeita coordenadas fora dos limites", () => expect(() => normalizeGeoJson({ type: "Polygon", coordinates: [[[200, -23], [201, -23], [201, -22], [200, -23]]] })).toThrow());
  it("calcula area, centroide, bbox e pixels provisoriamente", () => {
    const preview = calculateGeometryPreview(polygon);
    expect(preview.areaSquareMeters).toBeGreaterThan(0);
    expect(preview.centroid.longitude).toBeCloseTo(-46.961, 3);
    expect(preview.boundingBox).toEqual([-46.962, -23.109, -46.96, -23.107]);
    expect(preview.estimatedSentinelPixels).toBeGreaterThan(0);
  });
});
