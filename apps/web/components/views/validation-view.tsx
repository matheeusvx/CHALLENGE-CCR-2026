"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FlaskConical,
  HelpCircle,
  Leaf,
  Loader2,
  RefreshCcw,
} from "lucide-react";
import { useState } from "react";
import { SampleDetailModal } from "@/components/validation/sample-detail-modal";
import {
  getValidationExportUrl,
  getValidationSamples,
  getValidationSummary,
} from "@/lib/api/validation";
import type {
  MaintenanceTruth,
  ValidationSource,
  VegetationClass,
} from "@/lib/schemas/validation";
import { formatArea } from "@/lib/utils/recommendation";
import {
  formatDecibels,
  formatMaintenanceTruth,
  formatMetricValue,
  formatValidationDate,
  formatValidationDateTime,
  formatValidationDecision,
  formatValidationSource,
  formatVegetationClass,
  MAINTENANCE_TRUTH_LABELS,
  VALIDATION_SOURCE_LABELS,
  VEGETATION_CLASS_LABELS,
} from "@/lib/utils/validation";

type ValidationViewProps = {
  onStartNewAnalysis: () => void;
};

const ORDERED_CLASSES: VegetationClass[] = [
  "low_grass",
  "tall_dense_grass",
  "shrub",
  "tree",
  "mixed",
];

export function ValidationView({ onStartNewAnalysis }: ValidationViewProps) {
  const [selectedClass, setSelectedClass] = useState<VegetationClass | "">("");
  const [selectedTruth, setSelectedTruth] = useState<MaintenanceTruth | "">("");
  const [selectedSource, setSelectedSource] = useState<ValidationSource | "">("");
  const [selectedSampleId, setSelectedSampleId] = useState<string | null>(null);

  const summaryQuery = useQuery({
    queryKey: ["validation-summary"],
    queryFn: getValidationSummary,
  });

  const samplesQuery = useQuery({
    queryKey: [
      "validation-samples",
      {
        vegetation_class: selectedClass || undefined,
        maintenance_truth: selectedTruth || undefined,
        validation_source: selectedSource || undefined,
      },
    ],
    queryFn: () =>
      getValidationSamples({
        vegetation_class: selectedClass || undefined,
        maintenance_truth: selectedTruth || undefined,
        validation_source: selectedSource || undefined,
        limit: 100,
      }),
  });

  const summary = summaryQuery.data;
  const samples = samplesQuery.data?.items ?? [];
  const totalSamples = summary?.total_samples ?? 0;
  const targetTotal = summary?.target_total ?? 30;
  const remainingTotal = summary?.remaining_total ?? 30;
  const progressPct = Math.min(100, Math.round((totalSamples / targetTotal) * 100));

  const handleExportCsv = () => {
    const url = getValidationExportUrl();
    window.location.href = url;
  };

  return (
    <section className="secondary-view validation-view" aria-labelledby="validation-view-title">
      <header className="validation-header page-heading">
        <div className="validation-header-copy">
          <div className="title-row">
            <h1 id="validation-view-title" className="page-heading-title">Validação multissensor</h1>
            <span className="experimental-badge">EXPERIMENTAL</span>
          </div>
          <p className="validation-desc page-heading-description">
            Base de amostras de referência para avaliação do Sentinel-1 e Sentinel-2.
          </p>
          <p className="validation-disclaimer">
            <HelpCircle size={14} aria-hidden="true" />
            Os dados desta área não alteram a recomendação operacional.
          </p>
        </div>

        <div className="validation-header-actions">
          <button
            type="button"
            className="secondary-button export-button"
            onClick={handleExportCsv}
            aria-label="Exportar CSV com as amostras de validação"
          >
            <Download size={16} aria-hidden="true" />
            Exportar CSV
          </button>
        </div>
      </header>

      {summaryQuery.isLoading ? (
        <div className="view-loading">
          <Loader2 className="spinner" size={24} aria-hidden="true" />
          <p>Carregando dados da validação...</p>
        </div>
      ) : summaryQuery.isError ? (
        <div className="view-error" role="alert">
          <AlertCircle size={24} aria-hidden="true" />
          <p>Erro ao carregar o resumo de validação.</p>
          <button
            type="button"
            className="secondary-button"
            onClick={() => summaryQuery.refetch()}
          >
            <RefreshCcw size={15} aria-hidden="true" /> Tentar novamente
          </button>
        </div>
      ) : totalSamples === 0 ? (
        <div className="view-empty validation-empty">
          <FlaskConical size={36} aria-hidden="true" />
          <strong>Nenhuma amostra registrada</strong>
          <p>Nenhuma amostra de benchmark registrada no momento.</p>
          <button type="button" className="secondary-button" onClick={onStartNewAnalysis}>
            <Leaf size={16} aria-hidden="true" />
            Nova análise
          </button>
        </div>
      ) : (
        <div className="validation-content">
          {/* Bloco 1: Progresso do Dataset */}
          <section className="validation-section dataset-progress-section" aria-labelledby="dataset-progress-title">
            <div className="section-head">
              <h2 id="dataset-progress-title">Progresso do dataset</h2>
              <span className="progress-remaining-pill">
                <strong>{remainingTotal}</strong> {remainingTotal === 1 ? "restante" : "restantes"}
              </span>
            </div>

            <div className="progress-overall-box">
              <div className="progress-meta">
                <span className="progress-label">Amostras registradas</span>
                <span className="progress-numbers">
                  <strong>{totalSamples}</strong> / {targetTotal}
                </span>
              </div>
              <div
                className="progress-bar-track"
                role="progressbar"
                aria-valuenow={totalSamples}
                aria-valuemin={0}
                aria-valuemax={targetTotal}
                aria-label="Progresso total do dataset"
              >
                <div
                  className="progress-bar-fill"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>

            <div className="class-cards-grid">
              {ORDERED_CLASSES.map((cls) => {
                const classData = summary?.by_vegetation_class?.[cls];
                const count = classData?.count ?? summary?.counts_by_vegetation_class?.[cls] ?? 0;
                const target = classData?.target ?? summary?.target_per_class ?? 6;
                const label = VEGETATION_CLASS_LABELS[cls];
                const isComplete = count >= target;

                return (
                  <div key={cls} className={`class-progress-card ${isComplete ? "complete" : ""}`}>
                    <div className="class-card-head">
                      <span className="class-title">{label}</span>
                      {isComplete ? (
                        <CheckCircle2 size={14} className="complete-icon" aria-hidden="true" />
                      ) : null}
                    </div>
                    <div className="class-card-numbers">
                      <strong>{count}</strong> / {target}
                    </div>
                    <div className="mini-progress-track">
                      <div
                        className="mini-progress-fill"
                        style={{ width: `${Math.min(100, (count / target) * 100)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Bloco 2: Estatísticas descritivas (Comparativo por classe) */}
          <section className="validation-section stats-section" aria-labelledby="class-stats-title">
            <div className="section-head">
              <div className="title-with-note">
                <h2 id="class-stats-title">Comparativo por classe</h2>
                <p className="stats-disclaimer">
                  Estatísticas descritivas. Não representam regra operacional.
                </p>
              </div>
            </div>

            <div className="stats-classes-grid">
              {ORDERED_CLASSES.map((cls) => {
                const classData = summary?.by_vegetation_class?.[cls];
                const count = classData?.count ?? 0;
                if (count === 0) return null;

                const s2Stats = classData?.statistics?.sentinel2 ?? {};
                const s1Stats = classData?.statistics?.sentinel1 ?? {};

                const ndviMedian = s2Stats.current_ndvi_median?.median ?? s2Stats.current_ndvi_mean?.median;
                const vvDb = s1Stats.canonical_vv_sigma0_db?.median;
                const vhDb = s1Stats.canonical_vh_sigma0_db?.median;
                const vhMinusVvDb = s1Stats.canonical_vh_minus_vv_db?.median;

                return (
                  <div key={cls} className="class-stat-card">
                    <div className="stat-card-head">
                      <span className="stat-class-name">{VEGETATION_CLASS_LABELS[cls]}</span>
                      <span className="stat-sample-count">{count} {count === 1 ? "amostra" : "amostras"}</span>
                    </div>

                    <dl className="stat-metrics-list">
                      <div>
                        <dt>NDVI mediano</dt>
                        <dd className="mono-value">{formatMetricValue(ndviMedian)}</dd>
                      </div>
                      <div>
                        <dt>VV sigma0</dt>
                        <dd className="mono-value">{formatDecibels(vvDb)}</dd>
                      </div>
                      <div>
                        <dt>VH sigma0</dt>
                        <dd className="mono-value">{formatDecibels(vhDb)}</dd>
                      </div>
                      <div>
                        <dt>VH − VV</dt>
                        <dd className="mono-value">{formatDecibels(vhMinusVvDb)}</dd>
                      </div>
                    </dl>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Bloco 3: Tabela com Filtros */}
          <section className="validation-section table-section" aria-labelledby="table-section-title">
            <div className="section-head">
              <h2 id="table-section-title">Amostras de referência</h2>
              <span className="table-count-label">
                {samples.length} {samples.length === 1 ? "amostra listada" : "amostras listadas"}
              </span>
            </div>

            <div className="validation-filters-bar" role="search" aria-label="Filtros de amostras">
              <div className="filter-item">
                <label htmlFor="filter-veg-class">Tipo de vegetação:</label>
                <select
                  id="filter-veg-class"
                  value={selectedClass}
                  onChange={(e) => setSelectedClass(e.target.value as VegetationClass | "")}
                >
                  <option value="">Todos</option>
                  {ORDERED_CLASSES.map((cls) => (
                    <option key={cls} value={cls}>
                      {VEGETATION_CLASS_LABELS[cls]}
                    </option>
                  ))}
                </select>
              </div>

              <div className="filter-item">
                <label htmlFor="filter-maintenance-truth">Necessidade real:</label>
                <select
                  id="filter-maintenance-truth"
                  value={selectedTruth}
                  onChange={(e) => setSelectedTruth(e.target.value as MaintenanceTruth | "")}
                >
                  <option value="">Todos</option>
                  {(Object.keys(MAINTENANCE_TRUTH_LABELS) as MaintenanceTruth[]).map((key) => (
                    <option key={key} value={key}>
                      {MAINTENANCE_TRUTH_LABELS[key]}
                    </option>
                  ))}
                </select>
              </div>

              <div className="filter-item">
                <label htmlFor="filter-validation-source">Fonte:</label>
                <select
                  id="filter-validation-source"
                  value={selectedSource}
                  onChange={(e) => setSelectedSource(e.target.value as ValidationSource | "")}
                >
                  <option value="">Todas</option>
                  {(Object.keys(VALIDATION_SOURCE_LABELS) as ValidationSource[]).map((key) => (
                    <option key={key} value={key}>
                      {VALIDATION_SOURCE_LABELS[key]}
                    </option>
                  ))}
                </select>
              </div>

              {samplesQuery.isFetching ? (
                <div className="filter-loading-indicator" aria-label="Atualizando listagem">
                  <Loader2 className="spinner" size={14} aria-hidden="true" />
                  <span>Filtrando...</span>
                </div>
              ) : null}
            </div>

            <div className="validation-table-wrapper">
              <table className="validation-table" aria-label="Amostras de validação registradas">
                <thead>
                  <tr>
                    <th scope="col">Tipo de vegetação</th>
                    <th scope="col">Necessidade real</th>
                    <th scope="col">Fonte de validação</th>
                    <th scope="col">Data de referência</th>
                    <th scope="col">Área</th>
                    <th scope="col">Recomendação S2</th>
                    <th scope="col">NDVI atual</th>
                    <th scope="col">VV sigma0</th>
                    <th scope="col">VH sigma0</th>
                    <th scope="col">Órbita canônica</th>
                    <th scope="col">Data de registro</th>
                  </tr>
                </thead>
                <tbody>
                  {samples.length === 0 ? (
                    <tr>
                      <td colSpan={11} className="no-samples-cell">
                        Nenhuma amostra encontrada para os filtros selecionados.
                      </td>
                    </tr>
                  ) : (
                    samples.map((row) => (
                      <tr
                        key={row.sample_id}
                        className="validation-row"
                        onClick={() => setSelectedSampleId(row.sample_id)}
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setSelectedSampleId(row.sample_id);
                          }
                        }}
                        aria-label={`Ver detalhes da amostra de ${formatVegetationClass(row.vegetation_class)}`}
                      >
                        <td>
                          <strong>{formatVegetationClass(row.vegetation_class)}</strong>
                        </td>
                        <td>
                          <span className={`truth-tag ${row.maintenance_truth}`}>
                            {formatMaintenanceTruth(row.maintenance_truth)}
                          </span>
                        </td>
                        <td>{formatValidationSource(row.validation_source)}</td>
                        <td>{formatValidationDate(row.reference_date)}</td>
                        <td className="mono-value">
                          {row.selected_area_m2 != null ? formatArea(row.selected_area_m2) : "—"}
                        </td>
                        <td>
                          <span className={`decision-tag ${row.s2_decision ?? ""}`}>
                            {formatValidationDecision(row.s2_decision)}
                          </span>
                        </td>
                        <td className="mono-value">
                          {formatMetricValue(row.s2_ndvi_median ?? row.s2_ndvi_mean)}
                        </td>
                        <td className="mono-value">{formatDecibels(row.s1_vv_sigma0_db)}</td>
                        <td className="mono-value">{formatDecibels(row.s1_vh_sigma0_db)}</td>
                        <td className="mono-value">
                          {row.s1_canonical_relative_orbit != null
                            ? String(row.s1_canonical_relative_orbit)
                            : "—"}
                        </td>
                        <td className="date-cell">{formatValidationDateTime(row.created_at)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}

      {/* Modal de Detalhe da Amostra */}
      <SampleDetailModal
        sampleId={selectedSampleId}
        onClose={() => setSelectedSampleId(null)}
      />
    </section>
  );
}
