"use client";

import { Database, Layers, Radio, Ruler, Satellite, Waypoints } from "lucide-react";
import { dataSources } from "@/lib/app-config";

const statusLabels = { active: "Ativa", planned: "Planejada" } as const;

export function SourcesView() {
  return (
    <section className="secondary-view" aria-labelledby="secondary-view-sources">
      <header>
        <span>Dados de monitoramento</span>
        <h1 id="secondary-view-sources">Fontes de dados</h1>
        <p>Origens de imagens e índices usados nas análises. Novas fontes aparecem automaticamente nesta lista.</p>
      </header>

      <ul className="source-list">
        {dataSources.map((source) => (
          <li key={source.id} className="source-card">
            <div className="source-card-head">
              <span className="source-card-icon" aria-hidden="true"><Satellite size={22} /></span>
              <div className="source-card-title">
                <strong>{source.name}</strong>
                <span>{source.provider}</span>
              </div>
              <span className={`source-status ${source.status}`}>{statusLabels[source.status]}</span>
            </div>

            <dl className="source-card-specs">
              <div><Database size={15} /><dt>Tipo</dt><dd>{source.kind}</dd></div>
              <div><Ruler size={15} /><dt>Resolução</dt><dd>{source.resolution}</dd></div>
              <div><Radio size={15} /><dt>Frequência</dt><dd>{source.revisit}</dd></div>
              <div><Layers size={15} /><dt>Bandas</dt><dd className="source-bands">{source.bands.map((band) => <span key={band}>{band}</span>)}</dd></div>
            </dl>

            <p className="source-card-usage"><Waypoints size={15} aria-hidden="true" />{source.usage}</p>
          </li>
        ))}
      </ul>

      <p className="view-note">As fontes são consultadas apenas para a área delimitada pelo operador, sem armazenar imagens completas.</p>
    </section>
  );
}
