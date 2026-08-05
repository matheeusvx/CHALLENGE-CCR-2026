"""Validacao geoespacial, execucao e acesso controlado a artefatos."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.geometry import (
    calculate_geometry_metadata,
    extract_polygon_geometry,
)
from src.satellite_monitoring.service import InvalidAnalysisGeometryError

from ..config import settings
from ..dependencies import (
    AnalysisService,
    analysis_registry,
    get_analysis_service,
)
from ..exceptions import ApiError
from ..schemas import (
    AnalysisResponse,
    AnalysisRunRequest,
    GeometryRequest,
    GeometryValidationResponse,
)

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

ALLOWED_ARTIFACTS = {
    "summary": "application/json",
    "timeseries_csv": "text/csv",
    "scenes_csv": "text/csv",
    "recommendation_json": "application/json",
    "recommendation_csv": "text/csv",
    "chart": "image/png",
    "aoi": "application/geo+json",
}


def _validated_geometry_metadata(geometry_document: dict[str, Any]) -> dict[str, Any]:
    try:
        geometry, feature_count = extract_polygon_geometry(geometry_document)
        return calculate_geometry_metadata(
            geometry,
            source="geojson_inline",
            feature_count=feature_count,
        )
    except (TypeError, ValueError) as exc:
        raise ApiError(
            "INVALID_GEOMETRY",
            "A geometria enviada nao e valida.",
            status_code=422,
            details=[{"message": str(exc)}],
        ) from exc


@router.post("/validate-geometry", response_model=GeometryValidationResponse)
def validate_geometry(payload: GeometryRequest) -> GeometryValidationResponse:
    metadata = _validated_geometry_metadata(payload.geometry)
    area = float(metadata["area_square_meters"])
    warnings: list[str] = []
    if area < 400:
        warnings.append(
            "A area e pequena para a resolucao espacial de 10 metros do Sentinel-2."
        )
    return GeometryValidationResponse(
        valid=True,
        geometry_type=metadata["geometry_type"],
        area_square_meters=area,
        centroid=metadata["centroid"],
        bounding_box=metadata["bounding_box"],
        estimated_sentinel_pixels=max(1, math.ceil(area / 100.0)),
        warnings=warnings,
    )


def _to_api_response(result: Any) -> AnalysisResponse:
    recommendation = result.recommendation
    public_summary = dict(result.summary)
    if isinstance(public_summary.get("parameters"), dict):
        public_summary["parameters"] = {
            key: value
            for key, value in public_summary["parameters"].items()
            if key not in {"output_root", "geometry_file"}
        }
    artifact_urls = {
        name: f"/api/analyses/{result.analysis_id}/artifacts/{name}"
        for name in result.artifacts
        if name in ALLOWED_ARTIFACTS
    }
    return AnalysisResponse(
        analysis_id=result.analysis_id,
        status=result.status,
        recommendation={
            "decision": recommendation.get("recommendation", "inconclusivo"),
            "confidence": recommendation.get("confidence", "low"),
            "experimental": bool(recommendation.get("experimental", True)),
            "summary": recommendation.get("summary", ""),
            "reasons": recommendation.get("reasons") or [],
            "blocking_reasons": recommendation.get("blocking_reasons") or [],
            "limitations": recommendation.get("limitations") or [],
            "metrics": recommendation.get("metrics") or {},
        },
        aoi=result.aoi,
        summary=public_summary,
        timeseries=result.timeseries,
        scenes=result.scenes,
        artifacts=artifact_urls,
        warnings=result.warnings,
        errors=result.errors,
    )


@router.post("/run", response_model=AnalysisResponse)
def run_analysis(
    payload: AnalysisRunRequest,
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisResponse:
    if payload.start_date > payload.end_date:
        raise ApiError(
            "INVALID_DATE_RANGE",
            "A data inicial nao pode ser posterior a data final.",
            status_code=422,
        )
    _validated_geometry_metadata(payload.geometry)
    decision = payload.decision
    try:
        config = MonitoringConfig(
            geometry=payload.geometry,
            start_date=payload.start_date,
            end_date=payload.end_date,
            max_cloud_cover=payload.max_cloud_cover,
            max_scenes=payload.max_scenes,
            scene_order=payload.scene_order,
            min_valid_pixel_percentage=payload.min_valid_pixel_percentage,
            min_observations=payload.min_observations,
            daily_aggregation=payload.daily_aggregation,
            decision_min_observations=decision.decision_min_observations,
            high_vegetation_percentile=decision.high_vegetation_percentile,
            significant_drop_absolute=decision.significant_drop_absolute,
            significant_drop_relative_percentage=decision.significant_drop_relative_percentage,
            trend_window=decision.trend_window,
            max_gap_days=decision.max_gap_days,
            recent_intervention_days=decision.recent_intervention_days,
            output_root=settings.output_root,
        )
        result = service(config, analysis_id=str(uuid4()))
    except InvalidAnalysisGeometryError as exc:
        raise ApiError(
            "INVALID_GEOMETRY",
            "A geometria enviada nao e valida.",
            status_code=422,
            details=[{"message": str(exc)}],
        ) from exc
    except ValueError as exc:
        raise ApiError(
            "INVALID_PARAMETERS",
            "Os parametros enviados nao sao validos.",
            status_code=422,
            details=[{"message": str(exc)}],
        ) from exc
    except Exception as exc:
        raise ApiError(
            "PROCESSING_ERROR",
            "O pipeline nao conseguiu concluir a analise.",
            status_code=500,
        ) from exc

    if result.status == "failed":
        code = result.errors[0].get("code", "PROCESSING_ERROR") if result.errors else "PROCESSING_ERROR"
        status_code = 502 if code == "SATELLITE_PROVIDER_ERROR" else 500
        raise ApiError(code, "O pipeline nao conseguiu concluir a analise.", status_code=status_code)
    analysis_registry.add(result)
    return _to_api_response(result)


@router.get("/{analysis_id}/artifacts/{artifact_name}", response_class=FileResponse)
def get_artifact(analysis_id: UUID, artifact_name: str) -> FileResponse:
    if artifact_name not in ALLOWED_ARTIFACTS:
        raise ApiError("ARTIFACT_NOT_FOUND", "Artefato nao encontrado.", status_code=404)
    result = analysis_registry.get(str(analysis_id))
    if result is None or artifact_name not in result.artifacts:
        raise ApiError("ARTIFACT_NOT_FOUND", "Artefato nao encontrado.", status_code=404)

    path = Path(result.artifacts[artifact_name]).resolve()
    if result.run_directory is None:
        raise ApiError("ARTIFACT_NOT_FOUND", "Artefato nao encontrado.", status_code=404)
    run_directory = result.run_directory.resolve()
    if path.parent != run_directory or not path.is_file():
        raise ApiError("ARTIFACT_NOT_FOUND", "Artefato nao encontrado.", status_code=404)
    return FileResponse(path, media_type=ALLOWED_ARTIFACTS[artifact_name], filename=path.name)
