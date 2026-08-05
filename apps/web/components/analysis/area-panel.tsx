import { AlertTriangle, CheckCircle2, Clock3, Crosshair, Eraser, MapPin, Play, ScanSearch } from "lucide-react";
import { calculateGeometryPreview } from "@/lib/map/geometry";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { AdvancedGeoJson } from "./advanced-geojson";

const sourceLabels = { drawn: "Desenhada", pasted: "GeoJSON colado", uploaded: "Arquivo enviado", predefined: "Predefinida" } as const;

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
    if (currentValidation && !window.confirm("Esta area ja foi validada. Deseja limpa-la?")) return;
    state.clearGeometry();
  };

  return (
    <div className="workspace-panel-content">
      <div className={`geometry-state ${currentValidation ? "valid" : state.geometry ? "dirty" : "empty"}`}>
        {currentValidation ? <CheckCircle2 size={18} /> : state.geometry ? <AlertTriangle size={18} /> : <MapPin size={18} />}
        <div><strong>{validating ? "Validando area" : currentValidation ? "Area validada" : state.geometry ? state.isGeometryDirty && state.lastValidatedGeometryRevision !== null ? "Area alterada apos validacao" : "Area aguardando validacao" : "Nenhuma area delimitada"}</strong><span>{state.geometry ? "A validacao oficial e realizada pela API." : "Use a ferramenta de poligono sobre o mapa."}</span></div>
      </div>

      {data ? (
        <dl className="geometry-metrics">
          <div><dt>Tipo</dt><dd>Polygon</dd></div>
          <div><dt>Origem</dt><dd>{state.geometrySource ? sourceLabels[state.geometrySource] : "-"}</dd></div>
          <div className="metric-wide"><dt>Area</dt><dd>{formatArea(data.area)} <small>{(data.area / 10_000).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} ha</small></dd></div>
          <div className="metric-wide"><dt>Centroide</dt><dd className="mono">{data.centroid.longitude.toFixed(6)}, {data.centroid.latitude.toFixed(6)}</dd></div>
          <div className="metric-wide"><dt>Bounding box</dt><dd className="mono">{data.bbox.map((value) => Number(value).toFixed(5)).join(" / ")}</dd></div>
          <div><dt>Pixels estimados</dt><dd>{data.pixels.toLocaleString("pt-BR")}</dd></div>
          <div><dt>Ultima validacao</dt><dd>{state.lastValidatedAt ? new Date(state.lastValidatedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "-"}</dd></div>
        </dl>
      ) : <div className="panel-empty"><Crosshair size={24} /><p>Desenhe um poligono sobre a faixa lateral gramada que sera analisada.</p></div>}

      {official?.warnings.length ? <div className="panel-warnings"><strong>Avisos</strong>{official.warnings.map((warning) => <p key={warning}><AlertTriangle size={14} />{warning}</p>)}</div> : null}
      {error && <div className="inline-error" role="alert"><AlertTriangle size={16} />{error}</div>}
      {(validating || running) && <div className="inline-loading" role="status"><Clock3 className="spin" size={16} />{validating ? "Validando geometria pela API" : "Processando cenas Sentinel-2"}</div>}

      <div className="panel-actions">
        <button type="button" className="secondary-button" onClick={onValidate} disabled={!state.geometry || validating || running}><ScanSearch size={17} />Validar area</button>
        <button type="button" className="primary-button" onClick={onRun} disabled={!currentValidation || validating || running}><Play size={17} />Executar analise</button>
        <button type="button" className="quiet-action" onClick={state.requestGeometryFit} disabled={!state.geometry}><Crosshair size={15} />Enquadrar no mapa</button>
        <button type="button" className="quiet-action danger" onClick={clear} disabled={!state.geometry}><Eraser size={15} />Limpar area</button>
      </div>

      <AdvancedGeoJson />
    </div>
  );
}

function formatArea(area: number) {
  return `${Math.round(area).toLocaleString("pt-BR")} m2`;
}

