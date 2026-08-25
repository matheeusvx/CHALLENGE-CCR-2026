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
import { MAP_CONFIG, OPERATIONAL_RASTER_STYLE } from "@/lib/map/config";
import { fitMapToGeometry } from "@/lib/map/fit-map-to-geometry";
import { calculateGeometryPreview, type PolygonGeometry } from "@/lib/map/geometry";
import { getResultPopupPresentation } from "@/lib/map/result-popup";
import type { AnalysisResponse } from "@/lib/schemas/analyses";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { DrawingControls } from "./drawing-controls";
import { installAoiHoverInteractions, installOrUpdateAoiLayer, bringAoiLayersToFront } from "./layers/aoi-layer";
import { installOrUpdateManagedRoadsLayer } from "./layers/managed-roads-layer";
import { MapLegend } from "./map-legend";

import { MapStatus, type MapLoadStatus } from "./map-status";
import { MapStyleSelector } from "./map-style-selector";
import { useRoadColorStore } from "@/stores/road-color-store";

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
  const baseLoadFailedRef = useRef(false);
  const styleEditorReadyRef = useRef(false);
  const geometry = useAnalysisStore((state) => state.geometry);
  const geometryRevision = useAnalysisStore((state) => state.geometryRevision);
  const geometryValidation = useAnalysisStore((state) => state.geometryValidation);
  const isGeometryDirty = useAnalysisStore((state) => state.isGeometryDirty);
  const selectedTool = useAnalysisStore((state) => state.selectedTool);
  const activeTab = useAnalysisStore((state) => state.activeTab);
  const activeRoadColors = useRoadColorStore((state) => state.activeColors);
  const activeRoadVisibility = useRoadColorStore((state) => state.activeVisibility);
  const fitRequestId = useAnalysisStore((state) => state.fitRequestId);
  const setGeometry = useAnalysisStore((state) => state.setGeometry);
  const clearGeometry = useAnalysisStore((state) => state.clearGeometry);
  const setSelectedTool = useAnalysisStore((state) => state.setSelectedTool);
  const setMapViewport = useAnalysisStore((state) => state.setMapViewport);
  const [loadStatus, setLoadStatus] = useState<MapLoadStatus>("loading");
  const [historyState, setHistoryState] = useState({ canUndo: false, canRedo: false });

  const validated = isCurrentGeometryValidated(useAnalysisStore.getState());
  const aoiVisualState = useMemo(() => getAoiVisualState({
    editing: Boolean(geometry && (selectedTool === "draw" || selectedTool === "edit")),
    dirty: isGeometryDirty,
    validation: validationFailed ? "invalid" : validated ? "valid" : null,
    recommendation: geometry && !isGeometryDirty ? result?.recommendation.decision : undefined,
  }), [geometry, isGeometryDirty, result?.recommendation.decision, selectedTool, validated, validationFailed]);

  const geometryRef = useRef(geometry);
  const visualStateRef = useRef(aoiVisualState);
  const activeRoadColorsRef = useRef(activeRoadColors);
  const activeRoadVisibilityRef = useRef(activeRoadVisibility);

  useEffect(() => {
    geometryRef.current = geometry;
    visualStateRef.current = aoiVisualState;
    activeRoadColorsRef.current = activeRoadColors;
    activeRoadVisibilityRef.current = activeRoadVisibility;
  }, [aoiVisualState, geometry, activeRoadColors, activeRoadVisibility]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    maplibregl.setWorkerUrl(MAP_CONFIG.workerUrl);
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OPERATIONAL_RASTER_STYLE,
      center: [-46.955, -23.121],
      zoom: 12,
      bearing: 0,
      pitch: 0,
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
      try {
        installOrUpdateManagedRoadsLayer(map, activeRoadColorsRef.current, activeRoadVisibilityRef.current);
      } catch (e) {
        console.warn("Erro ao instalar camada de rodovias, ignorando.", e);
      }
      installOrUpdateAoiLayer(map, geometryRef.current, visualStateRef.current);
      disposeHoverRef.current = geometryRef.current ? installAoiHoverInteractions(map) : () => undefined;
    };

    const handleStyleLoad = () => {
      try {
        if (!styleEditorReadyRef.current) {
          initializeEditor();
          styleEditorReadyRef.current = true;
        }
        installOperationalLayers();
        if (!baseLoadFailedRef.current) setLoadStatus("ready");
      } catch (reason) {
        console.error("Falha ao instalar as camadas operacionais do mapa.", reason);
        baseLoadFailedRef.current = true;
        setLoadStatus("error");
      }
    };
    const handleLoad = () => {
      try {
        if (!styleEditorReadyRef.current) {
          initializeEditor();
          styleEditorReadyRef.current = true;
        }
        installOperationalLayers();
        if (!baseLoadFailedRef.current) setLoadStatus("ready");
      } catch (reason) {
        console.error("Falha ao concluir o carregamento do mapa.", reason);
        baseLoadFailedRef.current = true;
        setLoadStatus("error");
      }
    };
    const handleError = (event: maplibregl.ErrorEvent) => {
      if ((event as unknown as { sourceId?: string }).sourceId === "motiva-managed-roads") {
        console.warn("Falha ao carregar a camada de rodovias, ignorando erro.");
        return;
      }
      console.error("Erro do MapLibre ao carregar style, source ou tile.", event.error ?? event);
      baseLoadFailedRef.current = true;
      setLoadStatus("error");
    };
    const handleMoveEnd = () => {
      const center = map.getCenter();
      setMapViewport({ longitude: center.lng, latitude: center.lat, zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
    };

    map.on("load", handleLoad);
    map.on("style.load", handleStyleLoad);
    map.on("error", handleError);
    map.on("moveend", handleMoveEnd);

    return () => {
      disposeHoverRef.current();
      disposeEditor();
      popupRef.current?.remove();
      map.off("load", handleLoad);
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
    if (!map || !styleEditorReadyRef.current) return;
    disposeHoverRef.current();
    try {
      installOrUpdateManagedRoadsLayer(map, activeRoadColors, activeRoadVisibility);
    } catch (e) {
      console.warn("Erro ao atualizar camada de rodovias.", e);
    }
    installOrUpdateAoiLayer(map, geometry, aoiVisualState);
    disposeHoverRef.current = geometry ? installAoiHoverInteractions(map) : () => undefined;
    bringAoiLayersToFront(map);
  }, [aoiVisualState, geometry, activeRoadColors, activeRoadVisibility]);

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
    if (!map || !result || !geometry || isGeometryDirty) return;
    const presentation = getResultPopupPresentation(result);
    const popupNode = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = presentation.decision;
    const confidence = document.createElement("span");
    confidence.textContent = `Confiança: ${presentation.confidence}`;
    const period = document.createElement("span");
    period.textContent = `Período: ${presentation.period}`;
    popupNode.className = `map-result-popup ${presentation.state}`;
    popupNode.dataset.decision = presentation.state;
    popupNode.append(title, confidence, period);
    const centroid = geometryValidation?.centroid ?? calculateGeometryPreview(geometry).centroid;
    popupRef.current = new maplibregl.Popup({ closeButton: false, offset: 14, className: "analysis-result-map-popup" })
      .setLngLat([centroid.longitude, centroid.latitude])
      .setDOMContent(popupNode)
      .addTo(map);
  }, [geometry, geometryValidation, isGeometryDirty, result, validated]);

  const retryBaseMap = () => {
    const map = mapRef.current;
    if (!map) return;
    disposeHoverRef.current();
    disposeEditorRef.current();
    disposeEditorRef.current = () => undefined;
    drawRef.current = null;
    styleEditorReadyRef.current = false;
    baseLoadFailedRef.current = false;
    setLoadStatus("loading");
    map.setStyle(OPERATIONAL_RASTER_STYLE);
  };

  const deleteGeometry = () => {
    const state = useAnalysisStore.getState();
    if (isCurrentGeometryValidated(state) && !window.confirm("Esta área já foi validada. Deseja excluí-la?")) return;
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
      <div ref={containerRef} className="map-canvas" aria-label="Mapa para delimitação da área rodoviária" />
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
      <MapStatus status={loadStatus} tool={selectedTool} onRetry={retryBaseMap} />
      {geometry ? <div className="map-aoi-floating-label" data-state={aoiVisualState.id}>Área selecionada · {aoiVisualState.label}</div> : null}
      <MapStyleSelector active="operational" />
      {loadStatus === "ready" && (
        <>
          <MapLegend mapStyle="operational" aoiState={aoiVisualState} hasGeometry={Boolean(geometry)} />

        </>
      )}
    </div>
  );
}

const drawingStyles = {
  polygonFillColor: "#7c3aed" as const,
  polygonFillOpacity: 0.32,
  polygonOutlineColor: "#7c3aed" as const,
  polygonOutlineWidth: 6,
};

const polygonDrawingStyles = {
  fillColor: "#7c3aed" as const,
  fillOpacity: 0.32,
  outlineColor: "#7c3aed" as const,
  outlineOpacity: 1,
  outlineWidth: 6,
  closingPointColor: "#ffffff" as const,
  closingPointWidth: 11,
  closingPointOpacity: 1,
  closingPointOutlineColor: "#7c3aed" as const,
  closingPointOutlineWidth: 3,
  closingPointOutlineOpacity: 1,
  coordinatePointColor: "#ffffff" as const,
  coordinatePointWidth: 10,
  coordinatePointOpacity: 1,
  coordinatePointOutlineColor: "#7c3aed" as const,
  coordinatePointOutlineWidth: 3,
  coordinatePointOutlineOpacity: 1,
};

const selectionStyles = {
  selectedPolygonColor: "#7c3aed" as const,
  selectedPolygonFillOpacity: 0.32,
  selectedPolygonOutlineColor: "#7c3aed" as const,
  selectedPolygonOutlineOpacity: 1,
  selectedPolygonOutlineWidth: 6,
  selectionPointColor: "#ffffff" as const,
  selectionPointWidth: 11,
  selectionPointOpacity: 1,
  selectionPointOutlineColor: "#7c3aed" as const,
  selectionPointOutlineWidth: 3,
  selectionPointOutlineOpacity: 1,
  midPointColor: "#f2eaff" as const,
  midPointOutlineColor: "#7c3aed" as const,
  midPointWidth: 8,
  midPointOpacity: 1,
  midPointOutlineWidth: 2,
  midPointOutlineOpacity: 1,
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
      new TerraDrawPolygonMode({ styles: polygonDrawingStyles, showCoordinatePoints: true }),
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
