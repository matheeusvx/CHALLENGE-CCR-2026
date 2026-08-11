import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { OperationalContext } from "./operational-context";
import { RecommendationPanel } from "./recommendation-panel";
import { ResultReasons } from "./result-reasons";
import { TimeseriesChart } from "./timeseries-chart";

export function AnalysisResult({ result }: { result: AnalysisResponse }) {
  return (
    <div className="results" aria-live="polite">
      <header className="result-header">
        <div>
          <span>Painel de análise operacional</span>
          <h2>Resumo executivo da área selecionada</h2>
          <p>Resultado consolidado para apoiar a decisão de manejo da faixa lateral.</p>
        </div>
      </header>
      <RecommendationPanel result={result} />
      <div className="result-executive-grid">
        <OperationalContext result={result} />
        <ResultReasons result={result} />
      </div>
      <TimeseriesChart result={result} />
    </div>
  );
}
