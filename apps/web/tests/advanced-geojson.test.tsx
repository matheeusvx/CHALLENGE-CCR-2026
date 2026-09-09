import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdvancedGeoJson } from "@/components/analysis/advanced-geojson";
import { useAnalysisStore } from "@/stores/analysis-store";
import type { PolygonGeometry } from "@/lib/map/geometry";

const polygon: PolygonGeometry = { type: "Polygon", coordinates: [[[-46.962, -23.109], [-46.96, -23.109], [-46.96, -23.107], [-46.962, -23.107], [-46.962, -23.109]]] };

beforeEach(() => {
  useAnalysisStore.setState({ geometry: null, geometryRevision: 0, geometrySource: null, geometryValidation: null, geometryText: "" });
  vi.mocked(window.confirm).mockReturnValue(true);
});

describe("entrada avançada", () => {
  it("substitui uma geometria existente", () => { useAnalysisStore.getState().setGeometry(polygon, "drawn"); render(<AdvancedGeoJson />); fireEvent.click(screen.getByText("Entrada avançada por GeoJSON")); fireEvent.change(screen.getByLabelText("GeoJSON da área de interesse"), { target: { value: JSON.stringify({ type: "Feature", properties: {}, geometry: polygon }) } }); fireEvent.click(screen.getByRole("button", { name: "Aplicar" })); expect(window.confirm).toHaveBeenCalled(); expect(useAnalysisStore.getState().geometrySource).toBe("pasted"); });
  it("faz upload de um GeoJSON válido", async () => { render(<AdvancedGeoJson />); const file = new File([JSON.stringify(polygon)], "area.geojson", { type: "application/geo+json" }); fireEvent.change(screen.getByLabelText("Selecionar arquivo GeoJSON"), { target: { files: [file] } }); await waitFor(() => expect(useAnalysisStore.getState().geometrySource).toBe("uploaded")); expect(screen.getByText("Arquivo aplicado ao mapa.")).toBeInTheDocument(); });
  it("rejeita upload com extensão inválida", async () => { render(<AdvancedGeoJson />); fireEvent.change(screen.getByLabelText("Selecionar arquivo GeoJSON"), { target: { files: [new File(["x"], "area.txt")] } }); expect(await screen.findByRole("alert")).toHaveTextContent(".geojson ou .json"); });
  it("baixa a geometria atual", () => { useAnalysisStore.getState().setGeometry(polygon, "drawn"); render(<AdvancedGeoJson />); fireEvent.click(screen.getByText("Entrada avançada por GeoJSON")); fireEvent.click(screen.getByRole("button", { name: "Baixar geometria atual" })); expect(URL.createObjectURL).toHaveBeenCalled(); expect(URL.revokeObjectURL).toHaveBeenCalled(); });
});
