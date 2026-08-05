"use client";

import { Check, Clipboard, Download, FileJson, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { MAP_CONFIG } from "@/lib/map/config";
import { formatGeometry, parseGeoJsonText } from "@/lib/map/geometry";
import { useAnalysisStore } from "@/stores/analysis-store";

export function AdvancedGeoJson() {
  const inputRef = useRef<HTMLInputElement>(null);
  const geometry = useAnalysisStore((state) => state.geometry);
  const geometryText = useAnalysisStore((state) => state.geometryText);
  const setGeometry = useAnalysisStore((state) => state.setGeometry);
  const requestFit = useAnalysisStore((state) => state.requestGeometryFit);
  const setField = useAnalysisStore((state) => state.setField);
  const [error, setError] = useState<string>();
  const [feedback, setFeedback] = useState<string>();

  const apply = (text: string, source: "pasted" | "uploaded") => {
    setError(undefined);
    setFeedback(undefined);
    try {
      const parsed = parseGeoJsonText(text);
      if (geometry && !window.confirm("Substituir a geometria atual pela nova area?")) return;
      setGeometry(parsed, source);
      setField("geometryText", formatGeometry(parsed));
      requestFit();
      setFeedback(source === "uploaded" ? "Arquivo aplicado ao mapa." : "GeoJSON aplicado ao mapa.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nao foi possivel ler a geometria.");
    }
  };

  const handleUpload = async (file?: File) => {
    if (!file) return;
    setError(undefined);
    if (!/\.(geojson|json)$/i.test(file.name)) {
      setError("Selecione um arquivo .geojson ou .json.");
      return;
    }
    if (file.size > MAP_CONFIG.uploadMaxBytes) {
      setError("O arquivo excede o limite de 2 MB.");
      return;
    }
    apply(await file.text(), "uploaded");
    if (inputRef.current) inputRef.current.value = "";
  };

  const format = () => {
    try {
      const parsed = parseGeoJsonText(geometryText);
      setField("geometryText", formatGeometry(parsed));
      setError(undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "JSON invalido.");
    }
  };

  const copyCurrent = async () => {
    if (!geometry) return;
    await navigator.clipboard.writeText(formatGeometry(geometry));
    setFeedback("Geometria copiada.");
  };

  const download = () => {
    if (!geometry) return;
    const url = URL.createObjectURL(new Blob([formatGeometry(geometry)], { type: "application/geo+json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "area-monitoramento.geojson";
    anchor.click();
    URL.revokeObjectURL(url);
    setFeedback("Download preparado.");
  };

  return (
    <details className="advanced-geojson">
      <summary><FileJson size={16} />Entrada avancada por GeoJSON</summary>
      <div className="advanced-body">
        <label htmlFor="advanced-geometry">GeoJSON em EPSG:4326</label>
        <textarea id="advanced-geometry" aria-label="GeoJSON da area de interesse" value={geometryText} onChange={(event) => setField("geometryText", event.target.value)} placeholder='{"type":"Polygon","coordinates":[...]}' spellCheck={false} />
        {error && <p className="field-error" role="alert">{error}</p>}
        {feedback && <p className="field-success" role="status"><Check size={14} />{feedback}</p>}
        <div className="advanced-actions">
          <button type="button" className="text-action" onClick={() => apply(geometryText, "pasted")} disabled={!geometryText.trim()}><Check size={15} />Aplicar</button>
          <button type="button" className="icon-action" title="Formatar JSON" aria-label="Formatar JSON" onClick={format} disabled={!geometryText.trim()}><FileJson size={16} /></button>
          <button type="button" className="icon-action" title="Copiar geometria atual" aria-label="Copiar geometria atual" onClick={copyCurrent} disabled={!geometry}><Clipboard size={16} /></button>
          <button type="button" className="icon-action" title="Baixar geometria atual" aria-label="Baixar geometria atual" onClick={download} disabled={!geometry}><Download size={16} /></button>
          <button type="button" className="icon-action" title="Importar GeoJSON" aria-label="Importar GeoJSON" onClick={() => inputRef.current?.click()}><Upload size={16} /></button>
          <input ref={inputRef} className="sr-only" type="file" accept=".geojson,.json,application/geo+json,application/json" aria-label="Selecionar arquivo GeoJSON" onChange={(event) => void handleUpload(event.target.files?.[0])} />
        </div>
      </div>
    </details>
  );
}

