"""Validacao geoespacial, execucao e acesso controlado a artefatos."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from src.satellite_monitoring.config import MonitoringConfig
from src.satellite_monitoring.decision_support import build_decision_support
from src.satellite_monitoring.database import (
    AnalysisIdentity,
    count_analyses,
    get_analysis,
    list_analyses,
    nearest_km,
    save_analysis,
    session_scope,
)
from src.satellite_monitoring.database.identity import road_section_identity
from src.satellite_monitoring.geometry import (
    calculate_geometry_metadata,
    extract_polygon_geometry,
)
from src.satellite_monitoring.service import InvalidAnalysisGeometryError

from ..config import settings
from ..dependencies import (
    AnalysisService,
    analysis_registry,
    get_automatic_analysis_coordinator,
    get_analysis_now,
    get_analysis_service,
)
from ..automatic_analysis import AutomaticAnalysisCoordinator, AutomaticPipelineFailure
from ..exceptions import ApiError
from ..operational_profile import (
    DEFAULT_OPERATIONAL_ANALYSIS_PROFILE,
    AnalysisPeriod,
    resolve_analysis_period,
)
from ..schemas import (
    AnalysisHistoryDetail,
    AnalysisHistoryItem,
    AnalysisHistoryPage,
    AnalysisResponse,
    AnalysisRunRequest,
    AutomaticAnalysisRequest,
    AutomaticAnalysisResponse,
    Centroid,
    DecisionSupportResponse,
    GeometryRequest,
    GeometryValidationResponse,
)

router = APIRouter(prefix="/api/analyses", tags=["analyses"])
logger = logging.getLogger(__name__)
T = TypeVar("T")

ALLOWED_ARTIFACTS = {
    "summary": "application/json",
    "timeseries_csv": "text/csv",
    "raw_timeseries_csv": "text/csv",
    "scenes_csv": "text/csv",
    "quality_report": "application/json",
    "recommendation_json": "application/json",
    "recommendation_csv": "text/csv",
    "chart": "image/png",
    "aoi": "application/geo+json",
    "multisource_evidence": "application/json",
}


def _or_profile(value: T | None, profile_value: T) -> T:
    return profile_value if value is None else value


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


def _to_api_response(
    result: Any,
    analysis_period: AnalysisPeriod,
    *,
    analysis_trigger: str = "manual",
) -> AnalysisResponse:
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
        analysis_period=analysis_period.to_dict(),
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
        height_estimation=result.height_estimation,
        selected_area_m2=getattr(result, "selected_area_m2", None),
        effective_analysis_area_m2=getattr(
            result, "effective_analysis_area_m2", None
        ),
        effective_analysis_pct=getattr(result, "effective_analysis_pct", None),
        spatial_segmentation=getattr(result, "spatial_segmentation", None),
        multisource=getattr(result, "multisource", None),
        aoi=result.aoi,
        summary=public_summary,
        timeseries=result.timeseries,
        scenes=result.scenes,
        artifacts=artifact_urls,
        warnings=result.warnings,
        errors=result.errors,
        analysis_trigger=analysis_trigger,
    )


def _build_monitoring_config(
    payload: AnalysisRunRequest,
    analysis_period: AnalysisPeriod,
) -> MonitoringConfig:
    """Build the shared scientific configuration for manual and automatic runs."""
    profile = DEFAULT_OPERATIONAL_ANALYSIS_PROFILE
    decision = payload.decision
    return MonitoringConfig(
        geometry=payload.geometry,
        start_date=analysis_period.start_date,
        end_date=analysis_period.end_date,
        max_cloud_cover=_or_profile(payload.max_cloud_cover, profile.max_cloud_cover),
        max_scenes=_or_profile(payload.max_scenes, profile.max_scenes),
        max_candidate_scenes=profile.max_candidate_scenes,
        scene_order=_or_profile(payload.scene_order, profile.scene_order),
        min_valid_pixel_percentage=_or_profile(
            payload.min_valid_pixel_percentage,
            profile.min_valid_pixel_percentage,
        ),
        min_valid_pixel_count=profile.min_valid_pixel_count,
        min_aoi_coverage_percentage=profile.min_aoi_coverage_percentage,
        min_observations=_or_profile(payload.min_observations, profile.min_observations),
        daily_aggregation=_or_profile(payload.daily_aggregation, profile.daily_aggregation),
        decision_min_observations=_or_profile(
            decision.decision_min_observations if decision else None,
            profile.decision_min_observations,
        ),
        high_vegetation_percentile=_or_profile(
            decision.high_vegetation_percentile if decision else None,
            profile.high_vegetation_percentile,
        ),
        significant_drop_absolute=_or_profile(
            decision.significant_drop_absolute if decision else None,
            profile.significant_drop_absolute,
        ),
        significant_drop_relative_percentage=_or_profile(
            decision.significant_drop_relative_percentage if decision else None,
            profile.significant_drop_relative_percentage,
        ),
        trend_window=_or_profile(
            decision.trend_window if decision else None,
            profile.trend_window,
        ),
        max_gap_days=_or_profile(
            decision.max_gap_days if decision else None,
            profile.max_gap_days,
        ),
        recent_intervention_days=_or_profile(
            decision.recent_intervention_days if decision else None,
            profile.recent_intervention_days,
        ),
        output_root=settings.output_root,
        height_estimation_enabled=settings.height_estimation_enabled,
        spatial_segmentation_enabled=settings.spatial_segmentation_enabled,
        spatial_section_length_m=settings.spatial_section_length_m,
        spatial_regularization_enabled=settings.spatial_regularization_enabled,
        multisource_enabled=settings.multisource_enabled,
        sentinel1_enabled=settings.sentinel1_enabled,
        sentinel1_collection=settings.sentinel1_collection,
        sentinel1_max_scenes=settings.sentinel1_max_scenes,
        multisource_fusion_mode=settings.multisource_fusion_mode,
        validation_holdout_benchmark_path=settings.validation_holdout_benchmark_path,
    )


@router.post("/run", response_model=AnalysisResponse)
def run_analysis(
    payload: AnalysisRunRequest,
    service: AnalysisService = Depends(get_analysis_service),
    now: datetime = Depends(get_analysis_now),
) -> AnalysisResponse:
    try:
        analysis_period = resolve_analysis_period(
            payload.start_date,
            payload.end_date,
            now=now,
            timezone_name=settings.analysis_timezone,
        )
    except ValueError as exc:
        raise ApiError(
            "INVALID_DATE_RANGE",
            str(exc),
            status_code=422,
        ) from exc
    _validated_geometry_metadata(payload.geometry)
    try:
        config = _build_monitoring_config(payload, analysis_period)
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
    response = _to_api_response(result, analysis_period)
    # O apoio precisa ser anexado ANTES de gravar, para que o payload guardado
    # contenha a mesma resposta que o operador viu.
    support = _build_support(response, analysis_period)
    if support is not None:
        response.decision_support = DecisionSupportResponse.model_validate(support)
    _persist_analysis(result, response, payload.geometry)
    return response


def _build_support(
    response: AnalysisResponse, analysis_period: AnalysisPeriod
) -> dict[str, Any] | None:
    """Monta a evidencia de apoio para a area analisada.

    Tolerante a falhas: um problema de banco ou de modelo nunca pode invalidar
    uma decisao que o motor de satelite ja produziu.
    """

    try:
        centroid = response.aoi.get("centroid") or {}
        longitude = centroid.get("longitude", response.aoi.get("centroid_longitude"))
        latitude = centroid.get("latitude", response.aoi.get("centroid_latitude"))
        with session_scope() as session:
            support = build_decision_support(
                session,
                km=nearest_km(session, longitude, latitude),
                reference_date=analysis_period.end_date,
                decision=response.recommendation.decision,
            )
            return support.to_dict()
    except Exception:  # pragma: no cover - nunca invalida a resposta
        logger.exception("Falha ao montar o apoio do historico.")
        return None


def _persist_analysis(
    result: Any,
    response: AnalysisResponse,
    geometry: dict[str, Any],
    *,
    identity: AnalysisIdentity | None = None,
) -> None:
    """Grava a analise no historico, ja com o apoio anexado a resposta."""

    try:
        with session_scope() as session:
            save_analysis(
                session,
                response.model_dump(mode="json"),
                geometry=geometry,
                run_directory=(
                    str(result.run_directory) if result.run_directory else None
                ),
                artifacts={
                    name: str(value)
                    for name, value in (result.artifacts or {}).items()
                },
                identity=identity,
            )
    except Exception:  # pragma: no cover - nunca invalida a resposta
        logger.exception("Falha ao gravar a analise no historico.")


def _to_history_item(record: Any) -> AnalysisHistoryItem:
    centroid = None
    if record.centroid_longitude is not None and record.centroid_latitude is not None:
        centroid = Centroid(
            longitude=record.centroid_longitude, latitude=record.centroid_latitude
        )
    return AnalysisHistoryItem(
        analysis_id=record.id,
        created_at=record.created_at,
        status=record.status,
        decision=record.decision,
        confidence=record.confidence,
        summary=record.summary,
        period_start=record.period_start,
        period_end=record.period_end,
        selected_area_m2=record.selected_area_m2,
        analysis_quality_status=record.analysis_quality_status,
        observation_count=record.observation_count,
        nearest_km=record.nearest_km,
        centroid=centroid,
    )


@router.get("", response_model=AnalysisHistoryPage)
def list_analysis_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    decision: str | None = Query(None),
) -> AnalysisHistoryPage:
    """Historico de analises, da mais recente para a mais antiga."""

    with session_scope() as session:
        records = list_analyses(
            session, limit=limit, offset=offset, decision=decision
        )
        total = count_analyses(session, decision=decision)
        items = [_to_history_item(record) for record in records]
    return AnalysisHistoryPage(
        total=total, limit=limit, offset=offset, items=items
    )


@router.get("/{analysis_id}", response_model=AnalysisHistoryDetail)
def get_analysis_history_detail(analysis_id: UUID) -> AnalysisHistoryDetail:
    """Analise completa gravada, incluindo a geometria da AOI."""

    with session_scope() as session:
        record = get_analysis(session, str(analysis_id))
        if record is None or not record.payload:
            raise ApiError(
                "ANALYSIS_NOT_FOUND", "Analise nao encontrada.", status_code=404
            )
        return AnalysisHistoryDetail(
            analysis_id=record.id,
            created_at=record.created_at,
            geometry=record.geometry,
            result=AnalysisResponse.model_validate(record.payload),
        )


@router.post("/automatic", response_model=AutomaticAnalysisResponse)
def run_automatic_analysis(
    payload: AutomaticAnalysisRequest,
    service: AnalysisService = Depends(get_analysis_service),
    now: datetime = Depends(get_analysis_now),
    coordinator: AutomaticAnalysisCoordinator = Depends(
        get_automatic_analysis_coordinator
    ),
) -> dict[str, Any]:
    """Resolve, deduplicate, and asynchronously execute a canonical viewport AOI."""
    try:
        analysis_period = resolve_analysis_period(
            None,
            None,
            now=now,
            timezone_name=settings.analysis_timezone,
        )
    except ValueError as exc:
        raise ApiError("INVALID_DATE_RANGE", str(exc), status_code=422) from exc

    def execute(
        geometry: dict[str, Any],
        analysis_id: str,
        *,
        analysis_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validated_geometry_metadata(geometry)
        automatic_payload = AnalysisRunRequest(geometry=geometry)
        config = _build_monitoring_config(automatic_payload, analysis_period)
        result = service(config, analysis_id=analysis_id)
        if result.status == "failed":
            source_error_code = (
                str(result.errors[0].get("code") or "PROCESSING_ERROR")
                if result.errors
                else "PROCESSING_ERROR"
            )
            category = (
                "remote_stac_failure"
                if source_error_code == "SATELLITE_PROVIDER_ERROR"
                else "sentinel2_pipeline_failure"
            )
            raise AutomaticPipelineFailure(category, source_error_code)
        analysis_registry.add(result)
        response_model = _to_api_response(
            result,
            analysis_period,
            analysis_trigger="automatic_viewport",
        )
        support = _build_support(response_model, analysis_period)
        if support is not None:
            response_model.decision_support = DecisionSupportResponse.model_validate(
                support
            )
        # This callback runs only for a real pipeline execution. Polling and cache
        # hits are served by the coordinator repository and never persist again.
        identity = None
        if analysis_context and analysis_context.get("road"):
            identity = road_section_identity(
                str(analysis_context["spatial_key"]), analysis_context["road"]
            )
        _persist_analysis(result, response_model, geometry, identity=identity)
        response = response_model.model_dump(mode="json")
        return response

    return coordinator.request(
        bounds=payload.bounds.model_dump(),
        center=payload.center.model_dump(),
        zoom=payload.zoom,
        force_refresh=payload.force_refresh,
        analysis_period=(
            f"{analysis_period.start_date.isoformat()}/"
            f"{analysis_period.end_date.isoformat()}"
        ),
        execute=execute,
    )


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
