import { CalendarRange, MapPinned } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analyzedAreaSquareMeters,
  formatArea,
  formatDateBR,
} from "@/lib/utils/recommendation";

export function OperationalContext({ result }: { result: AnalysisResponse }) {
  const period = result.analysis_period;
  return (
    <section className="operational-context" aria-label="Contexto da análise">
      <div><MapPinned size={19} /><span>Área analisada</span><strong>{formatArea(analyzedAreaSquareMeters(result))}</strong></div>
      <div><CalendarRange size={19} /><span>Período analisado</span><strong>{formatDateBR(period.start_date)} a {formatDateBR(period.end_date)}</strong></div>
    </section>
  );
}
