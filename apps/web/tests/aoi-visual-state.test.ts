import { describe, expect, it } from "vitest";
import { getAoiVisualState } from "@/lib/map/aoi-visual-state";

describe("estado visual da AOI", () => {
  it.each([
    [{ editing: true, dirty: true, validation: null }, "editing"],
    [{ editing: false, dirty: true, validation: null }, "pending_validation"],
    [{ editing: false, dirty: false, validation: "valid" }, "valid"],
    [{ editing: false, dirty: false, validation: "invalid" }, "invalid"],
    [{ editing: false, dirty: false, validation: "valid", recommendation: "cortar" }, "cut"],
    [{ editing: false, dirty: false, validation: "valid", recommendation: "nao_cortar" }, "no_cut"],
    [{ editing: false, dirty: false, validation: "valid", recommendation: "inconclusivo" }, "inconclusive"],
  ] as const)("resolve %o como %s", (input, expected) => {
    expect(getAoiVisualState(input).id).toBe(expected);
  });

  it("nao mostra uma recomendacao antiga durante uma edicao", () => {
    expect(getAoiVisualState({ editing: true, dirty: true, validation: "valid", recommendation: "cortar" }).id).toBe("editing");
  });
});
