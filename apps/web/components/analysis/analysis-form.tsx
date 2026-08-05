"use client";

import { useMutation } from "@tanstack/react-query";
import { Play, ScanSearch } from "lucide-react";
import { useState } from "react";
import { runAnalysis, validateGeometry } from "@/lib/api/analyses";
import { ApiError } from "@/lib/api/client";
import { parseGeometryText, type AnalysisResponse } from "@/lib/schemas/analyses";
import { useAnalysisStore } from "@/stores/analysis-store";
import { AnalysisStatus } from "./analysis-status";
import { GeometryEditor } from "./geometry-editor";
import { GeometryValidationPanel } from "./geometry-validation";

type Props = { onResult: (result: AnalysisResponse) => void };

const messageFrom = (error: unknown) => error instanceof ApiError || error instanceof Error ? error.message : "Nao foi possivel concluir a operacao.";

export function AnalysisForm({ onResult }: Props) {
  const form = useAnalysisStore();
  const [localError, setLocalError] = useState<string>();
  const validation = useMutation({ mutationFn: validateGeometry });
  const analysis = useMutation({ mutationFn: runAnalysis, onSuccess: onResult });

  const handleValidate = () => {
    setLocalError(undefined);
    validation.reset();
    try {
      const geometry = parseGeometryText(form.geometryText);
      validation.mutate(geometry, { onSuccess: () => form.setField("geometryValidated", true) });
    } catch (error) {
      form.setField("geometryValidated", false);
      setLocalError(messageFrom(error));
    }
  };

  const handleRun = () => {
    setLocalError(undefined);
    try {
      const geometry = parseGeometryText(form.geometryText);
      analysis.mutate({
        geometry,
        start_date: form.startDate,
        end_date: form.endDate,
        max_cloud_cover: form.maxCloudCover,
        max_scenes: form.maxScenes,
        scene_order: "newest",
        min_valid_pixel_percentage: form.minValidPixelPercentage,
        min_observations: 4,
        daily_aggregation: form.dailyAggregation,
      });
    } catch (error) {
      setLocalError(messageFrom(error));
    }
  };

  const remoteError = validation.error ?? analysis.error;
  return (
    <section className="tool-panel" aria-labelledby="analysis-parameters">
      <div className="section-heading"><div><span>CONFIGURACAO</span><h2 id="analysis-parameters">Parametros da analise</h2></div><p>EPSG:4326 · longitude, latitude</p></div>
      <div className="form-grid">
        <GeometryEditor value={form.geometryText} onChange={(value) => form.setField("geometryText", value)} error={localError} />
        <div className="field"><label htmlFor="start-date">Data inicial</label><input id="start-date" type="date" value={form.startDate} onChange={(e) => form.setField("startDate", e.target.value)} /></div>
        <div className="field"><label htmlFor="end-date">Data final</label><input id="end-date" type="date" value={form.endDate} onChange={(e) => form.setField("endDate", e.target.value)} /></div>
        <div className="field"><label htmlFor="cloud">Cobertura maxima de nuvens (%)</label><input id="cloud" type="number" min="0" max="100" value={form.maxCloudCover} onChange={(e) => form.setField("maxCloudCover", Number(e.target.value))} /></div>
        <div className="field"><label htmlFor="scenes">Quantidade maxima de cenas</label><input id="scenes" type="number" min="1" max="100" value={form.maxScenes} onChange={(e) => form.setField("maxScenes", Number(e.target.value))} /></div>
        <div className="field"><label htmlFor="pixels">Pixels validos minimos (%)</label><input id="pixels" type="number" min="0" max="100" value={form.minValidPixelPercentage} onChange={(e) => form.setField("minValidPixelPercentage", Number(e.target.value))} /></div>
        <div className="field"><label htmlFor="aggregation">Agregacao diaria</label><select id="aggregation" value={form.dailyAggregation} onChange={(e) => form.setField("dailyAggregation", e.target.value as typeof form.dailyAggregation)}><option value="best">Melhor cena</option><option value="median">Mediana</option><option value="none">Sem consolidacao</option></select></div>
      </div>
      {validation.data && <GeometryValidationPanel data={validation.data} />}
      <AnalysisStatus loading={validation.isPending || analysis.isPending} error={remoteError ? messageFrom(remoteError) : undefined} />
      <div className="form-actions">
        <button type="button" className="secondary-button" onClick={handleValidate} disabled={validation.isPending || analysis.isPending}><ScanSearch size={17} />Validar area</button>
        <button type="button" className="primary-button" onClick={handleRun} disabled={!form.geometryValidated || validation.isPending || analysis.isPending}><Play size={17} />Executar analise</button>
      </div>
    </section>
  );
}
