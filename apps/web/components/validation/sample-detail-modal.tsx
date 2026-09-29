"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, MapPin, Radio, Satellite, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { getValidationSample } from "@/lib/api/validation";
import { formatArea } from "@/lib/utils/recommendation";
import {
  formatDecibels,
  formatMaintenanceTruth,
  formatMetricValue,
  formatPercentileValue,
  formatValidationConfidence,
  formatValidationDate,
  formatValidationDateTime,
  formatValidationDecision,
  formatValidationSource,
  formatVegetationClass,
} from "@/lib/utils/validation";

export interface SampleDetailModalProps {
  sampleId: string | null;
  onClose: () => void;
}

export function SampleDetailModal({ sampleId, onClose }: SampleDetailModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);

  const {
    data: sample,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["validation-sample", sampleId],
    queryFn: () => getValidationSample(sampleId!),
    enabled: Boolean(sampleId),
  });

  useEffect(() => {
    if (!sampleId) return;

    previouslyFocusedElementRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocusedElementRef.current?.focus();
    };
  }, [sampleId, onClose]);

  if (!sampleId) return null;

  const snapshot = (sample?.snapshot ?? {}) as Record<string, Record<string, unknown>>;
  const aoiSnap = snapshot.aoi ?? {};
  const s2Snap = snapshot.sentinel2 ?? {};
  const s1Snap = snapshot.sentinel1 ?? {};

  return (
    <div
      className="validation-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        className="validation-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sample-detail-title"
      >
        <div className="validation-modal-head">
          <div className="title-group">
            <span className="experimental-badge">EXPERIMENTAL</span>
            <h2 id="sample-detail-title">Detalhe da amostra de validação</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="icon-action close-button"
            onClick={onClose}
            aria-label="Fechar detalhe da amostra"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {isLoading ? (
          <div className="modal-loading-state" aria-live="polite">
            <Loader2 className="spinner" size={24} aria-hidden="true" />
            <p>Carregando dados da amostra...</p>
          </div>
        ) : isError ? (
          <div className="modal-error-state" role="alert">
            <AlertCircle size={24} aria-hidden="true" />
            <p>{error instanceof Error ? error.message : "Erro ao carregar detalhes da amostra."}</p>
            <button type="button" className="secondary-button" onClick={onClose}>
              Fechar
            </button>
          </div>
        ) : sample ? (
          <div className="modal-scroll-content">
            <div className="sample-meta-bar">
              <span><strong>ID da amostra:</strong> {sample.sample_id}</span>
              <span><strong>Registrada em:</strong> {formatValidationDateTime(sample.created_at)}</span>
            </div>

            <div className="validation-groups-grid">
              {/* Grupo 1: GROUND TRUTH */}
              <section className="validation-group-box ground-truth" aria-labelledby="group-gt-title">
                <div className="group-header">
                  <CheckCircle2 size={16} aria-hidden="true" />
                  <h3 id="group-gt-title">Ground truth (Referência de campo)</h3>
                </div>
                <dl className="group-metrics-list">
                  <div>
                    <dt>Classe de vegetação</dt>
                    <dd><strong>{formatVegetationClass(sample.vegetation_class)}</strong></dd>
                  </div>
                  <div>
                    <dt>Necessidade real</dt>
                    <dd>
                      <span className={`truth-tag ${sample.maintenance_truth}`}>
                        {formatMaintenanceTruth(sample.maintenance_truth)}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Fonte da validação</dt>
                    <dd>{formatValidationSource(sample.validation_source)}</dd>
                  </div>
                  <div>
                    <dt>Data de referência</dt>
                    <dd>{formatValidationDate(sample.reference_date)}</dd>
                  </div>
                  <div className="full-width">
                    <dt>Observações</dt>
                    <dd className="notes-text">{sample.notes?.trim() || "Nenhuma observação registrada."}</dd>
                  </div>
                </dl>
              </section>

              {/* Grupo 2: AOI */}
              <section className="validation-group-box aoi" aria-labelledby="group-aoi-title">
                <div className="group-header">
                  <MapPin size={16} aria-hidden="true" />
                  <h3 id="group-aoi-title">Área de interesse (AOI)</h3>
                </div>
                <dl className="group-metrics-list">
                  <div>
                    <dt>Área selecionada</dt>
                    <dd>{sample.selected_area_m2 ? formatArea(sample.selected_area_m2) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Cobertura efetiva</dt>
                    <dd>
                      {typeof aoiSnap.effective_analysis_pct === "number"
                        ? formatPercentileValue(aoiSnap.effective_analysis_pct as number)
                        : "—"}
                    </dd>
                  </div>
                  <div className="full-width">
                    <dt>ID da análise original</dt>
                    <dd className="mono-code">{sample.analysis_id}</dd>
                  </div>
                </dl>
              </section>

              {/* Grupo 3: SENTINEL-2 */}
              <section className="validation-group-box sentinel2" aria-labelledby="group-s2-title">
                <div className="group-header">
                  <Satellite size={16} aria-hidden="true" />
                  <h3 id="group-s2-title">Sentinel-2 (Óptico)</h3>
                </div>
                <dl className="group-metrics-list">
                  <div>
                    <dt>Recomendação S2</dt>
                    <dd>
                      <span className={`decision-tag ${sample.s2_decision ?? ""}`}>
                        {formatValidationDecision(sample.s2_decision)}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Confiança</dt>
                    <dd>{formatValidationConfidence(sample.s2_confidence)}</dd>
                  </div>
                  <div>
                    <dt>NDVI atual (mediano)</dt>
                    <dd className="mono-value">{formatMetricValue(sample.s2_ndvi_median ?? sample.s2_ndvi_mean)}</dd>
                  </div>
                  <div>
                    <dt>Percentil histórico</dt>
                    <dd className="mono-value">{formatPercentileValue(sample.s2_current_percentile)}</dd>
                  </div>
                  <div>
                    <dt>Tendência recente</dt>
                    <dd>{(s2Snap.recent_trend_status as string) || (s2Snap.recent_trend != null ? formatMetricValue(s2Snap.recent_trend as number) : "—")}</dd>
                  </div>
                  <div>
                    <dt>Observações ópticas</dt>
                    <dd>{s2Snap.observation_count != null ? String(s2Snap.observation_count) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Qualidade da análise</dt>
                    <dd>{(s2Snap.analysis_quality_status as string) ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Fração de vegetação</dt>
                    <dd className="mono-value">
                      {typeof s2Snap.vegetation_fraction === "number"
                        ? formatPercentileValue((s2Snap.vegetation_fraction as number) * 100)
                        : "—"}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* Grupo 4: SENTINEL-1 */}
              <section className="validation-group-box sentinel1" aria-labelledby="group-s1-title">
                <div className="group-header">
                  <Radio size={16} aria-hidden="true" />
                  <h3 id="group-s1-title">Sentinel-1 (Radar SAR)</h3>
                </div>
                <dl className="group-metrics-list">
                  <div>
                    <dt>Status SAR</dt>
                    <dd>{sample.s1_status ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Qualidade radar</dt>
                    <dd>{sample.s1_quality != null ? formatPercentileValue(sample.s1_quality) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Cobertura SAR</dt>
                    <dd>{sample.s1_coverage != null ? formatPercentileValue(sample.s1_coverage) : "—"}</dd>
                  </div>
                  <div>
                    <dt>Calibração radiométrica</dt>
                    <dd>{(s1Snap.radiometric_calibration_status as string) ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Órbita relativa canônica</dt>
                    <dd className="mono-value">{sample.s1_canonical_relative_orbit ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Observações na órbita</dt>
                    <dd className="mono-value">{sample.s1_canonical_observation_count ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>VV sigma0</dt>
                    <dd className="mono-value">{formatDecibels(sample.s1_vv_sigma0_db)}</dd>
                  </div>
                  <div>
                    <dt>VH sigma0</dt>
                    <dd className="mono-value">{formatDecibels(sample.s1_vh_sigma0_db)}</dd>
                  </div>
                  <div>
                    <dt>VH − VV</dt>
                    <dd className="mono-value">{formatDecibels(sample.s1_vh_minus_vv_db)}</dd>
                  </div>
                  <div>
                    <dt>Comparabilidade temporal</dt>
                    <dd className="small-text">{(s1Snap.temporal_comparability as string) ?? "—"}</dd>
                  </div>
                </dl>
              </section>
            </div>
          </div>
        ) : null}

        <div className="validation-modal-footer">
          <p className="disclaimer-mini">Amostra registrada para validação multissensor. Não altera a decisão operacional.</p>
          <button type="button" className="secondary-button" onClick={onClose}>
            Fechar
          </button>
        </div>
      </div>
    </div>
  );
}
