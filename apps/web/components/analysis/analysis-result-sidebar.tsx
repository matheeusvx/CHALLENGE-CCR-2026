import {
  AlertTriangle,
  CalendarRange,
  CheckCircle2,
  Crosshair,
  Gauge,
  Grid3x3,
  HelpCircle,
  Leaf,
  MapPinned,
  RotateCcw,
} from "lucide-react";
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
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";

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
  const autoRoad = useAutoAnalysisStore((state) => state.road);

  if (!result) {
    return <div className="result-panel-empty"><Leaf size={30} /><strong>Nenhum resultado disponível</strong><p>Valide a área e execute a análise para preencher esta etapa.</p></div>;
  }

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
  const road = result.road ?? (result.analysis_trigger === "automatic_viewport" ? autoRoad : null);

  return (
    <div className="workspace-panel-content result-sidebar">
      <div className={`decision-block ${effective.primaryDecision}`} data-decision={effective.primaryDecision}>
        <div className="decision-header-flex">
          <div className="decision-main-info">
            <span>{hasSegmentation ? "Resultado consolidado" : "Recomendação"}</span>
            <strong>{formatRecommendation(effective.primaryDecision)}</strong>
          </div>
          {road ? (
            <div className="decision-road-context" data-testid="result-road-context">
              {road.ref ? <span className="road-title">{road.ref}</span> : null}
              {road.name ? <span className="road-name">{road.name}</span> : null}
              <span className="road-subtitle">Trecho analisado</span>
            </div>
          ) : null}
        </div>
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
      <div className="panel-actions result-actions">
        <button type="button" className="secondary-button action-fit-area" onClick={requestFit}>
          <Crosshair size={16} aria-hidden="true" />
          Enquadrar área selecionada
        </button>
        <button type="button" className="quiet-action" onClick={onRetry}>
          <RotateCcw size={15} aria-hidden="true" />
          Executar novamente
        </button>
      </div>
    </div>
  );
}

