"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2 } from "lucide-react";
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
import type { PolygonGeometry } from "@/lib/map/geometry";
import type { AnalysisResponse, AutomaticAnalysisRequest, AutomaticAnalysisResponse } from "@/lib/schemas/analyses";
import { isCurrentGeometryValidated, useAnalysisStore } from "@/stores/analysis-store";
import { useAutoAnalysisStore } from "@/stores/auto-analysis-store";
import { useHistoryStore } from "@/stores/history-store";
import { decisionLabels, getEffectiveRecommendation } from "@/lib/utils/recommendation";
import { DrawingControls } from "./drawing-controls";
import { AutoAnalysisPanel } from "./auto-analysis-panel";
import { installAoiHoverInteractions, installOrUpdateAoiLayer, bringAoiLayersToFront } from "./layers/aoi-layer";
import {
  boundsToPolygonCoordinates,
  installOrUpdateCanonicalAoiLayer,
  removeCanonicalAoiLayer,
} from "./layers/canonical-aoi-layer";
import {
  fitMapToRoadsideGeometry,
  installOrUpdateRoadsideLayer,
  removeRoadsideLayer,
} from "./layers/roadside-layer";
import { installOrUpdateManagedRoadsLayer } from "./layers/managed-roads-layer";
import { installOrUpdateZonesLayer, removeZonesLayer, bringZonesLayersToFront, installZoneClickInteraction } from "./layers/spatial-zones-layer";
import { MapStatus, type MapLoadStatus } from "./map-status";
import { MapStyleSelector } from "./map-style-selector";
import { useRoadColorStore } from "@/stores/road-color-store";


type Props = { result?: AnalysisResponse; validationFailed?: boolean };

function getSpatialStrategyName(response: AutomaticAnalysisResponse): string {
  if (typeof response.spatial_strategy === "string") return response.spatial_strategy;
  if (response.spatial_strategy && typeof response.spatial_strategy === "object") {
    return String((response.spatial_strategy as Record<string, unknown>).name ?? "");
  }
  return "";
}

function isRoadsideResponse(response: AutomaticAnalysisResponse): boolean {
  return (
    getSpatialStrategyName(response).startsWith("roadside") ||
    Boolean(response.analyzed_geometry) ||
    Boolean(response.side_a_geometry) ||
    Boolean(response.side_b_geometry)
  );
}

function getRoadsideIdentity(response: AutomaticAnalysisResponse): string | null {
  if (response.spatial_key) return response.spatial_key;
  const road = response.road as Record<string, unknown> | null | undefined;
  const axisId = road?.axis_id ?? road?.id;
  const sectionId = road?.section_id;
  return axisId && sectionId ? `roadside:${String(axisId)}:${String(sectionId)}` : null;
}

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
  const analyzedGeometry = useAutoAnalysisStore((state) => state.analyzedGeometry);
  const centerline = useAutoAnalysisStore((state) => state.centerline);
  const spatialStrategy = useAutoAnalysisStore((state) => state.spatialStrategy);
  const hasRoadsideOverlay = Boolean(
    spatialStrategy?.startsWith("roadside") || analyzedGeometry,
  );
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
  const lastFittedRoadsideIdentityRef = useRef<string | null>(null);
  const roadsideCameraFitInProgressRef = useRef(false);
  const roadsideCameraFitResetTimerRef = useRef<NodeJS.Timeout | null>(null);

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
    if (roadsideCameraFitResetTimerRef.current) {
      clearTimeout(roadsideCameraFitResetTimerRef.current);
      roadsideCameraFitResetTimerRef.current = null;
    }
    roadsideCameraFitInProgressRef.current = false;
    requestIdRef.current += 1;
  };

  const schedulePolling = (payload: AutomaticAnalysisRequest, currentRequestId: number) => {
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current);
    }
    pollingTimerRef.current = setTimeout(async () => {
      if (currentRequestId !== requestIdRef.current || !autoEnabledRef.current) return;
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
    if (!map || currentRequestId !== requestIdRef.current) return;
    const isRoadside = isRoadsideResponse(response);

    const fitRoadsideOnce = (roadsideGeometry: unknown) => {
      const identity = getRoadsideIdentity(response);
      if (!identity || !roadsideGeometry || identity === lastFittedRoadsideIdentityRef.current) return;

      lastFittedRoadsideIdentityRef.current = identity;
      roadsideCameraFitInProgressRef.current = true;
      const fitted = fitMapToRoadsideGeometry(map, roadsideGeometry);
      if (!fitted) {
        roadsideCameraFitInProgressRef.current = false;
        lastFittedRoadsideIdentityRef.current = null;
        return;
      }
      if (roadsideCameraFitResetTimerRef.current) {
        clearTimeout(roadsideCameraFitResetTimerRef.current);
      }
      roadsideCameraFitResetTimerRef.current = setTimeout(() => {
        roadsideCameraFitInProgressRef.current = false;
        roadsideCameraFitResetTimerRef.current = null;
      }, 1_200);
    };

    if (response.status === "disabled") {
      setAutoStatus("disabled", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "zoom_required") {
      setAutoStatus("zoom_required", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "invalid_viewport") {
      setAutoStatus("invalid_viewport", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "road_context_required") {
      setAutoStatus("road_context_required", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "road_not_found") {
      setAutoStatus("road_not_found", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (
      response.status === "road_geometry_unavailable" ||
      response.status === "road_geometry_unreliable" ||
      response.status === "road_ambiguous" ||
      response.status === "roadside_too_small" ||
      response.status === "roadside_geometry_invalid"
    ) {
      setAutoStatus("road_ambiguous", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "skipped") {
      setAutoStatus("idle", response.reason);
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }

    if (response.status === "failed") {
      removeRoadsideLayer(map);
      setAutoFailed(response.reason, response.canonical_bounds ?? null);
      if (!isRoadside && response.canonical_bounds) {
        installOrUpdateCanonicalAoiLayer(map, response.canonical_bounds, "failed");
      } else {
        removeCanonicalAoiLayer(map);
      }
      return;
    }

    if (
      response.status === "analysis_started" ||
      response.status === "in_progress" ||
      response.status === "road_section_resolved"
    ) {
      const roadsideDetails = isRoadside
        ? {
            road: response.road ?? null,
            analyzedGeometry: response.analyzed_geometry ?? null,
            centerline: response.centerline ?? null,
            sideAGeometry: response.side_a_geometry ?? null,
            sideBGeometry: response.side_b_geometry ?? null,
            spatialStrategy: getSpatialStrategyName(response) || "roadside_v1",
          }
        : undefined;

      setAutoStarted(response.spatial_key ?? null, response.canonical_bounds ?? null, roadsideDetails);
      if (isRoadside) {
        removeCanonicalAoiLayer(map);
        installOrUpdateRoadsideLayer(
          map,
          {
            analyzedGeometry: response.analyzed_geometry,
            centerline: response.centerline,
            sideAGeometry: response.side_a_geometry,
            sideBGeometry: response.side_b_geometry,
          },
          "analyzing",
        );
        fitRoadsideOnce(response.analyzed_geometry);
      } else {
        removeRoadsideLayer(map);
        if (response.canonical_bounds) {
          installOrUpdateCanonicalAoiLayer(map, response.canonical_bounds, "analyzing");
        }
      }
      schedulePolling(payload, currentRequestId);
      return;
    }

    if (response.status === "cache_hit" || response.status === "completed") {
      if (!response.result || response.result.status !== "completed") {
        removeCanonicalAoiLayer(map);
        removeRoadsideLayer(map);
        setAutoFailed("invalid_terminal_response");
        return;
      }
      setAutoResult(response);
      const roadsideState = useAutoAnalysisStore.getState();
      const roadsideGeometry = response.analyzed_geometry ?? roadsideState.analyzedGeometry;
      const roadsideCenterline = response.centerline ?? roadsideState.centerline;
      const decision = getEffectiveRecommendation(response.result).primaryDecision;

      if (isRoadside || roadsideState.spatialStrategy?.startsWith("roadside")) {
        removeCanonicalAoiLayer(map);
        installOrUpdateRoadsideLayer(
          map,
          { analyzedGeometry: roadsideGeometry, centerline: roadsideCenterline },
          response.status,
          decision,
        );
        fitRoadsideOnce(roadsideGeometry);
      } else {
        removeRoadsideLayer(map);
        removeCanonicalAoiLayer(map);
      }

      const resultWithRoad = response.road
        ? { ...response.result, road: response.road }
        : response.result;
      if (!isRoadside && response.canonical_bounds) {
        const tileGeometry: PolygonGeometry = {
          type: "Polygon",
          coordinates: boundsToPolygonCoordinates(response.canonical_bounds),
        };
        addHistoryEntry(resultWithRoad, tileGeometry, undefined, response.road);
      }
      useAnalysisStore.getState().applyAutomaticResult(resultWithRoad);
      return;
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
      removeRoadsideLayer(map);
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
      const automaticState = useAutoAnalysisStore.getState();
      if (
        automaticState.enabled &&
        automaticState.spatialStrategy?.startsWith("roadside") &&
        automaticState.analyzedGeometry
      ) {
        removeCanonicalAoiLayer(map);
        installOrUpdateRoadsideLayer(
          map,
          {
            analyzedGeometry: automaticState.analyzedGeometry,
            centerline: automaticState.centerline,
          },
          automaticState.uiStatus === "analyzing" ? "analyzing" : "completed",
          automaticState.result
            ? getEffectiveRecommendation(automaticState.result).primaryDecision
            : undefined,
        );
      }
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
      if (roadsideCameraFitInProgressRef.current) return;
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

      if (roadsideCameraFitInProgressRef.current) {
        roadsideCameraFitInProgressRef.current = false;
        if (roadsideCameraFitResetTimerRef.current) {
          clearTimeout(roadsideCameraFitResetTimerRef.current);
          roadsideCameraFitResetTimerRef.current = null;
        }
        return;
      }

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
        removeRoadsideLayer(mapRef.current);
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
      if (mapRef.current) {
        removeCanonicalAoiLayer(mapRef.current);
        removeRoadsideLayer(mapRef.current);
      }
    }
  }, [selectedTool]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleEditorReadyRef.current) return;
    if (!autoEnabled) {
      removeCanonicalAoiLayer(map);
      removeRoadsideLayer(map);
      return;
    }
    const decision = autoResult ? getEffectiveRecommendation(autoResult).primaryDecision : undefined;
    if (hasRoadsideOverlay) {
      removeCanonicalAoiLayer(map);
      if (analyzedGeometry) {
        installOrUpdateRoadsideLayer(
          map,
          { analyzedGeometry, centerline },
          autoStatus === "analyzing" ? "analyzing" : "completed",
          decision,
        );
      } else {
        removeRoadsideLayer(map);
      }
    } else {
      removeRoadsideLayer(map);
      installOrUpdateCanonicalAoiLayer(map, canonicalBounds, autoStatus, decision);
    }
  }, [autoEnabled, autoStatus, autoResult, canonicalBounds, analyzedGeometry, centerline, hasRoadsideOverlay]);

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
    if (popupRef.current) {
      popupRef.current.remove();
      popupRef.current = null;
    }
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

  const isManualResult = Boolean(
    result &&
    result.analysis_trigger !== "automatic_viewport" &&
    geometry
  );
  const manualEffective = isManualResult && result ? getEffectiveRecommendation(result) : null;
  const manualDecisionKey = manualEffective?.primaryDecision ?? null;
  const manualDecisionLabel = manualDecisionKey ? decisionLabels[manualDecisionKey] : null;
  const manualClass = manualDecisionKey === "nao_cortar"
    ? "manual-result-pill--no-cut"
    : manualDecisionKey === "cortar"
      ? "manual-result-pill--cut"
      : "manual-result-pill--inconclusive";

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
      <div className="map-top-right-controls">
        <AutoAnalysisPanel />
        {isManualResult && manualDecisionKey && manualDecisionLabel ? (
          <div
            className={`manual-result-pill ${manualClass}`}
            data-testid="manual-result-pill"
            data-decision={manualDecisionKey}
            role="status"
            aria-label={`Resultado da análise manual: ${manualDecisionLabel}`}
          >
            <span className="manual-result-dot" aria-hidden="true" />
            <span className="manual-result-prefix">Manual</span>
            <span className="manual-result-sep" aria-hidden="true"> · </span>
            <span className="manual-result-decision">{manualDecisionLabel}</span>
          </div>
        ) : geometry && validated && !isGeometryDirty && !result ? (
          <div className="manual-validated-pill" role="status">
            <CheckCircle2 size={12} className="validated-pill-icon" aria-hidden="true" />
            <span>Área validada</span>
          </div>
        ) : null}
      </div>
      <MapStatus status={loadStatus} tool={selectedTool} onRetry={retryBaseMap} />
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
