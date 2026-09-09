import centroid from "@turf/centroid";
import type { Feature, FeatureCollection, Polygon } from "geojson";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap, MapLayerMouseEvent, Popup as MapPopup } from "maplibre-gl";
import type { SpatialZone } from "@/lib/schemas/analyses";
import {
  decisionLabels,
  formatArea,
  formatAnalysisQuality,
  levelLabels,
  recommendationReasonLabel,
} from "@/lib/utils/recommendation";

// ---------------------------------------------------------------------------
// Layer IDs
// ---------------------------------------------------------------------------

export const ZONES_LAYER_IDS = {
  source: "spatial-zones-source",
  fill: "spatial-zones-fill",
  outline: "spatial-zones-outline",
} as const;

// ---------------------------------------------------------------------------
// Visual styles per decision
// ---------------------------------------------------------------------------

const ZONE_COLORS = {
  cortar: {
    fill: "rgba(224, 86, 36, 0.45)",
    fillHover: "rgba(224, 86, 36, 0.70)",
    outline: "#e05624",
    outlineHover: "#ff6b35",
  },
  nao_cortar: {
    fill: "rgba(39, 134, 91, 0.40)",
    fillHover: "rgba(39, 134, 91, 0.65)",
    outline: "#27865b",
    outlineHover: "#34b077",
  },
  inconclusivo: {
    fill: "rgba(180, 130, 20, 0.38)",
    fillHover: "rgba(180, 130, 20, 0.60)",
    outline: "#b48214",
    outlineHover: "#d99e1a",
  },
};

const FALLBACK_ZONE_COLOR = {
  fill: "rgba(128, 128, 128, 0.25)",
  fillHover: "rgba(128, 128, 128, 0.45)",
  outline: "#808080",
  outlineHover: "#a0a0a0",
};

// ---------------------------------------------------------------------------
// Feature collection
// ---------------------------------------------------------------------------

type ZoneProperties = {
  zone_id: string;
  recommendation: string;
  area_m2: number;
  confidence: string;
  analysis_quality: string;
  reasons: string[];
  start_distance_m: number;
  end_distance_m: number;
  road_ref: string;
};

function zonesToFeatureCollection(zones: SpatialZone[]): FeatureCollection<Polygon, ZoneProperties> {
  return {
    type: "FeatureCollection",
    features: zones.map((zone) => ({
      type: "Feature" as const,
      id: zone.zone_id,
      geometry: zone.geometry as unknown as Polygon,
      properties: {
        zone_id: zone.zone_id,
        recommendation: zone.recommendation,
        area_m2: zone.area_m2,
        confidence: zone.confidence,
        analysis_quality: zone.analysis_quality,
        reasons: zone.reasons,
        start_distance_m: zone.start_distance_m,
        end_distance_m: zone.end_distance_m,
        road_ref: zone.road_ref,
      },
    })),
  };
}

// ---------------------------------------------------------------------------
// Install / update / remove
// ---------------------------------------------------------------------------

export function installOrUpdateZonesLayer(map: MapLibreMap, zones: SpatialZone[]): void {
  if (zones.length === 0) {
    removeZonesLayer(map);
    return;
  }

  const data = zonesToFeatureCollection(zones);
  const source = map.getSource(ZONES_LAYER_IDS.source);
  if (source) {
    (source as maplibregl.GeoJSONSource).setData(data);
  } else {
    map.addSource(ZONES_LAYER_IDS.source, { type: "geojson", data });
  }

  if (!map.getLayer(ZONES_LAYER_IDS.fill)) {
    map.addLayer({
      id: ZONES_LAYER_IDS.fill,
      type: "fill",
      source: ZONES_LAYER_IDS.source,
      paint: {
        "fill-color": [
          "case",
          ["boolean", ["feature-state", "hover"], false],
          [
            "match", ["get", "recommendation"],
            "cortar", ZONE_COLORS.cortar.fillHover,
            "nao_cortar", ZONE_COLORS.nao_cortar.fillHover,
            "inconclusivo", ZONE_COLORS.inconclusivo.fillHover,
            FALLBACK_ZONE_COLOR.fillHover,
          ],
          [
            "match", ["get", "recommendation"],
            "cortar", ZONE_COLORS.cortar.fill,
            "nao_cortar", ZONE_COLORS.nao_cortar.fill,
            "inconclusivo", ZONE_COLORS.inconclusivo.fill,
            FALLBACK_ZONE_COLOR.fill,
          ],
        ],
        "fill-opacity": 1,
      },
    });
  }

  if (!map.getLayer(ZONES_LAYER_IDS.outline)) {
    map.addLayer({
      id: ZONES_LAYER_IDS.outline,
      type: "line",
      source: ZONES_LAYER_IDS.source,
      paint: {
        "line-color": [
          "case",
          ["boolean", ["feature-state", "hover"], false],
          [
            "match", ["get", "recommendation"],
            "cortar", ZONE_COLORS.cortar.outlineHover,
            "nao_cortar", ZONE_COLORS.nao_cortar.outlineHover,
            "inconclusivo", ZONE_COLORS.inconclusivo.outlineHover,
            FALLBACK_ZONE_COLOR.outlineHover,
          ],
          [
            "match", ["get", "recommendation"],
            "cortar", ZONE_COLORS.cortar.outline,
            "nao_cortar", ZONE_COLORS.nao_cortar.outline,
            "inconclusivo", ZONE_COLORS.inconclusivo.outline,
            FALLBACK_ZONE_COLOR.outline,
          ],
        ],
        "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 4, 2.5],
        "line-opacity": 1,
      },
    });
  }
}

export function removeZonesLayer(map: MapLibreMap): void {
  [ZONES_LAYER_IDS.outline, ZONES_LAYER_IDS.fill].forEach((id) => {
    if (map.getLayer(id)) map.removeLayer(id);
  });
  if (map.getSource(ZONES_LAYER_IDS.source)) map.removeSource(ZONES_LAYER_IDS.source);
}

export function bringZonesLayersToFront(map: MapLibreMap): void {
  if (typeof map.moveLayer !== "function") return;
  [ZONES_LAYER_IDS.fill, ZONES_LAYER_IDS.outline].forEach((id) => {
    if (map.getLayer(id)) map.moveLayer(id);
  });
}

// ---------------------------------------------------------------------------
// Click & Hover interaction — shows popup with zone details
// ---------------------------------------------------------------------------

export function installZoneClickInteraction(map: MapLibreMap): { dispose: () => void; popupRef: MapPopup | null } {
  let popup: MapPopup | null = null;
  let hoveredZoneId: string | number | undefined;

  const handleClick = (e: MapLayerMouseEvent) => {
    const feature = e.features?.[0];
    if (!feature || !feature.properties) return;

    const props = feature.properties;
    const decision = String(props.recommendation ?? "");
    const label = decisionLabels[decision as keyof typeof decisionLabels] ?? decision.toUpperCase();
    const confidence = levelLabels[props.confidence as keyof typeof levelLabels] ?? String(props.confidence);
    const quality = formatAnalysisQuality(
      props.analysis_quality === "high" || props.analysis_quality === "medium" || props.analysis_quality === "low"
        ? props.analysis_quality
        : null,
    );
    const area = formatArea(typeof props.area_m2 === "number" ? props.area_m2 : null);
    const roadRef = String(props.road_ref ?? "-");

    // Parse reasons — MapLibre serializes arrays as strings
    let reasons: string[] = [];
    try {
      const raw = props.reasons;
      reasons = typeof raw === "string" ? JSON.parse(raw) : Array.isArray(raw) ? raw : [];
    } catch {
      reasons = [];
    }

    popup?.remove();

    const container = document.createElement("div");
    container.className = `zone-popup-content ${decision}`;

    container.innerHTML = `
      <div class="zone-popup-header">
        <span class="zone-popup-label">Zona · ${roadRef}</span>
        <strong class="zone-popup-decision">${label}</strong>
      </div>
      <dl class="zone-popup-metrics">
        <div><dt>Área</dt><dd>${area}</dd></div>
        <div><dt>Confiança</dt><dd>${confidence}</dd></div>
        <div><dt>Qualidade</dt><dd>${quality}</dd></div>
        <div><dt>Trecho</dt><dd>${Math.round(props.start_distance_m ?? 0)}m – ${Math.round(props.end_distance_m ?? 0)}m</dd></div>
      </dl>
      ${reasons.length ? `<div class="zone-popup-reasons"><strong>Motivos</strong><ul>${reasons.map((r: string) => `<li>${recommendationReasonLabel(r)}</li>`).join("")}</ul></div>` : ""}
    `;

    // Compute centroid of the clicked feature
    let lngLat = e.lngLat;
    try {
      const c = centroid(feature as unknown as Feature<Polygon>);
      lngLat = new maplibregl.LngLat(c.geometry.coordinates[0], c.geometry.coordinates[1]);
    } catch {
      // fallback to click position
    }

    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px", className: "zone-detail-popup" })
      .setLngLat(lngLat)
      .setDOMContent(container)
      .addTo(map);
  };

  const handleEnter = (e: MapLayerMouseEvent) => {
    map.getCanvas().style.cursor = "pointer";
    const id = e.features?.[0]?.id;
    if (id !== undefined && id !== hoveredZoneId) {
      if (hoveredZoneId !== undefined) {
        try { map.setFeatureState({ source: ZONES_LAYER_IDS.source, id: hoveredZoneId }, { hover: false }); } catch {}
      }
      hoveredZoneId = id;
      try { map.setFeatureState({ source: ZONES_LAYER_IDS.source, id }, { hover: true }); } catch {}
    }
  };

  const handleMove = (e: MapLayerMouseEvent) => {
    const id = e.features?.[0]?.id;
    if (id !== undefined && id !== hoveredZoneId) {
      if (hoveredZoneId !== undefined) {
        try { map.setFeatureState({ source: ZONES_LAYER_IDS.source, id: hoveredZoneId }, { hover: false }); } catch {}
      }
      hoveredZoneId = id;
      try { map.setFeatureState({ source: ZONES_LAYER_IDS.source, id }, { hover: true }); } catch {}
    }
  };

  const handleLeave = () => {
    map.getCanvas().style.cursor = "";
    if (hoveredZoneId !== undefined) {
      try { map.setFeatureState({ source: ZONES_LAYER_IDS.source, id: hoveredZoneId }, { hover: false }); } catch {}
      hoveredZoneId = undefined;
    }
  };

  map.on("click", ZONES_LAYER_IDS.fill, handleClick);
  map.on("mouseenter", ZONES_LAYER_IDS.fill, handleEnter);
  map.on("mousemove", ZONES_LAYER_IDS.fill, handleMove);
  map.on("mouseleave", ZONES_LAYER_IDS.fill, handleLeave);

  return {
    get popupRef() { return popup; },
    dispose: () => {
      map.off("click", ZONES_LAYER_IDS.fill, handleClick);
      map.off("mouseenter", ZONES_LAYER_IDS.fill, handleEnter);
      map.off("mousemove", ZONES_LAYER_IDS.fill, handleMove);
      map.off("mouseleave", ZONES_LAYER_IDS.fill, handleLeave);
      handleLeave();
      popup?.remove();
      popup = null;
    },
  };
}

