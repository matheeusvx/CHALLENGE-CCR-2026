import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { DecisionSupportPanel } from "./decision-support-panel";
import { OperationalContext } from "./operational-context";
import { HeightEstimationCard } from "./height-estimation-card";
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
      <HeightEstimationCard
        heightEstimation={result.height_estimation}
        recommendation={result.recommendation.decision}
      />
      <DecisionSupportPanel support={result.decision_support} />
      <div className="result-executive-grid">
        <OperationalContext result={result} />
        <ResultReasons result={result} />
      </div>
      <TimeseriesChart result={result} />
    </div>
  );
}
