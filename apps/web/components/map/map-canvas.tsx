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
import { AUTO_ANALYSIS_CONFIG, computeTileKey, MAP_CONFIG, OPERATIONAL_RASTER_STYLE } from "@/lib/map/config";
import { runAutomaticAnalysis } from "@/lib/api/analyses";
import { fitMapToGeometry } from "@/lib/map/fit-map-to-geometry";
import { calculateGeometryPreview, type PolygonGeometry } from "@/lib/map/geometry";
import { getResultPopupPresentation } from "@/lib/map/result-popup";
import type { AnalysisResponse, AutomaticAnalysisRequest, AutomaticAnalysisResponse } from "@/lib/schemas/analyses";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";
import { useHistoryStore } from "@/stores/history-store";
import { getEffectiveRecommendation } from "@/lib/utils/recommendation";
import { DrawingControls } from "./drawing-controls";
import { AutoAnalysisPanel } from "./auto-analysis-panel";
import { installAoiHoverInteractions, installOrUpdateAoiLayer, bringAoiLayersToFront } from "./layers/aoi-layer";
import {
  boundsToPolygonCoordinates,
  installOrUpdateCanonicalAoiLayer,
  removeCanonicalAoiLayer,
} from "./layers/canonical-aoi-layer";
import { installOrUpdateManagedRoadsLayer } from "./layers/managed-roads-layer";
import { installOrUpdateZonesLayer, removeZonesLayer, bringZonesLayersToFront, installZoneClickInteraction } from "./layers/spatial-zones-layer";
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
  const disposeZoneClickRef = useRef<() => void>(() => undefined);
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

  const autoEnabled = useAutoAnalysisStore((state) => state.enabled);
  const autoStatus = useAutoAnalysisStore((state) => state.uiStatus);
  const autoResult = useAutoAnalysisStore((state) => state.result);
  const canonicalBounds = useAutoAnalysisStore((state) => state.canonicalBounds);
  const currentSpatialKey = useAutoAnalysisStore((state) => state.currentSpatialKey);
  const setAutoStatus = useAutoAnalysisStore((state) => state.setUiStatus);
  const setAutoStarted = useAutoAnalysisStore((state) => state.setAnalysisStarted);
  const setAutoResult = useAutoAnalysisStore((state) => state.setAnalysisResult);
  const setAutoFailed = useAutoAnalysisStore((state) => state.setFailed);
  const addHistoryEntry = useHistoryStore((state) => state.addEntry);

  const autoEnabledRef = useRef(autoEnabled);
  const selectedToolRef = useRef(selectedTool);
  const autoStatusRef = useRef(autoStatus);
  const autoResultRef = useRef(autoResult);
  const currentSpatialKeyRef = useRef(currentSpatialKey);

  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pollingTimerRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef<number>(0);
  const pollingIterationsRef = useRef<number>(0);

  useEffect(() => {
    autoEnabledRef.current = autoEnabled;
    selectedToolRef.current = selectedTool;
    autoStatusRef.current = autoStatus;
    autoResultRef.current = autoResult;
    currentSpatialKeyRef.current = currentSpatialKey;
  }, [autoEnabled, selectedTool, autoStatus, autoResult, currentSpatialKey]);

  const cancelAutoAnalysis = () => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    requestIdRef.current += 1;
    pollingIterationsRef.current = 0;
  };

  const schedulePolling = (payload: AutomaticAnalysisRequest, currentRequestId: number) => {
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current);
    }
    pollingTimerRef.current = setTimeout(async () => {
      if (currentRequestId !== requestIdRef.current || !autoEnabledRef.current) return;
      if (pollingIterationsRef.current >= AUTO_ANALYSIS_CONFIG.maxPollingIterations) {
        setAutoFailed("timeout");
        return;
      }
      pollingIterationsRef.current += 1;
      try {
        const response = await runAutomaticAnalysis(payload);
        if (currentRequestId !== requestIdRef.current) return;
        handleAutoResponse(response, payload, currentRequestId);
      } catch (err: unknown) {
        if (currentRequestId !== requestIdRef.current) return;
        setAutoFailed(err instanceof Error ? err.message : "polling_error");
      }
    }, AUTO_ANALYSIS_CONFIG.pollingIntervalMs);
  };

  const handleAutoResponse = (
    response: AutomaticAnalysisResponse,
    payload: AutomaticAnalysisRequest,
    currentRequestId: number,
  ) => {
    const map = mapRef.current;
    if (!map) return;

    if (response.status === "disabled") {
      setAutoStatus("disabled", response.reason);
      removeCanonicalAoiLayer(map);
      return;
    }

    if (response.status === "zoom_required") {
      setAutoStatus("zoom_required", response.reason);
      removeCanonicalAoiLayer(map);
      return;
    }

    if (response.status === "invalid_viewport") {
      setAutoStatus("invalid_viewport", response.reason);
      removeCanonicalAoiLayer(map);
      return;
    }

    if (response.status === "failed") {
      setAutoFailed(response.reason, response.canonical_bounds ?? null);
      if (response.canonical_bounds) {
        installOrUpdateCanonicalAoiLayer(map, response.canonical_bounds, "failed");
      }
      return;
    }

    if (response.status === "cache_hit" || response.status === "completed") {
      setAutoResult(response);
      removeCanonicalAoiLayer(map);
      if (response.result) {
        const tileGeometry: PolygonGeometry = response.canonical_bounds
          ? {
              type: "Polygon",
              coordinates: boundsToPolygonCoordinates(response.canonical_bounds),
            }
          : {
              type: "Polygon",
              coordinates: [],
            };
        addHistoryEntry(response.result, tileGeometry);
        useAnalysisStore.getState().applyAutomaticResult(response.result);
      }
      return;
    }

    if (response.status === "analysis_started" || response.status === "in_progress") {
      setAutoStarted(response.spatial_key ?? null, response.canonical_bounds ?? null);
      if (response.canonical_bounds) {
        installOrUpdateCanonicalAoiLayer(map, response.canonical_bounds, "analyzing");
      }
      schedulePolling(payload, currentRequestId);
    }
  };

  const executeAutoAnalysis = async () => {
    const map = mapRef.current;
    if (!map) return;
    if (!autoEnabledRef.current) return;
    if (selectedToolRef.current === "draw" || selectedToolRef.current === "edit") return;

    const zoom = map.getZoom();
    const bounds = map.getBounds();
    const center = map.getCenter();

    if (zoom < AUTO_ANALYSIS_CONFIG.minZoom) {
      cancelAutoAnalysis();
      setAutoStatus("zoom_required", "zoom_below_minimum");
      removeCanonicalAoiLayer(map);
      return;
    }

    const tileKey = computeTileKey(center.lng, center.lat, 17);
    if (
      tileKey === currentSpatialKeyRef.current &&
      (autoStatusRef.current === "cache_hit" || autoStatusRef.current === "completed") &&
      autoResultRef.current
    ) {
      return;
    }

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const currentRequestId = ++requestIdRef.current;
    pollingIterationsRef.current = 0;

    setAutoStatus("analyzing");

    const payload: AutomaticAnalysisRequest = {
      bounds: {
        west: bounds.getWest(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        north: bounds.getNorth(),
      },
      zoom,
      center: { lng: center.lng, lat: center.lat },
      force_refresh: false,
    };

    try {
      const response = await runAutomaticAnalysis(payload, controller.signal);
      if (currentRequestId !== requestIdRef.current || controller.signal.aborted) {
        return;
      }
      handleAutoResponse(response, payload, currentRequestId);
    } catch (err: unknown) {
      if (currentRequestId !== requestIdRef.current || controller.signal.aborted) {
        return;
      }
      setAutoFailed(err instanceof Error ? err.message : "network_error");
    }
  };

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
    const handleMoveStart = () => {
      if (autoEnabledRef.current) {
        if (debounceTimerRef.current) {
          clearTimeout(debounceTimerRef.current);
          debounceTimerRef.current = null;
        }
        setAutoStatus("stabilizing");
      }
    };

    const handleMoveEnd = () => {
      const center = map.getCenter();
      setMapViewport({ longitude: center.lng, latitude: center.lat, zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });

      if (!autoEnabledRef.current) return;
      if (selectedToolRef.current === "draw" || selectedToolRef.current === "edit") return;

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      debounceTimerRef.current = setTimeout(() => {
        executeAutoAnalysis();
      }, AUTO_ANALYSIS_CONFIG.debounceMs);
    };

    map.on("load", handleLoad);
    map.on("style.load", handleStyleLoad);
    map.on("error", handleError);
    map.on("movestart", handleMoveStart);
    map.on("zoomstart", handleMoveStart);
    map.on("moveend", handleMoveEnd);

    // Observa redimensionamento do container para recalibrar o canvas MapLibre sem corte ou área preta
    let resizeFrameId: number | null = null;
    let resizeObserver: ResizeObserver | null = null;

    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      resizeObserver = new ResizeObserver(() => {
        if (resizeFrameId !== null) cancelAnimationFrame(resizeFrameId);
        resizeFrameId = requestAnimationFrame(() => {
          mapRef.current?.resize();
        });
      });
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      if (resizeFrameId !== null) cancelAnimationFrame(resizeFrameId);
      resizeObserver?.disconnect();
      cancelAutoAnalysis();
      disposeHoverRef.current();
      disposeZoneClickRef.current();
      disposeEditor();
      popupRef.current?.remove();
      map.off("load", handleLoad);
      map.off("style.load", handleStyleLoad);
      map.off("error", handleError);
      map.off("movestart", handleMoveStart);
      map.off("zoomstart", handleMoveStart);
      map.off("moveend", handleMoveEnd);
      mapRef.current = null;
      map.remove();
    };
  // The map is deliberately created once; mutable refs carry current AOI state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!autoEnabled) {
      cancelAutoAnalysis();
      if (mapRef.current) {
        removeCanonicalAoiLayer(mapRef.current);
      }
    } else {
      if (mapRef.current && styleEditorReadyRef.current && selectedTool === "navigate") {
        if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = setTimeout(() => {
          executeAutoAnalysis();
        }, AUTO_ANALYSIS_CONFIG.debounceMs);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoEnabled]);

  useEffect(() => {
    if (selectedTool === "draw" || selectedTool === "edit") {
      cancelAutoAnalysis();
    }
  }, [selectedTool]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleEditorReadyRef.current) return;
    if (!autoEnabled) {
      removeCanonicalAoiLayer(map);
      return;
    }
    const decision = autoResult ? getEffectiveRecommendation(autoResult).primaryDecision : undefined;
    installOrUpdateCanonicalAoiLayer(map, canonicalBounds, autoStatus, decision);
  }, [autoEnabled, autoStatus, autoResult, canonicalBounds]);

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

  const hasActiveZones = Boolean(
    result?.spatial_segmentation?.status === "available" && (result.spatial_segmentation.zones.length > 0),
  );

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleEditorReadyRef.current) return;
    disposeHoverRef.current();
    try {
      installOrUpdateManagedRoadsLayer(map, activeRoadColors, activeRoadVisibility);
    } catch (e) {
      console.warn("Erro ao atualizar camada de rodovias.", e);
    }
    installOrUpdateAoiLayer(map, geometry, aoiVisualState, { hasActiveZones });
    disposeHoverRef.current = geometry ? installAoiHoverInteractions(map) : () => undefined;
    bringAoiLayersToFront(map);
    if (hasActiveZones) {
      bringZonesLayersToFront(map);
    }
  }, [aoiVisualState, geometry, activeRoadColors, activeRoadVisibility, hasActiveZones]);

  // Spatial segmentation zones
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleEditorReadyRef.current) return;
    const seg = result?.spatial_segmentation;
    const zones = seg?.status === "available" ? seg.zones : [];
    disposeZoneClickRef.current();
    if (zones.length > 0) {
      installOrUpdateZonesLayer(map, zones);
      bringZonesLayersToFront(map);
      const interaction = installZoneClickInteraction(map);
      disposeZoneClickRef.current = interaction.dispose;
    } else {
      removeZonesLayer(map);
      disposeZoneClickRef.current = () => undefined;
    }
  }, [result]);

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
    // Card grande só para análise manual — automática usa o badge no painel
    if (!map || !result || !geometry || isGeometryDirty) return;
    if (result.analysis_trigger === "automatic_viewport") return;
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
    <div className="map-stage" data-tour="map" tabIndex={0} onKeyDown={(event) => {
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
      <AutoAnalysisPanel />
      <MapStatus status={loadStatus} tool={selectedTool} onRetry={retryBaseMap} />
      {geometry && result?.analysis_trigger !== "automatic_viewport" ? <div className="map-aoi-floating-label" data-state={aoiVisualState.id}>Área selecionada · {aoiVisualState.label}</div> : null}
      <MapStyleSelector active="operational" />
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
