import { Info, Ruler } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  formatDisplayedHeightClass,
  getDisplayedHeightClass,
} from "@/lib/utils/height-estimation";

type HeightEstimation = NonNullable<AnalysisResponse["height_estimation"]>;

export function HeightEstimationCard({
  heightEstimation,
  recommendation,
}: {
  heightEstimation?: HeightEstimation;
  recommendation: AnalysisResponse["recommendation"]["decision"];
}) {
  if (!heightEstimation || heightEstimation.status === "disabled") return null;

  const displayedClass = getDisplayedHeightClass(recommendation, heightEstimation);
  const value = formatDisplayedHeightClass(displayedClass);

  return (
    <section className="height-estimation-card" aria-labelledby="height-estimation-title">
      <div className="height-estimation-icon" aria-hidden="true"><Ruler size={21} /></div>
      <div>
        <span id="height-estimation-title">Altura estimada</span>
        <strong>{value}</strong>
      </div>
      <span
        className="experimental-badge"
        title="Estimativa baseada em sensoriamento remoto. Não representa medição direta em campo."
      >
        <Info size={13} aria-hidden="true" />Experimental
      </span>
    </section>
  );
}
