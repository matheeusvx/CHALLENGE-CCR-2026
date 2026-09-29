import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({ default: (_loader: unknown, options: { ssr?: boolean }) => function ClientMap() { return <div data-testid="client-map" data-ssr={String(options.ssr)} />; } }));

import { AnalysisMap } from "@/components/map/analysis-map";

describe("carregamento do mapa", () => {
  it("carrega o canvas somente no cliente para evitar erros de SSR", () => { render(<AnalysisMap />); expect(screen.getByTestId("client-map")).toHaveAttribute("data-ssr", "false"); });
});

