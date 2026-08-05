import { AlertTriangle, CheckCircle2, Scissors, ShieldQuestion } from "lucide-react";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const labels = { cortar: "CORTAR", nao_cortar: "NAO CORTAR", inconclusivo: "INCONCLUSIVO" } as const;
const confidence = { high: "Alta", medium: "Media", low: "Baixa" } as const;

export function RecommendationPanel({ result }: { result: AnalysisResponse }) {
  const recommendation = result.recommendation;
  const Icon = recommendation.decision === "cortar" ? Scissors : recommendation.decision === "nao_cortar" ? CheckCircle2 : ShieldQuestion;
  return (
    <section className={`recommendation ${recommendation.decision}`} aria-labelledby="recommendation-title">
      <div className="recommendation-decision"><Icon size={25} /><div><span>RECOMENDACAO EXPERIMENTAL</span><h2 id="recommendation-title">{labels[recommendation.decision]}</h2></div></div>
      <div className="confidence"><span>Confianca</span><strong>{confidence[recommendation.confidence]}</strong></div>
      <p>{recommendation.summary}</p>
      <div className="reason-columns"><div><h3>Motivos</h3><ul>{recommendation.reasons.map((reason) => <li key={reason}>{reason.replaceAll("_", " ")}</li>)}</ul></div><div><h3>Bloqueios</h3>{recommendation.blocking_reasons.length ? <ul>{recommendation.blocking_reasons.map((reason) => <li key={reason}>{reason.replaceAll("_", " ")}</li>)}</ul> : <span className="quiet">Nenhum bloqueio registrado</span>}</div></div>
      <div className="experimental-note"><AlertTriangle size={16} />O Sentinel-2 nao mede diretamente a altura da grama. Validacao de campo permanece necessaria.</div>
    </section>
  );
}
