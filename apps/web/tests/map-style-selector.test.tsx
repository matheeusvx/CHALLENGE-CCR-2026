import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MapStyleSelector } from "@/components/map/map-style-selector";

describe("seletor de mapa-base", () => {
  it("mantem Operacional ativo e Terreno indisponivel", () => {
    render(<MapStyleSelector active="operational" />);
    expect(screen.getByRole("button", { name: "Operacional" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Terreno temporariamente indisponível" })).toBeDisabled();
    expect(screen.getByText("Terreno temporariamente indisponível")).toBeInTheDocument();
  });
});
