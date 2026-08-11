import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { OperationalContext } from "./operational-context";
import { RecommendationPanel } from "./recommendation-panel";
import { ResultReasons } from "./result-reasons";
import { TimeseriesChart } from "./timeseries-chart";

export function AnalysisResult({ result }: { result: AnalysisResponse }) {
  return (
    <div className="results" aria-live="polite">
      <header className="result-header"><div><span>RESULTADO DA ANALISE</span><h2>Orientacao operacional para a area selecionada</h2></div></header>
      <RecommendationPanel result={result} />
      <OperationalContext result={result} />
      <ResultReasons result={result} />
      <TimeseriesChart result={result} />
      <section className="about-analysis" aria-labelledby="about-analysis-title">
        <h2 id="about-analysis-title">Sobre esta analise</h2>
        <p>Esta recomendacao e experimental e utiliza indicadores espectrais obtidos por satelite. Ela nao representa medicao direta da altura da vegetacao nem substitui a inspecao de campo.</p>
      </section>
    </div>
  );
}
