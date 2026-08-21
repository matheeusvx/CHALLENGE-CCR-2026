import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeightEstimationCard } from "@/components/analysis/height-estimation-card";
import { heightEstimationSchema, type AnalysisResponse } from "@/lib/schemas/analyses";
import { getDisplayedHeightClass } from "@/lib/utils/height-estimation";

type HeightEstimation = NonNullable<AnalysisResponse["height_estimation"]>;

function value(
  status: HeightEstimation["status"],
  estimatedClass: HeightEstimation["estimated_class"],
): HeightEstimation {
  return {
    status,
    estimated_class: estimatedClass,
    score_gt_30_cm: status === "experimental" ? 0.23 : null,
    probability_gt_30_cm: status === "experimental" ? 0.23 : null,
    calibration_status: "uncalibrated",
    vegetation_fraction: status === "experimental" ? 0.8 : null,
    height_valid_pixel_count: status === "experimental" ? 80 : null,
    height_total_pixel_count: status === "experimental" ? 100 : null,
    mixed_pixel_risk: status === "experimental" ? "low" : null,
    confidence: status === "experimental" ? "medium" : null,
    reference_threshold_cm: 30,
    model_version: status === "experimental" ? "height-estimator-v0" : null,
    provenance: status === "experimental"
      ? { feature_pipeline: "height_valid_mask_v1" }
      : null,
  };
}

describe("indicador de altura estimada", () => {
  it("aceita histórico legado e contrato técnico novo sem mudar a apresentação", () => {
    const legacy = {
      status: "experimental" as const,
      estimated_class: "le_30_cm" as const,
      probability_gt_30_cm: 0.23,
      confidence: "medium" as const,
      reference_threshold_cm: 30 as const,
      model_version: "height-estimator-v0",
    };
    const current = value("experimental", "le_30_cm");
    expect(heightEstimationSchema.parse(legacy).probability_gt_30_cm).toBe(0.23);
    expect(heightEstimationSchema.parse(current).score_gt_30_cm).toBe(0.23);
  });

  it.each([
    ["nao_cortar", "le_30_cm", "le_30_cm"],
    ["cortar", "gt_30_cm", "gt_30_cm"],
    ["nao_cortar", "gt_30_cm", "inconclusive"],
    ["cortar", "le_30_cm", "inconclusive"],
    ["inconclusivo", "le_30_cm", "inconclusive"],
    ["inconclusivo", "gt_30_cm", "inconclusive"],
    ["nao_cortar", "inconclusive", "inconclusive"],
    ["cortar", "inconclusive", "inconclusive"],
  ] as const)("combina %s + %s como %s", (recommendation, estimatedClass, expected) => {
    expect(
      getDisplayedHeightClass(recommendation, value("experimental", estimatedClass)),
    ).toBe(expected);
  });

  it.each([
    ["nao_cortar", "le_30_cm", "Até 30 cm"],
    ["cortar", "gt_30_cm", "Acima de 30 cm"],
    ["nao_cortar", "gt_30_cm", "Inconclusiva"],
    ["cortar", "le_30_cm", "Inconclusiva"],
    ["inconclusivo", "gt_30_cm", "Inconclusiva"],
  ] as const)("renderiza %s + %s como %s", (recommendation, estimatedClass, label) => {
    render(
      <HeightEstimationCard
        heightEstimation={value("experimental", estimatedClass)}
        recommendation={recommendation}
      />,
    );
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("Experimental")).toBeInTheDocument();
    expect(screen.queryByText("0.23")).not.toBeInTheDocument();
    expect(screen.queryByText("medium")).not.toBeInTheDocument();
    expect(screen.queryByText("height-estimator-v0")).not.toBeInTheDocument();
    expect(screen.queryByText("uncalibrated")).not.toBeInTheDocument();
    expect(screen.queryByText("0.8")).not.toBeInTheDocument();
  });

  it("mostra indisponível sem detalhes técnicos", () => {
    expect(getDisplayedHeightClass("cortar", value("unavailable", null))).toBe("unavailable");
    render(
      <HeightEstimationCard
        heightEstimation={value("unavailable", null)}
        recommendation="cortar"
      />,
    );
    expect(screen.getByText("Indisponível")).toBeInTheDocument();
  });

  it("não renderiza quando a funcionalidade está desabilitada", () => {
    expect(getDisplayedHeightClass("nao_cortar", value("disabled", null))).toBeNull();
    const { container } = render(
      <HeightEstimationCard
        heightEstimation={value("disabled", null)}
        recommendation="nao_cortar"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
