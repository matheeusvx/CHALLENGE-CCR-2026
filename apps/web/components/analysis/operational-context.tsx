import { CalendarRange, Gauge, MapPinned } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  effectiveAnalysisPercentage,
  formatArea,
  formatDateBR,
  formatPercentage,
  selectedAreaSquareMeters,
} from "@/lib/utils/recommendation";

export function OperationalContext({ result }: { result: AnalysisResponse }) {
  const period = result.analysis_period;
  const effectivePercentage = effectiveAnalysisPercentage(result);
  return (
    <section className="operational-context" aria-label="Contexto da análise">
      <div><MapPinned size={19} /><span>Área selecionada</span><strong>{formatArea(selectedAreaSquareMeters(result))}</strong></div>
      {effectivePercentage !== null ? <div><Gauge size={19} /><span>Cobertura efetiva</span><strong>{formatPercentage(effectivePercentage)}</strong></div> : null}
      <div><CalendarRange size={19} /><span>Período analisado</span><strong>{formatDateBR(period.start_date)} a {formatDateBR(period.end_date)}</strong></div>
    </section>
  );
}
