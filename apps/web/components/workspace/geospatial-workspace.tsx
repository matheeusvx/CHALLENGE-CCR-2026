"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { AnalysisMap } from "@/components/map/analysis-map";
import { AreaPanel } from "@/components/analysis/area-panel";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import { runAnalysis, validateGeometry } from "@/lib/api/analyses";
import { ApiError } from "@/lib/api/client";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { useHistoryStore } from "@/stores/history-store";
import { WorkspaceTabs } from "./workspace-tabs";

export function GeospatialWorkspace() {
  const state = useAnalysisStore();
  const [result, setResult] = useState<AnalysisResponse>();
  const validation = useMutation({
    mutationFn: ({ geometry }: { geometry: NonNullable<typeof state.geometry>; revision: number }) => validateGeometry(geometry),
  });
  const addHistoryEntry = useHistoryStore((store) => store.addEntry);
  const analysis = useMutation({
    mutationFn: runAnalysis,
    onSuccess: (data, variables) => {
      setResult(data);
      state.setField("activeTab", "result");
      addHistoryEntry(data, variables.geometry as NonNullable<typeof state.geometry>);
    },
  });

  const handleValidate = () => {
    if (!state.geometry) return;
    validation.reset();
    const revision = state.geometryRevision;
    validation.mutate({ geometry: state.geometry, revision }, {
      onSuccess: (data) => state.applyGeometryValidation(data, revision),
    });
  };

  const handleRun = () => {
    const current = useAnalysisStore.getState();
    if (!current.geometry || !isCurrentGeometryValidated(current)) return;
    analysis.reset();
    analysis.mutate({ geometry: current.geometry });
  };

  const error = validation.error ?? analysis.error;
  const errorMessage = error ? messageFrom(error) : undefined;

  return (
    <div className="geospatial-page">
      <div className="product-hero">
        <div className="product-hero-copy">
          <span className="product-hero-eyebrow">Painel operacional</span>
          <h1>Motiva Faixa Verde</h1>
          <p>Monitoramento inteligente da vegetação lateral rodoviária</p>
        </div>
      </div>
      <div className="geospatial-workspace">
        <section className="map-workspace" aria-label="Workspace geoespacial">
          <AnalysisMap
            result={result}
            validationFailed={Boolean(validation.error && validation.variables?.revision === state.geometryRevision)}
          />
        </section>
        <aside className="analysis-drawer" aria-label="Painel da análise">
          <WorkspaceTabs active={state.activeTab} onChange={(tab) => state.setField("activeTab", tab)} hasResult={Boolean(result)} />
          <div role="tabpanel">
            {state.activeTab === "area" && <AreaPanel validating={validation.isPending} running={analysis.isPending} error={errorMessage} onValidate={handleValidate} onRun={handleRun} />}
            {state.activeTab === "result" && <AnalysisResultSidebar result={result} onRetry={handleRun} />}
          </div>
        </aside>
      </div>
      {result && state.activeTab === "result" ? <section className="workspace-detail"><AnalysisResult result={result} /></section> : null}
    </div>
  );
}

function messageFrom(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "Não foi possível concluir a operação.";
}
