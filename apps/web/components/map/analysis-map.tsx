"use client";

import dynamic from "next/dynamic";
import type { AnalysisResponse } from "@/lib/schemas/analyses";

const MapCanvas = dynamic(() => import("./map-canvas").then((module) => module.MapCanvas), {
  ssr: false,
  loading: () => <div className="map-loading" role="status"><span className="map-loader" />Preparando workspace geoespacial</div>,
});

export function AnalysisMap({ result, validationFailed = false }: { result?: AnalysisResponse; validationFailed?: boolean }) {
  return <MapCanvas result={result} validationFailed={validationFailed} />;
}
