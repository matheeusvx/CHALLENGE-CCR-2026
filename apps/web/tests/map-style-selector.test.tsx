import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MapStyleSelector } from "@/components/map/map-style-selector";

describe("seletor de mapa-base", () => {
  it("identifica a base ativa e permite selecionar Terreno pelo teclado ou clique", () => {
    const onChange = vi.fn();
    render(<MapStyleSelector active="operational" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Operacional" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Terreno" }));
    expect(onChange).toHaveBeenCalledWith("terrain");
  });
});
