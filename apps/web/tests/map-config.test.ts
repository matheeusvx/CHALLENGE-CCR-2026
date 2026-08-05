import { describe, expect, it } from "vitest";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, MAP_STYLES, OPERATIONAL_RASTER_STYLE } from "@/lib/map/config";

describe("configuracao cartografica", () => {
  it("define o style raster operacional local", () => {
    expect(OPERATIONAL_RASTER_STYLE.version).toBe(8);
    expect(OPERATIONAL_RASTER_STYLE.sources.openStreetMap).toMatchObject({
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
    });
    expect(OPERATIONAL_RASTER_STYLE.layers).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "openstreetmap-base", type: "raster", source: "openStreetMap" }),
    ]));
  });

  it("abre em Louveira na escala operacional", () => {
    expect(MAP_CONFIG.initialViewport).toMatchObject({ longitude: -46.955, latitude: -23.121, zoom: 12 });
    expect(MAP_CONFIG.minZoom).toBeGreaterThan(0);
    expect(MAP_CONFIG.maxZoom).toBeGreaterThan(MAP_CONFIG.initialViewport.zoom);
  });

  it("mantem somente o estilo operacional disponivel", () => {
    expect(DEFAULT_MAP_STYLE_ID).toBe("operational");
    expect(MAP_STYLES.operational.available).toBe(true);
    expect(MAP_STYLES.terrain.available).toBe(false);
  });
});
