"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Polygon } from "geojson";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap, Popup as MapPopup } from "maplibre-gl";
import {
  TerraDraw,
  TerraDrawPolygonMode,
  TerraDrawRenderMode,
  TerraDrawSelectMode,
  TerraDrawSessionUndoRedo,
  type GeoJSONStoreFeatures,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import { getAoiVisualState } from "@/lib/map/aoi-visual-state";
import { MAP_CONFIG, MAP_STYLES, type MapStyleId } from "@/lib/map/config";
import { fitMapToGeometry } from "@/lib/map/fit-map-to-geometry";
import type { PolygonGeometry } from "@/lib/map/geometry";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { DrawingControls } from "./drawing-controls";
import { installAoiHoverInteractions, installOrUpdateAoiLayer } from "./layers/aoi-layer";
import { MapLegend } from "./map-legend";
import { MapStatus } from "./map-status";
import { MapStyleSelector } from "./map-style-selector";

type Props = { result?: AnalysisResponse; validationFailed?: boolean };

export function MapCanvas({ result, validationFailed = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const drawRef = useRef<TerraDraw | null>(null);
  const popupRef = useRef<MapPopup | null>(null);
  const applyingGeometry = useRef(false);
  const disposeEditorRef = useRef<() => void>(() => undefined);
  const disposeHoverRef = useRef<() => void>(() => undefined);
  const initialGeometryFitDone = useRef(false);
  const geometry = useAnalysisStore((state) => state.geometry);
  const geometryRevision = useAnalysisStore((state) => state.geometryRevision);
  const geometryValidation = useAnalysisStore((state) => state.geometryValidation);
  const isGeometryDirty = useAnalysisStore((state) => state.isGeometryDirty);
  const selectedTool = useAnalysisStore((state) => state.selectedTool);
  const activeMapStyle = useAnalysisStore((state) => state.activeMapStyle);
  const activeTab = useAnalysisStore((state) => state.activeTab);
  const viewport = useAnalysisStore((state) => state.mapViewport);
  const fitRequestId = useAnalysisStore((state) => state.fitRequestId);
  const setGeometry = useAnalysisStore((state) => state.setGeometry);
  const clearGeometry = useAnalysisStore((state) => state.clearGeometry);
  const setSelectedTool = useAnalysisStore((state) => state.setSelectedTool);
  const setActiveMapStyle = useAnalysisStore((state) => state.setActiveMapStyle);
  const setMapViewport = useAnalysisStore((state) => state.setMapViewport);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [historyState, setHistoryState] = useState({ canUndo: false, canRedo: false });

  const validated = isCurrentGeometryValidated(useAnalysisStore.getState());
  const aoiVisualState = useMemo(() => getAoiVisualState({
    editing: Boolean(geometry && (selectedTool === "draw" || selectedTool === "edit")),
    dirty: isGeometryDirty,
    validation: validationFailed ? "invalid" : validated ? "valid" : null,
    recommendation: validated && !isGeometryDirty ? result?.recommendation.decision : undefined,
  }), [geometry, isGeometryDirty, result?.recommendation.decision, selectedTool, validated, validationFailed]);

  const geometryRef = useRef(geometry);
  const visualStateRef = useRef(aoiVisualState);

  useEffect(() => {
    geometryRef.current = geometry;
    visualStateRef.current = aoiVisualState;
  }, [aoiVisualState, geometry]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLES[useAnalysisStore.getState().activeMapStyle].styleUrl,
      center: [viewport.longitude, viewport.latitude],
      zoom: viewport.zoom,
      bearing: viewport.bearing,
      pitch: viewport.pitch,
      minZoom: MAP_CONFIG.minZoom,
      maxZoom: MAP_CONFIG.maxZoom,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");

    const disposeEditor = () => {
      disposeEditorRef.current();
      disposeEditorRef.current = () => undefined;
      drawRef.current = null;
    };

    const initializeEditor = () => {
      disposeEditor();
      const draw = createEditor(map, applyingGeometry, {
        onGeometryChange: (nextGeometry) => setGeometry(nextGeometry, "drawn"),
        onFinish: (nextGeometry) => {
          setGeometry(nextGeometry, "drawn");
          setSelectedTool("navigate");
          fitMapToGeometry(map, nextGeometry);
        },
        onDelete: clearGeometry,
        onHistoryChange: setHistoryState,
      });
      drawRef.current = draw.instance;
      disposeEditorRef.current = draw.dispose;

      const currentGeometry = useAnalysisStore.getState().geometry;
      if (currentGeometry) {
        replaceDrawGeometry(draw.instance, currentGeometry, applyingGeometry);
        if (!initialGeometryFitDone.current) {
          fitMapToGeometry(map, currentGeometry);
          initialGeometryFitDone.current = true;
        }
      }
      setDrawMode(draw.instance, useAnalysisStore.getState().selectedTool);
    };

    const installOperationalLayers = () => {
      disposeHoverRef.current();
      installOrUpdateAoiLayer(map, geometryRef.current, visualStateRef.current);
      disposeHoverRef.current = geometryRef.current ? installAoiHoverInteractions(map) : () => undefined;
    };

    const handleStyleLoad = () => {
      try {
        installOperationalLayers();
        initializeEditor();
        setLoading(false);
        setError(undefined);
      } catch (reason) {
        setLoading(false);
        setError(reason instanceof Error ? `Falha ao iniciar o editor: ${reason.message}` : "Falha ao iniciar o editor de geometria.");
      }
    };
    const handleError = (event: maplibregl.ErrorEvent) => {
      if (!map.isStyleLoaded()) {
        setLoading(false);
        setError(event.error?.message ? `Base cartografica indisponivel: ${event.error.message}` : "Nao foi possivel carregar a base cartografica.");
      }
    };
    const handleMoveEnd = () => {
      const center = map.getCenter();
      setMapViewport({ longitude: center.lng, latitude: center.lat, zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
    };

    map.on("style.load", handleStyleLoad);
    map.on("error", handleError);
    map.on("moveend", handleMoveEnd);

    return () => {
      disposeHoverRef.current();
      disposeEditor();
      popupRef.current?.remove();
      map.off("style.load", handleStyleLoad);
      map.off("error", handleError);
      map.off("moveend", handleMoveEnd);
      mapRef.current = null;
      map.remove();
    };
  // The map is deliberately created once; mutable refs carry current AOI state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const draw = drawRef.current;
    if (draw) setDrawMode(draw, selectedTool);
  }, [selectedTool]);

  useEffect(() => {
    const draw = drawRef.current;
    if (!draw) return;
    const existing = draw.getSnapshot().find((item) => item.geometry.type === "Polygon");
    const same = geometry && existing && JSON.stringify(existing.geometry) === JSON.stringify(geometry);
    if (!same) replaceDrawGeometry(draw, geometry, applyingGeometry);
  }, [geometry, geometryRevision]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    disposeHoverRef.current();
    installOrUpdateAoiLayer(map, geometry, aoiVisualState);
    disposeHoverRef.current = geometry ? installAoiHoverInteractions(map) : () => undefined;
  }, [aoiVisualState, geometry]);

  useEffect(() => {
    if (fitRequestId > 0 && geometry && mapRef.current) fitMapToGeometry(mapRef.current, geometry);
  }, [fitRequestId, geometry]);

  const previousResultTab = useRef(activeTab);
  const lastFittedAnalysis = useRef<string | undefined>(undefined);
  useEffect(() => {
    const reopenedResult = activeTab === "result" && previousResultTab.current !== "result";
    const newAnalysis = Boolean(result && result.analysis_id !== lastFittedAnalysis.current);
    if (geometry && mapRef.current && (reopenedResult || newAnalysis)) fitMapToGeometry(mapRef.current, geometry);
    if (result) lastFittedAnalysis.current = result.analysis_id;
    previousResultTab.current = activeTab;
  }, [activeTab, geometry, result]);

  useEffect(() => {
    popupRef.current?.remove();
    popupRef.current = null;
    const map = mapRef.current;
    if (!map || !result || !geometry || !validated || isGeometryDirty || !geometryValidation) return;
    const popupNode = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = decisionLabel(result.recommendation.decision);
    const details = document.createElement("span");
    const range = result.summary.date_range_effectively_processed as { start?: string; end?: string } | undefined;
    details.textContent = `Confianca ${result.recommendation.confidence} | ${range?.start ?? "-"} a ${range?.end ?? "-"}`;
    popupNode.className = "map-result-popup";
    popupNode.append(title, details);
    popupRef.current = new maplibregl.Popup({ closeButton: false, offset: 12 })
      .setLngLat([geometryValidation.centroid.longitude, geometryValidation.centroid.latitude])
      .setDOMContent(popupNode)
      .addTo(map);
  }, [geometry, geometryValidation, isGeometryDirty, result, validated]);

  const changeMapStyle = (style: MapStyleId) => {
    const map = mapRef.current;
    if (!map || style === activeMapStyle) return;
    disposeHoverRef.current();
    disposeEditorRef.current();
    disposeEditorRef.current = () => undefined;
    drawRef.current = null;
    setLoading(true);
    setError(undefined);
    setActiveMapStyle(style);
    map.setStyle(MAP_STYLES[style].styleUrl);
  };

  const deleteGeometry = () => {
    const state = useAnalysisStore.getState();
    if (isCurrentGeometryValidated(state) && !window.confirm("Esta area ja foi validada. Deseja exclui-la?")) return;
    drawRef.current?.clear();
    clearGeometry();
  };

  const replayHistory = (direction: "undo" | "redo") => {
    const draw = drawRef.current;
    if (!draw) return;
    if (direction === "undo") draw.undo();
    else draw.redo();
    const item = draw.getSnapshot().find((feature) => feature.geometry.type === "Polygon");
    if (item) setGeometry(item.geometry as PolygonGeometry, "drawn");
    else clearGeometry();
    setHistoryState({ canUndo: draw.canUndo(), canRedo: draw.canRedo() });
  };

  return (
    <div className="map-stage" tabIndex={0} onKeyDown={(event) => {
      if (event.key === "Escape") setSelectedTool("navigate");
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        replayHistory(event.shiftKey ? "redo" : "undo");
      }
    }}>
      <div ref={containerRef} className="map-canvas" aria-label="Mapa para delimitacao da area rodoviaria" />
      <DrawingControls
        tool={selectedTool}
        hasGeometry={Boolean(geometry)}
        canUndo={historyState.canUndo}
        canRedo={historyState.canRedo}
        onTool={setSelectedTool}
        onDelete={deleteGeometry}
        onFit={() => geometry && mapRef.current && fitMapToGeometry(mapRef.current, geometry)}
        onUndo={() => replayHistory("undo")}
        onRedo={() => replayHistory("redo")}
      />
      <MapStatus loading={loading} error={error} tool={selectedTool} />
      <MapStyleSelector active={activeMapStyle} onChange={changeMapStyle} />
      <MapLegend mapStyle={activeMapStyle} aoiState={aoiVisualState} hasGeometry={Boolean(geometry)} />
    </div>
  );
}

const drawingStyles = {
  polygonFillColor: "#7c3aed" as const,
  polygonFillOpacity: 0.22,
  polygonOutlineColor: "#6d28d9" as const,
  polygonOutlineWidth: 4,
};

const polygonDrawingStyles = {
  fillColor: "#7c3aed" as const,
  fillOpacity: 0.22,
  outlineColor: "#6d28d9" as const,
  outlineWidth: 4,
  closingPointColor: "#6d28d9" as const,
  coordinatePointColor: "#ffffff" as const,
  coordinatePointOutlineColor: "#6d28d9" as const,
};

const selectionStyles = {
  selectedPolygonColor: "#7c3aed" as const,
  selectedPolygonFillOpacity: 0.22,
  selectedPolygonOutlineColor: "#6d28d9" as const,
  selectedPolygonOutlineWidth: 4,
  selectionPointColor: "#ffffff" as const,
  selectionPointOutlineColor: "#6d28d9" as const,
  midPointColor: "#f2eaff" as const,
  midPointOutlineColor: "#7c3aed" as const,
};

type EditorCallbacks = {
  onGeometryChange: (geometry: PolygonGeometry) => void;
  onFinish: (geometry: PolygonGeometry) => void;
  onDelete: () => void;
  onHistoryChange: (state: { canUndo: boolean; canRedo: boolean }) => void;
};

function createEditor(map: MapLibreMap, guard: React.MutableRefObject<boolean>, callbacks: EditorCallbacks) {
  const draw = new TerraDraw({
    adapter: new TerraDrawMapLibreGLAdapter({ map, coordinatePrecision: 9 }),
    modes: [
      new TerraDrawRenderMode({ modeName: "navigate", styles: drawingStyles }),
      new TerraDrawPolygonMode({ styles: polygonDrawingStyles }),
      new TerraDrawSelectMode({
        flags: {
          polygon: {
            feature: {
              draggable: true,
              selfIntersectable: false,
              coordinates: { draggable: true, deletable: true, midpoints: true },
            },
          },
        },
        styles: selectionStyles,
      }),
    ],
    undoRedo: { sessionLevel: new TerraDrawSessionUndoRedo({ maxStackSize: 40 }) },
  });

  const latestPolygon = () => {
    if (guard.current) return null;
    const polygons = draw.getSnapshot().filter((item) => item.geometry.type === "Polygon");
    const current = polygons.at(-1);
    if (!current) return null;
    if (polygons.length > 1) {
      draw.removeFeatures(polygons.slice(0, -1).map((item) => item.id).filter((id) => id !== undefined));
    }
    return current.geometry as PolygonGeometry;
  };
  const updateHistory = () => callbacks.onHistoryChange({ canUndo: draw.canUndo(), canRedo: draw.canRedo() });
  const handleChange = () => {
    updateHistory();
    if (draw.getMode() !== "select") return;
    const polygon = latestPolygon();
    if (polygon) callbacks.onGeometryChange(polygon);
    else callbacks.onDelete();
  };
  const handleFinish = () => {
    const polygon = latestPolygon();
    if (polygon) callbacks.onFinish(polygon);
    updateHistory();
  };

  draw.on("change", handleChange);
  draw.on("finish", handleFinish);
  draw.on("history", updateHistory);
  draw.start();

  return {
    instance: draw,
    dispose: () => {
      draw.off("change", handleChange);
      draw.off("finish", handleFinish);
      draw.off("history", updateHistory);
      draw.stop();
    },
  };
}

function setDrawMode(draw: TerraDraw, tool: "navigate" | "draw" | "edit") {
  const desired = tool === "edit" ? "select" : tool === "draw" ? "polygon" : "navigate";
  if (draw.getMode() !== desired) draw.setMode(desired);
}

function replaceDrawGeometry(draw: TerraDraw, geometry: PolygonGeometry | null, guard: React.MutableRefObject<boolean>) {
  guard.current = true;
  draw.clear();
  if (geometry) {
    draw.addFeatures([{ type: "Feature", id: "active-aoi", geometry: geometry as Polygon, properties: { mode: "polygon" } } as GeoJSONStoreFeatures]);
  }
  draw.clearUndoRedoHistory();
  guard.current = false;
}

function decisionLabel(decision: AnalysisResponse["recommendation"]["decision"]) {
  return decision === "cortar" ? "CORTAR" : decision === "nao_cortar" ? "NAO CORTAR" : "INCONCLUSIVO";
}
