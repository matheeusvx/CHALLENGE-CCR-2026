import type { AnalysisResponse } from "@/lib/schemas/analyses";

const display = (value: unknown) => value === null || value === undefined || value === "" ? "-" : String(value);
const percentage = (value: unknown) => typeof value === "number" ? `${value.toFixed(1)}%` : "-";

export function ScenesTable({ scenes }: { scenes: AnalysisResponse["scenes"] }) {
  return (
    <section className="table-panel" aria-labelledby="scenes-title">
      <div className="section-heading"><div><span>RASTREABILIDADE</span><h2 id="scenes-title">Cenas Sentinel-2</h2></div><p>{scenes.length} registros</p></div>
      {scenes.length ? <div className="table-scroll"><table><thead><tr><th>Data</th><th>Item Sentinel-2</th><th>Nuvens</th><th>Pixels validos</th><th>Qualidade</th><th>Aceita</th><th>Agregacao</th></tr></thead><tbody>{scenes.map((scene, index) => <tr key={`${display(scene.item_id)}-${index}`}><td>{display(scene.datetime).slice(0, 10)}</td><td className="mono">{display(scene.item_id)}</td><td>{percentage(scene.cloud_cover)}</td><td>{percentage(scene.valid_pixel_percentage)}</td><td><span className={`quality quality-${display(scene.quality_status)}`}>{display(scene.quality_status)}</span></td><td>{scene.accepted_for_timeseries ? "Sim" : "Nao"}</td><td>{display(scene.daily_aggregation ?? scene.processing_status)}</td></tr>)}</tbody></table></div> : <div className="empty-state">Nenhuma cena foi retornada para esta execucao.</div>}
    </section>
  );
}
