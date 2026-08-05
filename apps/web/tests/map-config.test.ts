import { describe, expect, it } from "vitest";
import { DEFAULT_MAP_STYLE_ID, MAP_CONFIG, MAP_STYLES } from "@/lib/map/config";

describe("configuracao cartografica", () => {
  it("abre em Louveira na escala operacional", () => {
    expect(MAP_CONFIG.initialViewport).toMatchObject({ longitude: -46.955, latitude: -23.121, zoom: 12 });
    expect(MAP_CONFIG.minZoom).toBeGreaterThan(0);
    expect(MAP_CONFIG.maxZoom).toBeGreaterThan(MAP_CONFIG.initialViewport.zoom);
  });

  it("usa o mapa operacional bright por padrao", () => {
    expect(DEFAULT_MAP_STYLE_ID).toBe("operational");
    expect(MAP_STYLES.operational.styleUrl).toContain("openfreemap.org/styles/bright");
    expect(MAP_STYLES.terrain.label).toBe("Terreno");
  });
});
