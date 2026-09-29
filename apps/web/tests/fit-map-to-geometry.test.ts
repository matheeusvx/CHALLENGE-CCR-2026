import { describe, expect, it, vi } from "vitest";
import { fitMapToGeometry } from "@/lib/map/fit-map-to-geometry";
import type { PolygonGeometry } from "@/lib/map/geometry";

const louveira: PolygonGeometry = {
  type: "Polygon",
  coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]],
};

describe("fitMapToGeometry", () => {
  it("enquadra a geometria com padding operacional e zoom limitado", () => {
    const fitBounds = vi.fn();
    expect(fitMapToGeometry({ fitBounds } as never, louveira)).toBe(true);
    const [bounds, options] = fitBounds.mock.calls[0];
    expect(bounds[0][0]).toBeCloseTo(-46.962);
    expect(bounds[0][1]).toBeCloseTo(-23.109);
    expect(bounds[1][0]).toBeCloseTo(-46.96);
    expect(bounds[1][1]).toBeCloseTo(-23.107);
    expect(options).toEqual(expect.objectContaining({ maxZoom: 16, duration: 700, padding: { top: 80, right: 180, bottom: 80, left: 80 } }));
  });

  it("expande caixas degeneradas sem inventar uma geometria", () => {
    const fitBounds = vi.fn();
    const degenerate: PolygonGeometry = { type: "Polygon", coordinates: [[[1, 1], [1, 1], [1, 1], [1, 1]]] };
    expect(fitMapToGeometry({ fitBounds } as never, degenerate, { duration: 0 })).toBe(true);
    const bounds = fitBounds.mock.calls[0][0];
    expect(bounds[1][0] - bounds[0][0]).toBeCloseTo(0.0001);
    expect(bounds[1][1] - bounds[0][1]).toBeCloseTo(0.0001);
  });

  it("usa o menor intervalo ao cruzar o antimeridiano", () => {
    const fitBounds = vi.fn();
    const crossing: PolygonGeometry = { type: "Polygon", coordinates: [[[179.8, -1], [-179.8, -1], [-179.8, 1], [179.8, 1], [179.8, -1]]] };
    expect(fitMapToGeometry({ fitBounds } as never, crossing)).toBe(true);
    const bounds = fitBounds.mock.calls[0][0];
    expect(bounds[1][0] - bounds[0][0]).toBeCloseTo(0.4);
  });

  it("recusa coordenadas nao finitas", () => {
    const fitBounds = vi.fn();
    const invalid: PolygonGeometry = { type: "Polygon", coordinates: [[[Number.NaN, 0], [0, 0], [0, 1], [Number.NaN, 0]]] };
    expect(fitMapToGeometry({ fitBounds } as never, invalid)).toBe(false);
    expect(fitBounds).not.toHaveBeenCalled();
  });
});
