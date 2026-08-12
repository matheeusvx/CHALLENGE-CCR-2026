import { AlertTriangle, CheckCircle2, Clock3, Crosshair, Eraser, MapPin, Play, ScanSearch } from "lucide-react";
import { calculateGeometryPreview } from "@/lib/map/geometry";
import { formatArea } from "@/lib/utils/recommendation";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { AdvancedGeoJson } from "./advanced-geojson";

const sourceLabels = { drawn: "Desenhada no mapa", pasted: "GeoJSON colado", uploaded: "Arquivo enviado", predefined: "Predefinida" } as const;

type Props = {
  validating: boolean;
  running: boolean;
  error?: string;
  onValidate: () => void;
  onRun: () => void;
};

export function AreaPanel({ validating, running, error, onValidate, onRun }: Props) {
  const state = useAnalysisStore();
  const official = state.geometryValidation;
  const preview = state.geometry ? calculateGeometryPreview(state.geometry) : null;
  const currentValidation = isCurrentGeometryValidated(state);
  const data = official && currentValidation ? {
    area: official.area_square_meters,
    centroid: official.centroid,
    bbox: official.bounding_box,
    pixels: official.estimated_sentinel_pixels,
  } : preview ? {
    area: preview.areaSquareMeters,
    centroid: preview.centroid,
    bbox: preview.boundingBox,
    pixels: preview.estimatedSentinelPixels,
  } : null;

  const clear = () => {
    if (currentValidation && !window.confirm("Esta área já foi validada. Deseja limpá-la?")) return;
    state.clearGeometry();
  };

  return (
    <div className="workspace-panel-content">
      <div className={`geometry-state ${currentValidation ? "valid" : state.geometry ? "dirty" : "empty"}`}>
        {currentValidation ? <CheckCircle2 size={18} /> : state.geometry ? <AlertTriangle size={18} /> : <MapPin size={18} />}
        <div>
          <strong>{validating ? "Validando área" : currentValidation ? "Área validada" : state.geometry ? state.isGeometryDirty && state.lastValidatedGeometryRevision !== null ? "Área alterada após validação" : "Área aguardando validação" : "Nenhuma área delimitada"}</strong>
          <span>{state.geometry ? "Revise a área lateral selecionada antes de executar a análise." : "Desenhe um polígono sobre a faixa lateral que será analisada."}</span>
        </div>
      </div>

      {data ? (
        <dl className="geometry-metrics">
          <div className="metric-wide metric-area">
            <dt>Área selecionada</dt>
            <dd><span>{formatArea(data.area)}</span><small>{(data.area / 10_000).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} ha</small></dd>
          </div>
          <div><dt>Tipo</dt><dd>Polígono</dd></div>
          <div><dt>Origem</dt><dd>{state.geometrySource ? sourceLabels[state.geometrySource] : "-"}</dd></div>
          <div><dt>Pixels estimados</dt><dd>{data.pixels.toLocaleString("pt-BR")}</dd></div>
          <div><dt>Última validação</dt><dd>{state.lastValidatedAt ? new Date(state.lastValidatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—"}</dd></div>
          <div className="metric-wide metric-technical"><dt>Centroide</dt><dd className="mono">{data.centroid.longitude.toFixed(6)}, {data.centroid.latitude.toFixed(6)}</dd></div>
          <div className="metric-wide metric-technical"><dt>Bounding box</dt><dd className="mono">{data.bbox.map((value) => Number(value).toFixed(5)).join(" / ")}</dd></div>
        </dl>
      ) : <div className="panel-empty"><Crosshair size={24} /><p>Comece delimitando somente a vegetação lateral da rodovia. Evite pista, edificações, árvores densas e áreas urbanas.</p></div>}

      {official?.warnings.length ? <div className="panel-warnings"><strong>Avisos</strong>{official.warnings.map((warning) => <p key={warning}><AlertTriangle size={14} />{warning}</p>)}</div> : null}
      {error && <div className="inline-error" role="alert"><AlertTriangle size={16} />{error}</div>}
      {(validating || running) && <div className="inline-loading" role="status"><Clock3 className="spin" size={16} />{validating ? "Validando geometria pela API" : "Processando cenas Sentinel-2"}</div>}

      <div className="panel-actions">
        <button type="button" className="secondary-button" onClick={onValidate} disabled={!state.geometry || validating || running}><ScanSearch size={17} />Validar área</button>
        <button type="button" className="primary-button" onClick={onRun} disabled={!currentValidation || validating || running}><Play size={17} />Executar análise</button>
        <button type="button" className="quiet-action" onClick={state.requestGeometryFit} disabled={!state.geometry}><Crosshair size={15} />Enquadrar no mapa</button>
        <button type="button" className="quiet-action danger" onClick={clear} disabled={!state.geometry}><Eraser size={15} />Limpar área</button>
      </div>

      <AdvancedGeoJson />
    </div>
  );
}
