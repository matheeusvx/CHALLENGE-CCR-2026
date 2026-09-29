import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DecisionSupportPanel } from "@/components/analysis/decision-support-panel";
import { decisionSupportSchema, type DecisionSupport } from "@/lib/schemas/analyses";

function support(overrides: Partial<DecisionSupport> = {}): DecisionSupport {
  return decisionSupportSchema.parse({
    status: "available",
    experimental: true,
    model_version: "regrowth-support-v0",
    calibration_status: "uncalibrated",
    suggestion: "cortar",
    score: 0.72,
    confidence: "medium",
    agreement: "concorda",
    reference_km: 12,
    last_survey_on: "2026-03-20",
    days_since_survey: 5,
    prediction_horizon_days: 7,
    factors: ["Ponto mais critico: CANT. DISPOSITIVO EXT."],
    context: {
      non_compliant_count: 3,
      applicable_count: 10,
      non_compliant_share: 0.3,
      dominant_mowing_method: "Apenas manual",
    },
    ...overrides,
  });
}

describe("apoio do histórico", () => {
  it("não renderiza nada quando a análise não traz apoio", () => {
    const { container } = render(<DecisionSupportPanel support={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("apresenta a sugestão como evidência complementar, não como decisão", () => {
    render(<DecisionSupportPanel support={support()} />);

    expect(screen.getByRole("heading", { name: "O que o histórico operacional indica" })).toBeInTheDocument();
    expect(screen.getByText("CORTAR")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText("Apoio concorda com o satélite")).toBeInTheDocument();
    expect(screen.getByText(/recomendação oficial continua sendo a do satélite/i)).toBeInTheDocument();
    expect(screen.getByText("km 12")).toBeInTheDocument();
    expect(screen.getByText("3 de 10 (30%)")).toBeInTheDocument();
    expect(screen.getByText("Apenas manual")).toBeInTheDocument();
  });

  it("sinaliza divergência em relação ao satélite", () => {
    render(<DecisionSupportPanel support={support({ agreement: "diverge" })} />);
    expect(screen.getByText("Apoio diverge do satélite")).toBeInTheDocument();
  });

  it("não sugere decisão quando o dado de campo está velho", () => {
    render(
      <DecisionSupportPanel
        support={support({
          status: "stale_field_data",
          suggestion: null,
          score: null,
          agreement: null,
          days_since_survey: 167,
        })}
      />,
    );

    expect(screen.getByText(/antigo demais para sustentar uma projeção/i)).toBeInTheDocument();
    expect(screen.queryByText("CORTAR")).not.toBeInTheDocument();
    expect(screen.queryByText("Apoio concorda com o satélite")).not.toBeInTheDocument();
    // O retrato descritivo da última vistoria continua disponível.
    expect(screen.getByText("3 de 10 (30%)")).toBeInTheDocument();
  });

  it("explica a ausência de vistoria para o trecho", () => {
    render(
      <DecisionSupportPanel
        support={support({
          status: "insufficient_history",
          suggestion: null,
          score: null,
          agreement: null,
          reference_km: null,
          context: {},
          factors: [],
        })}
      />,
    );

    expect(screen.getByText(/não há vistoria de campo registrada/i)).toBeInTheDocument();
    expect(screen.queryByText("CORTAR")).not.toBeInTheDocument();
  });
});
