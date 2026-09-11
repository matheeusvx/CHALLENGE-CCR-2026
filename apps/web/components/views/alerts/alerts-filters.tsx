import { Filter } from "lucide-react";
import type { AlertSeverityValue } from "@/lib/schemas/alerts";
import { SEVERITY_LABELS, TYPE_FILTERS, STATUS_LABELS } from "@/lib/utils/alerts";

export type AlertRoadOption = { value: string; label: string };

type AlertsFiltersProps = {
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  selectedRoad: string;
  onRoadChange: (road: string) => void;
  availableRoads: AlertRoadOption[];
  selectedSeverity: AlertSeverityValue | "";
  onSeverityChange: (severity: AlertSeverityValue | "") => void;
  selectedStatus: string;
  onStatusChange: (status: string) => void;
};

export function AlertsFilters({
  activeCategory,
  onCategoryChange,
  selectedRoad,
  onRoadChange,
  availableRoads,
  selectedSeverity,
  onSeverityChange,
  selectedStatus,
  onStatusChange,
}: AlertsFiltersProps) {
  return (
    <div className="alerts-filters-bar" role="toolbar" aria-label="Filtros de alertas">
      {/* Pílulas de categorias rápidas */}
      <div className="alerts-type-pills" role="radiogroup" aria-label="Filtro rápido">
        {TYPE_FILTERS.map((pill) => (
          <button
            key={pill.id}
            type="button"
            className={`filter-pill ${activeCategory === pill.id ? "active" : ""}`}
            onClick={() => onCategoryChange(pill.id)}
            aria-pressed={activeCategory === pill.id}
            data-testid={`filter-category-${pill.id}`}
          >
            {pill.label}
          </button>
        ))}
      </div>

      {/* Filtros secundários compactos: Rodovia e Status */}
      <div className="alerts-secondary-filters">
        <div className="alerts-filter-control">
          <label htmlFor="filter-road" className="sr-only">
            Filtrar por rodovia
          </label>
          <select
            id="filter-road"
            value={selectedRoad}
            onChange={(e) => onRoadChange(e.target.value)}
            className="alerts-select"
            data-testid="filter-road-select"
            aria-label="Filtrar por rodovia"
          >
            <option value="">Todas as rodovias</option>
            {availableRoads.map((road) => (
              <option key={road.value} value={road.value}>
                {road.label}
              </option>
            ))}
          </select>
        </div>

        <div className="alerts-filter-control">
          <label htmlFor="filter-severity" className="sr-only">Filtrar por severidade</label>
          <select
            id="filter-severity"
            value={selectedSeverity}
            onChange={(event) =>
              onSeverityChange(event.target.value as AlertSeverityValue | "")
            }
            className="alerts-select"
            data-testid="filter-severity-select"
            aria-label="Filtrar por severidade"
          >
            <option value="">Todas as prioridades</option>
            <option value="critical">{SEVERITY_LABELS.critical}</option>
            <option value="high">{SEVERITY_LABELS.high}</option>
            <option value="medium">{SEVERITY_LABELS.medium}</option>
            <option value="low">{SEVERITY_LABELS.low}</option>
          </select>
        </div>

        <div className="alerts-filter-control">
          <label htmlFor="filter-status" className="sr-only">
            Filtrar por status
          </label>
          <select
            id="filter-status"
            value={selectedStatus}
            onChange={(e) => onStatusChange(e.target.value)}
            className="alerts-select"
            data-testid="filter-status-select"
            aria-label="Filtrar por status"
          >
            <option value="">Todos os status</option>
            <option value="new">{STATUS_LABELS.new}</option>
            <option value="seen">{STATUS_LABELS.seen}</option>
            <option value="monitoring">{STATUS_LABELS.monitoring}</option>
            <option value="resolved">{STATUS_LABELS.resolved}</option>
          </select>
        </div>

        {(activeCategory !== "all" || selectedRoad || selectedSeverity || selectedStatus) && (
          <button
            type="button"
            className="quiet-action"
            onClick={() => {
              onCategoryChange("all");
              onRoadChange("");
              onSeverityChange("");
              onStatusChange("");
            }}
            data-testid="clear-filters-btn"
            title="Limpar todos os filtros"
          >
            <Filter size={13} aria-hidden="true" />
            <span>Limpar filtros</span>
          </button>
        )}
      </div>
    </div>
  );
}
