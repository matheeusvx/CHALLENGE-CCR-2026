import { CalendarRange, MapPinned } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import {
  analyzedAreaSquareMeters,
  formatOperationalArea,
  formatOperationalDate,
} from "@/lib/utils/recommendation";

export function OperationalContext({ result }: { result: AnalysisResponse }) {
  const period = result.analysis_period;
  return (
    <section className="operational-context" aria-label="Contexto da analise">
      <div><MapPinned size={19} /><span>Area analisada</span><strong>{formatOperationalArea(analyzedAreaSquareMeters(result))}</strong></div>
      <div><CalendarRange size={19} /><span>Periodo analisado</span><strong>{formatOperationalDate(period.start_date)} a {formatOperationalDate(period.end_date)}</strong></div>
    </section>
  );
}
