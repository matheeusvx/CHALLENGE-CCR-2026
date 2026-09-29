export type HeightEstimationDisplay = {
  status: "experimental" | "unavailable" | "disabled";
  estimated_class: "le_30_cm" | "gt_30_cm" | "inconclusive" | null;
};

export type RecommendationDecision = "cortar" | "nao_cortar" | "inconclusivo";
export type DisplayedHeightClass =
  | NonNullable<HeightEstimationDisplay["estimated_class"]>
  | "unavailable"
  | null;

const labels: Record<NonNullable<HeightEstimationDisplay["estimated_class"]>, string> = {
  le_30_cm: "Até 30 cm",
  gt_30_cm: "Acima de 30 cm",
  inconclusive: "Inconclusiva",
};

export function getDisplayedHeightClass(
  recommendation: RecommendationDecision,
  heightEstimation: HeightEstimationDisplay,
): DisplayedHeightClass {
  if (heightEstimation.status === "disabled") return null;
  if (heightEstimation.status === "unavailable" || !heightEstimation.estimated_class) {
    return "unavailable";
  }
  if (
    recommendation === "inconclusivo"
    || heightEstimation.estimated_class === "inconclusive"
  ) {
    return "inconclusive";
  }

  const isConsistent =
    (recommendation === "nao_cortar" && heightEstimation.estimated_class === "le_30_cm")
    || (recommendation === "cortar" && heightEstimation.estimated_class === "gt_30_cm");

  return isConsistent ? heightEstimation.estimated_class : "inconclusive";
}

export function formatDisplayedHeightClass(displayedClass: DisplayedHeightClass): string {
  if (displayedClass === "unavailable") return "Indisponível";
  if (displayedClass === null) return "";
  return labels[displayedClass];
}
