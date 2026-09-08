"use client";

import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { AnalysisMap } from "@/components/map/analysis-map";
import { AreaPanel } from "@/components/analysis/area-panel";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import { AnalysisResultSidebar } from "@/components/analysis/analysis-result-sidebar";
import { runAnalysis, validateGeometry } from "@/lib/api/analyses";
import { ApiError } from "@/lib/api/client";
import { getCurrentAnalysisResponse, isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { useHistoryStore } from "@/stores/history-store";
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";
import { WorkspaceTabs } from "./workspace-tabs";

export function GeospatialWorkspace() {
  const state = useAnalysisStore();
  const previousGeometryRevision = useRef(state.geometryRevision);
  const [validationRevision, setValidationRevision] = useState<number | null>(null);
  const [analysisRevision, setAnalysisRevision] = useState<number | null>(null);
  const result = getCurrentAnalysisResponse(state);
  const validation = useMutation({
    mutationFn: ({ geometry }: { geometry: NonNullable<typeof state.geometry>; revision: number }) => validateGeometry(geometry),
  });
  const addHistoryEntry = useHistoryStore((store) => store.addEntry);
  const analysis = useMutation({
    mutationFn: ({ geometry }: { geometry: NonNullable<typeof state.geometry>; revision: number }) => runAnalysis({ geometry }),
    onSuccess: (data, variables) => {
      const current = useAnalysisStore.getState();
      if (variables.revision !== current.geometryRevision || !isCurrentGeometryValidated(current)) return;
      current.applyAnalysisResult(data, variables.revision);

      const props = data.aoi?.properties as Record<string, unknown> | undefined;
      let manualRoad = undefined;
      if (props && (props.road_ref || props.road_name)) {
        manualRoad = {
          id: props.road_id as string | undefined,
          ref: props.road_ref as string | undefined,
          name: props.road_name as string | undefined,
        };
      } else {
        const autoRoad = useAutoAnalysisStore.getState().road;
        if (autoRoad) manualRoad = autoRoad;
      }

      addHistoryEntry(data, variables.geometry, current.geometryValidation ?? undefined, data.road ?? manualRoad);
    },
  });

  useEffect(() => {
    if (previousGeometryRevision.current === state.geometryRevision) return;
    previousGeometryRevision.current = state.geometryRevision;
    validation.reset();
    analysis.reset();
    // Mutation state belongs to the geometry revision that created it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.geometryRevision]);

  const handleValidate = () => {
    if (!state.geometry) return;
    validation.reset();
    const revision = state.geometryRevision;
    setValidationRevision(revision);
    validation.mutate({ geometry: state.geometry, revision }, {
      onSuccess: (data) => state.applyGeometryValidation(data, revision),
    });
  };

  const handleRun = () => {
    const current = useAnalysisStore.getState();
    if (!current.geometry || !isCurrentGeometryValidated(current)) return;
    analysis.reset();
    setAnalysisRevision(current.geometryRevision);
    analysis.mutate({ geometry: current.geometry, revision: current.geometryRevision });
  };

  const validationIsCurrent = validationRevision === state.geometryRevision;
  const analysisIsCurrent = analysisRevision === state.geometryRevision;
  const error = analysisIsCurrent && analysis.error
    ? analysis.error
    : validationIsCurrent
      ? validation.error
      : null;
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
            validationFailed={Boolean(validation.error && validationIsCurrent)}
          />
        </section>
        <aside className="analysis-drawer" aria-label="Painel da análise" data-tour="area-panel">
          <WorkspaceTabs active={state.activeTab} onChange={(tab) => state.setField("activeTab", tab)} hasResult={Boolean(result)} data-tour="result-panel" />
          <div role="tabpanel">
            {state.activeTab === "area" && <AreaPanel validating={validation.isPending && validationIsCurrent} running={analysis.isPending && analysisIsCurrent} error={errorMessage} onValidate={handleValidate} onRun={handleRun} />}
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
