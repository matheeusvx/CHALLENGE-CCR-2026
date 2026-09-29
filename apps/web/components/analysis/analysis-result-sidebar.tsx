import {
  AlertTriangle,
  CalendarRange,
  CheckCircle2,
  Crosshair,
  FlaskConical,
  Gauge,
  Grid3x3,
  HelpCircle,
  Leaf,
  MapPinned,
  RotateCcw,
} from "lucide-react";
import { useState } from "react";
import { SaveValidationSampleModal } from "@/components/validation/save-validation-sample-modal";
import type { AnalysisResponse, SpatialZone } from "@/lib/schemas/analyses";
import {
  analysisQualityStatus,
  calculateZoneAreaStats,
  effectiveAnalysisPercentage,
  formatAnalysisQuality,
  formatArea,
  formatConfidence,
  formatDateBR,
  formatPercentage,
  formatRecommendation,
  formatRecommendationSummary,
  getEffectiveRecommendation,
  recommendationReasonLabel,
  selectedAreaSquareMeters,
} from "@/lib/utils/recommendation";
import { useAnalysisStore } from "@/stores/analysis-store";

function SegmentationSummary({ zones, coveragePct }: { zones: SpatialZone[]; coveragePct?: number | null }) {
  const stats = calculateZoneAreaStats(zones, coveragePct);

  return (
    <section className="segmentation-summary" aria-label="Segmentação espacial">
      <div className="segmentation-summary-header">
        <Grid3x3 size={17} aria-hidden="true" />
        <div>
          <span className="segmentation-level-label">Resultado espacial</span>
          <h3>Segmentação por zonas</h3>
        </div>
      </div>

      <p className="segmentation-zone-count">
        <strong>{stats.totalZones}</strong> {stats.totalZones === 1 ? "zona identificada" : "zonas identificadas"}
      </p>

      <div className="segmentation-cards-grid">
        <div className="seg-card cortar">
          <div className="seg-card-head">
            <span className="seg-card-tag">CORTAR</span>
            <span className="seg-card-count">{stats.cutCount} {stats.cutCount === 1 ? "zona" : "zonas"}</span>
          </div>
          <strong className="seg-card-pct">{formatPercentage(stats.cutPct)}</strong>
          <span className="seg-card-area">{formatArea(stats.cutArea)}</span>
        </div>

        <div className="seg-card nao_cortar">
          <div className="seg-card-head">
            <span className="seg-card-tag">NÃO CORTAR</span>
            <span className="seg-card-count">{stats.noCutCount} {stats.noCutCount === 1 ? "zona" : "zonas"}</span>
          </div>
          <strong className="seg-card-pct">{formatPercentage(stats.noCutPct)}</strong>
          <span className="seg-card-area">{formatArea(stats.noCutArea)}</span>
        </div>

        <div
          className="seg-card inconclusivo"
          title="Dados insuficientes para uma recomendação local"
        >
          <div className="seg-card-head">
            <span className="seg-card-tag">INCONCLUSIVO</span>
            <span className="seg-card-count">{stats.inconclusiveCount} {stats.inconclusiveCount === 1 ? "zona" : "zonas"}</span>
          </div>
          <strong className="seg-card-pct">{formatPercentage(stats.inconclusivePct)}</strong>
          <span className="seg-card-area">{formatArea(stats.inconclusiveArea)}</span>
          <span className="seg-card-note">
            <HelpCircle size={11} aria-hidden="true" />
            Dados insuficientes para uma recomendação local
          </span>
        </div>
      </div>

      {stats.coveragePct != null ? (
        <div className="seg-coverage-row">
          <span>Cobertura efetiva</span>
          <strong>{formatPercentage(stats.coveragePct)}</strong>
        </div>
      ) : null}
    </section>
  );
}

export function AnalysisResultSidebar({ result, onRetry }: { result?: AnalysisResponse; onRetry: () => void }) {
  const requestFit = useAnalysisStore((state) => state.requestGeometryFit);
  const [isValidationModalOpen, setIsValidationModalOpen] = useState(false);
  const [savedSamples, setSavedSamples] = useState<Set<string>>(new Set());
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);

  if (!result) {
    return <div className="result-panel-empty"><Leaf size={30} /><strong>Nenhum resultado disponível</strong><p>Valide a área e execute a análise para preencher esta etapa.</p></div>;
  }

  const isSampleSaved = Boolean(result.analysis_id && savedSamples.has(result.analysis_id));
  const range = result.analysis_period;
  const quality = analysisQualityStatus(result);
  const effectivePercentage = effectiveAnalysisPercentage(result);
  const reasons = [...new Set([
    ...result.recommendation.reasons,
    ...result.recommendation.blocking_reasons,
  ])];

  const hasSegmentation = Boolean(
    result.spatial_segmentation?.status === "available" && (result.spatial_segmentation.zones.length > 0),
  );
  const zoneStats = hasSegmentation
    ? calculateZoneAreaStats(result.spatial_segmentation!.zones, result.spatial_segmentation!.effective_coverage_pct)
    : null;

  const effective = getEffectiveRecommendation(result);

  return (
    <div className="workspace-panel-content result-sidebar">
      <div className={`decision-block ${effective.primaryDecision}`} data-decision={effective.primaryDecision}>
        <span>{effective.isMultisource ? "Resultado multissensor" : hasSegmentation ? "Resultado consolidado" : "Recomendação"}</span>
        <strong>{formatRecommendation(effective.primaryDecision)}</strong>
        {effective.isMultisource ? (
          <div className="sidebar-multisource-audit">
            <span className="s2-audit-tag">Sentinel-2: {formatRecommendation(effective.s2Decision)}</span>
            {effective.influenced ? (
              <span className="s1-influenced-tag">Sentinel-1 influenciou esta análise</span>
            ) : null}
            {effective.fusionRule ? (
              <span className="fusion-rule-tag">Regra {effective.fusionRule}</span>
            ) : null}
          </div>
        ) : null}
        <p>{formatRecommendationSummary(effective.summary)}</p>

        {zoneStats && zoneStats.cutCount > 0 ? (
          <div className="localized-intervention-callout" role="status">
            <div className="callout-main">
              <AlertTriangle size={14} className="callout-icon" aria-hidden="true" />
              <span className="callout-title">
                {zoneStats.cutCount} {zoneStats.cutCount === 1 ? "zona requer intervenção" : "zonas requerem intervenção"}
              </span>
            </div>
            <span className="callout-detail">
              {formatArea(zoneStats.cutArea)} · {formatPercentage(zoneStats.cutPct)} da área segmentada
            </span>
          </div>
        ) : null}
      </div>

      <dl className="result-quick-metrics">
        <div><Gauge size={17} /><dt>Confiança</dt><dd>{formatConfidence(result.recommendation.confidence)}</dd></div>
        <div><Leaf size={17} /><dt>Qualidade</dt><dd>{formatAnalysisQuality(quality)}</dd></div>
        <div><MapPinned size={17} /><dt>Área selecionada</dt><dd>{formatArea(selectedAreaSquareMeters(result))}</dd></div>
        {effectivePercentage !== null ? <div><Gauge size={17} /><dt>Cobertura efetiva</dt><dd>{formatPercentage(effectivePercentage)}</dd></div> : null}
        <div><CalendarRange size={17} /><dt>Período</dt><dd>{formatDateBR(range.start_date)} a {formatDateBR(range.end_date)}</dd></div>
      </dl>

      <section className={`result-reasons${result.recommendation.blocking_reasons.length ? " blocking" : ""}`}>
        <h3>{result.recommendation.blocking_reasons.length ? <AlertTriangle size={17} /> : null}Motivos da recomendação</h3>
        {reasons.length ? <ul>{reasons.map((reason) => <li key={reason}><CheckCircle2 size={16} aria-hidden="true" /><span>{recommendationReasonLabel(reason)}</span></li>)}</ul> : <p>Nenhum motivo adicional registrado.</p>}
      </section>

      {hasSegmentation && result.spatial_segmentation ? (
        <SegmentationSummary zones={result.spatial_segmentation.zones} coveragePct={result.spatial_segmentation.effective_coverage_pct} />
      ) : null}

      {feedbackMessage ? (
        <div className="validation-feedback-pill" role="status">
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>{feedbackMessage}</span>
        </div>
      ) : null}

      <div className="panel-actions result-actions">
        {result.analysis_id ? (
          <button
            type="button"
            className="secondary-button action-save-validation"
            onClick={() => setIsValidationModalOpen(true)}
            disabled={isSampleSaved}
          >
            {isSampleSaved ? (
              <>
                <CheckCircle2 size={16} aria-hidden="true" />
                Amostra registrada
              </>
            ) : (
              <>
                <FlaskConical size={16} aria-hidden="true" />
                Salvar para validação
              </>
            )}
          </button>
        ) : null}
        <button type="button" className="secondary-button" onClick={requestFit}><Crosshair size={16} />Enquadrar área selecionada</button>
        <button type="button" className="quiet-action" onClick={onRetry}><RotateCcw size={15} />Executar novamente</button>
      </div>

      {result.analysis_id ? (
        <SaveValidationSampleModal
          analysisId={result.analysis_id}
          isOpen={isValidationModalOpen}
          onClose={() => setIsValidationModalOpen(false)}
          onSaved={() => {
            if (result.analysis_id) {
              setSavedSamples((prev) => new Set(prev).add(result.analysis_id));
            }
            setFeedbackMessage("Amostra salva na base de validação.");
            setTimeout(() => setFeedbackMessage(null), 5000);
          }}
        />
      ) : null}
    </div>
  );
}

