import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { OperationalContext } from "./operational-context";
import { RecommendationPanel } from "./recommendation-panel";
import { ResultReasons } from "./result-reasons";
import { TimeseriesChart } from "./timeseries-chart";

export function AnalysisResult({ result }: { result: AnalysisResponse }) {
  return (
    <div className="results" aria-live="polite">
      <header className="result-header"><div><span>RESULTADO DA ANÁLISE</span><h2>Orientação operacional para a área selecionada</h2></div></header>
      <RecommendationPanel result={result} />
      <OperationalContext result={result} />
      <ResultReasons result={result} />
      <TimeseriesChart result={result} />
    </div>
  );
}
