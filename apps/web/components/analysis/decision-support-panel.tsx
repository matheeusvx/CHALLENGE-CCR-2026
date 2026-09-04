import { Check, CircleAlert, History, Info, Milestone, TriangleAlert } from "lucide-react";
import type { DecisionSupport } from "@/lib/schemas/analyses";
import { formatDateBR, formatRecommendation } from "@/lib/utils/recommendation";

const agreementLabels = {
  concorda: "Apoio concorda com o satélite",
  diverge: "Apoio diverge do satélite",
  indeterminado: "Apoio não conclusivo",
} as const;

const statusMessages = {
  stale_field_data: "O levantamento de campo disponível é antigo demais para sustentar uma projeção. Abaixo fica apenas o retrato da última vistoria.",
  insufficient_history: "Ainda não há vistoria de campo registrada para este trecho.",
  unavailable: "O modelo de apoio não está disponível no momento.",
} as const;

function formatPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export function DecisionSupportPanel({ support }: { support?: DecisionSupport | null }) {
  if (!support) return null;

  const hasSuggestion = support.status === "available" && Boolean(support.suggestion);
  const share = typeof support.context?.non_compliant_share === "number" ? (support.context.non_compliant_share as number) : null;
  const nonCompliant = typeof support.context?.non_compliant_count === "number" ? (support.context.non_compliant_count as number) : null;
  const applicable = typeof support.context?.applicable_count === "number" ? (support.context.applicable_count as number) : null;
  const method = typeof support.context?.dominant_mowing_method === "string" ? (support.context.dominant_mowing_method as string) : null;

  return (
    <section className="support-panel" data-status={support.status} aria-labelledby="decision-support-title">
      <div className="support-head">
        <span className="support-icon" aria-hidden="true"><History size={20} /></span>
        <div>
          <span>Apoio do histórico</span>
          <h2 id="decision-support-title">O que o histórico operacional indica</h2>
        </div>
        {hasSuggestion && support.agreement ? (
          <span className={`support-agreement ${support.agreement}`}>
            {support.agreement === "concorda" ? <Check size={14} /> : support.agreement === "diverge" ? <TriangleAlert size={14} /> : <Info size={14} />}
            {agreementLabels[support.agreement]}
          </span>
        ) : null}
      </div>

      <p className="support-disclaimer">
        <CircleAlert size={14} aria-hidden="true" />
        Evidência complementar e experimental. A recomendação oficial continua sendo a do satélite.
      </p>

      {hasSuggestion ? (
        <dl className="support-metrics">
          <div>
            <dt>Sugestão do histórico</dt>
            <dd className={`support-suggestion ${support.suggestion}`}>
              {formatRecommendation(support.suggestion as "cortar" | "nao_cortar" | "inconclusivo")}
            </dd>
          </div>
          <div>
            <dt>Probabilidade de estar acima do limite</dt>
            <dd>{formatPercent(support.score)}</dd>
          </div>
          <div>
            <dt>Última vistoria</dt>
            <dd>{support.last_survey_on ? formatDateBR(support.last_survey_on) : "—"}{support.days_since_survey !== null && support.days_since_survey !== undefined ? ` · há ${support.days_since_survey} dias` : ""}</dd>
          </div>
        </dl>
      ) : (
        <p className="support-status-message">{statusMessages[support.status as keyof typeof statusMessages] ?? ""}</p>
      )}

      {(support.reference_km !== null && support.reference_km !== undefined) || nonCompliant !== null || method ? (
        <dl className="support-context">
          {support.reference_km !== null && support.reference_km !== undefined ? (
            <div><Milestone size={14} /><dt>Trecho de referência</dt><dd>km {support.reference_km}</dd></div>
          ) : null}
          {nonCompliant !== null && applicable !== null ? (
            <div><TriangleAlert size={14} /><dt>Pontos acima do limite na última vistoria</dt><dd>{nonCompliant} de {applicable}{share !== null ? ` (${formatPercent(share)})` : ""}</dd></div>
          ) : null}
          {method ? (
            <div><History size={14} /><dt>Método de roçada predominante</dt><dd>{method}</dd></div>
          ) : null}
        </dl>
      ) : null}

      {support.factors.length ? (
        <ul className="support-factors">
          {support.factors.map((factor) => (
            <li key={factor}>{factor}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
