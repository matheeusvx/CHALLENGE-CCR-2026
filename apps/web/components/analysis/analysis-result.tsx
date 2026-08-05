import { Download } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { toApiUrl } from "@/lib/api/client";
import { MetricsGrid } from "./metrics-grid";
import { RecommendationPanel } from "./recommendation-panel";
import { ScenesTable } from "./scenes-table";
import { TimeseriesChart } from "./timeseries-chart";

export function AnalysisResult({ result }: { result: AnalysisResponse }) {
  return (
    <div className="results" aria-live="polite">
      <div className="result-header"><div><span>ANALISE {result.analysis_id.slice(0, 8).toUpperCase()}</span><h2>Resultado operacional</h2></div><div className="artifact-links">{Object.entries(result.artifacts).slice(0, 3).map(([name, url]) => <a href={toApiUrl(url)} key={name}><Download size={15} />{name.replaceAll("_", " ")}</a>)}</div></div>
      <RecommendationPanel result={result} />
      <MetricsGrid result={result} />
      <TimeseriesChart result={result} />
      <ScenesTable scenes={result.scenes} />
    </div>
  );
}
