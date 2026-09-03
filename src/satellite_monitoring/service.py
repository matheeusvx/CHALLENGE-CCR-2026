"""Servico reutilizavel para executar o pipeline de monitoramento."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import numpy as np

from .config import MonitoringConfig
from .cut_recommendation import (
    RecommendationInput,
    RecommendationThresholds,
    aggregate_daily_observations,
    recommend_cut,
)
from .geometry import resolve_aoi
from .height_estimation import (
    disabled_height_estimation,
    estimate_height_class,
    unavailable_height_estimation,
)
from .features.vegetation_mask import extract_height_features
from .indices import InsufficientValidPixelsError, analyze_ndvi
from .multisource.runtime import (
    collect_multisource_evidence,
    write_multisource_evidence,
)
from .outputs import create_run_directory, to_json_compatible, write_outputs
from .quality import (
    assess_scene_quality,
    determine_overall_status,
    select_quality_assessed_observations,
    summarize_scene_quality,
)
from .raster_processing import (
    SCL_EXCLUDED_CLASSES,
    calculate_mask_intersection_area,
    read_scene_bands,
)
from .segment_first_analysis import run_segment_first_shadow_segmentation
from .spatial_segmentation import SpatialRasterObservation
from .spatial_regularization import evaluate_spatial_regularization
from .stac_client import Scene, search_scenes
from .temporal_quality import calculate_analysis_quality, diagnose_temporal_consistency


class InvalidAnalysisGeometryError(ValueError):
    """Indica que a area de interesse nao pode ser resolvida."""


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    message: str
    current: int | None = None
    total: int | None = None
    item_id: str | None = None


@dataclass(frozen=True)
class PipelineDependencies:
    """Pontos de substituicao usados por adaptadores e testes."""

    search_scenes: Callable[..., Any] = search_scenes
    read_scene_bands: Callable[..., Any] = read_scene_bands
    extract_height_features: Callable[..., dict[str, Any]] = extract_height_features
    estimate_height: Callable[..., dict[str, Any]] = estimate_height_class
    segment_spatial: Callable[..., dict[str, Any]] = run_segment_first_shadow_segmentation
    regularize_spatial: Callable[..., dict[str, Any]] = evaluate_spatial_regularization
    collect_multisource: Callable[..., dict[str, Any] | None] = (
        collect_multisource_evidence
    )
    write_multisource_artifact: Callable[..., Path] = write_multisource_evidence
    write_outputs: Callable[..., dict[str, Path]] = write_outputs


@dataclass
class AnalysisResult:
    analysis_id: str
    status: str
    exit_code: int
    recommendation: dict[str, Any]
    aoi: dict[str, Any]
    summary: dict[str, Any]
    timeseries: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    selected_area_m2: float | None = None
    effective_analysis_area_m2: float | None = None
    effective_analysis_pct: float | None = None
    spatial_segmentation: dict[str, Any] | None = None
    multisource: dict[str, Any] | None = None
    height_estimation: dict[str, Any] = field(default_factory=disabled_height_estimation)
    artifacts: dict[str, Path] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    run_directory: Path | None = None

    @property
    def confidence(self) -> str:
        return str(self.recommendation.get("confidence", "low"))

    @property
    def reasons(self) -> list[str]:
        return list(self.recommendation.get("reasons") or [])

    @property
    def blocking_reasons(self) -> list[str]:
        return list(self.recommendation.get("blocking_reasons") or [])

    def to_dict(self, *, include_internal_paths: bool = False) -> dict[str, Any]:
        payload = {
            "analysis_id": self.analysis_id,
            "status": self.status,
            "recommendation": self.recommendation,
            "height_estimation": self.height_estimation,
            "aoi": self.aoi,
            "summary": self.summary,
            "timeseries": self.timeseries,
            "scenes": self.scenes,
            "selected_area_m2": self.selected_area_m2,
            "effective_analysis_area_m2": self.effective_analysis_area_m2,
            "effective_analysis_pct": self.effective_analysis_pct,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        if self.spatial_segmentation is not None:
            payload["spatial_segmentation"] = self.spatial_segmentation
        if self.multisource is not None:
            payload["multisource"] = self.multisource
        if include_internal_paths:
            payload["artifacts"] = {key: str(path) for key, path in self.artifacts.items()}
        else:
            payload["artifacts"] = {key: path.name for key, path in self.artifacts.items()}
        return to_json_compatible(payload)


def _emit(callback: Callable[[ProgressEvent], None] | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


def _new_scene_record(scene: Scene) -> dict[str, Any]:
    record = scene.to_record()
    record.update(
        {
            "valid_pixel_percentage": None,
            "local_valid_pixel_percentage": None,
            "local_invalid_pixel_percentage": None,
            "valid_pixel_count": None,
            "total_pixel_count": None,
            "aoi_coverage_percentage": None,
            "partial_raster_coverage": None,
            "has_scl": False,
            "global_cloud_cover": record.get("cloud_cover"),
            "scl_class_percentages": {},
            "ndvi_mean": None,
            "ndvi_median": None,
            "ndvi_std": None,
            "effective_analysis_area_m2": None,
            "red_median_reflectance": None,
            "nir_median_reflectance": None,
            "reflectance_scale_source": None,
            "reflectance_scale": None,
            "reflectance_offset": None,
            "height_ndvi_median": None,
            "vegetation_fraction": None,
            "height_valid_pixel_count": None,
            "height_total_pixel_count": None,
            "mixed_pixel_risk": None,
            "height_purity_gate_passed": None,
            "height_purity_gate_reasons": [],
            "height_mask_configuration": None,
            "scene_quality_score": None,
            "min_pixel_requirement_met": False,
            "aoi_coverage_requirement_met": False,
            "scl_required_met": False,
            "quality_status": "unknown",
            "quality_reasons": [],
            "accepted_for_timeseries": False,
            "selected_after_quality": False,
            "temporal_outlier_suspected": False,
            "included_in_analysis": False,
            "exclusion_reasons": [],
            "processing_status": "processing",
            "error": None,
        }
    )
    return record


def _effective_date_range(records: list[dict[str, Any]]) -> dict[str, str | None]:
    if not records:
        return {"start": None, "end": None}
    dates = sorted(record["datetime"] for record in records)
    return {"start": dates[0], "end": dates[-1]}


def _record_date(record: dict[str, Any]) -> str:
    return datetime.fromisoformat(
        str(record["datetime"]).replace("Z", "+00:00")
    ).date().isoformat()


def run_monitoring_analysis(
    config: MonitoringConfig,
    *,
    analysis_id: str | None = None,
    dependencies: PipelineDependencies | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> AnalysisResult:
    """Executa a analise sem depender de terminal, HTTP ou subprocessos."""
    deps = dependencies or PipelineDependencies()
    try:
        resolved_aoi = resolve_aoi(config)
    except (FileNotFoundError, ValueError) as exc:
        raise InvalidAnalysisGeometryError(str(exc)) from exc

    identifier = analysis_id or str(uuid4())
    started_at = datetime.now(timezone.utc)
    run_directory = create_run_directory(config.output_root)
    aoi_geojson = resolved_aoi.geometry
    scene_records: list[dict[str, Any]] = []
    quality_observation_records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    processed_item_ids: list[str] = []
    failed_item_ids: list[str] = []
    spatial_accounting_inputs: dict[str, dict[str, Any]] = {}
    spatial_segmentation_inputs: dict[str, SpatialRasterObservation] = {}
    discarded_candidate_scenes: list[dict[str, Any]] = []
    candidate_scenes: list[Scene] = []
    total_matches = 0
    fatal_error: str | None = None

    _emit(on_progress, ProgressEvent("search_started", "Consultando cenas Sentinel-2."))
    try:
        search_result = deps.search_scenes(config, aoi_geojson)
        total_matches = search_result.total_matches
        candidate_scenes = search_result.scenes
        discarded_candidate_scenes = [
            {
                "item_id": scene.item_id,
                "datetime": scene.datetime.isoformat(),
                "cloud_cover": scene.item.properties.get("eo:cloud_cover"),
                "reason": "max_candidate_scenes_safety_limit",
            }
            for scene in search_result.discarded_scenes
        ]
        _emit(
            on_progress,
            ProgressEvent(
                "search_completed",
                f"{total_matches} cenas encontradas; {len(candidate_scenes)} candidatas.",
                total=len(candidate_scenes),
            ),
        )

        for position, scene in enumerate(candidate_scenes, start=1):
            _emit(
                on_progress,
                ProgressEvent(
                    "scene_started",
                    f"Processando {scene.item_id}.",
                    current=position,
                    total=len(candidate_scenes),
                    item_id=scene.item_id,
                ),
            )
            scene_record = _new_scene_record(scene)
            try:
                raster_data = deps.read_scene_bands(scene.item, aoi_geojson)
                statistics = None
                ndvi_values = None
                try:
                    ndvi_values, statistics = analyze_ndvi(
                        raster_data.red,
                        raster_data.nir,
                        raster_data.valid_mask,
                        raster_data.total_pixel_count,
                    )
                except InsufficientValidPixelsError:
                    pass

                if ndvi_values is not None:
                    spatial_accounting_inputs[scene.item_id] = {
                        "transform": raster_data.spatial_transform,
                        "geometry_document": raster_data.spatial_aoi_geometry,
                        "crs_is_projected": raster_data.spatial_crs_is_projected,
                        "accepted_pixel_mask": np.isfinite(ndvi_values).copy(),
                    }

                height_features: dict[str, Any] = {}
                if config.height_estimation_enabled and statistics is not None:
                    try:
                        height_features = deps.extract_height_features(
                            scene.item, raster_data
                        )
                    except Exception as exc:
                        warnings.append(
                            {
                                "code": "HEIGHT_FEATURES_UNAVAILABLE",
                                "item_id": scene.item_id,
                                "message": f"Features experimentais indisponiveis: {exc}",
                            }
                        )

                valid_pixel_count = statistics.valid_pixel_count if statistics else 0
                valid_pixel_percentage = statistics.valid_pixel_percentage if statistics else 0.0
                assessment = assess_scene_quality(
                    valid_pixel_percentage=valid_pixel_percentage,
                    valid_pixel_count=valid_pixel_count,
                    min_valid_pixel_percentage=config.min_valid_pixel_percentage,
                    min_valid_pixel_count=config.min_valid_pixel_count,
                    medium_threshold=config.medium_quality_threshold,
                    high_threshold=config.high_quality_threshold,
                    has_scl=raster_data.scl_asset is not None,
                    scl_class_percentages=raster_data.scl_class_percentages,
                    cloud_cover=scene_record["cloud_cover"],
                    max_cloud_cover=config.max_cloud_cover,
                    aoi_coverage_percentage=raster_data.aoi_coverage_percentage,
                    min_aoi_coverage_percentage=config.min_aoi_coverage_percentage,
                    partial_raster_coverage=raster_data.partial_raster_coverage,
                    has_valid_ndvi_pixels=statistics is not None,
                    include_low_quality_scenes=config.include_low_quality_scenes,
                )
                scene_record.update(
                    {
                        "valid_pixel_percentage": valid_pixel_percentage,
                        "local_valid_pixel_percentage": assessment.local_valid_pixel_percentage,
                        "local_invalid_pixel_percentage": assessment.local_invalid_pixel_percentage,
                        "valid_pixel_count": valid_pixel_count,
                        "total_pixel_count": raster_data.total_pixel_count,
                        "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                        "partial_raster_coverage": raster_data.partial_raster_coverage,
                        "has_scl": raster_data.scl_asset is not None,
                        "scl_class_percentages": raster_data.scl_class_percentages,
                        "ndvi_mean": statistics.mean if statistics else None,
                        "ndvi_median": statistics.median if statistics else None,
                        "ndvi_std": statistics.std if statistics else None,
                        "red_median_reflectance": height_features.get(
                            "red_median_reflectance"
                        ),
                        "nir_median_reflectance": height_features.get(
                            "nir_median_reflectance"
                        ),
                        "reflectance_scale_source": height_features.get(
                            "reflectance_scale_source"
                        ),
                        "reflectance_scale": height_features.get("reflectance_scale"),
                        "reflectance_offset": height_features.get("reflectance_offset"),
                        "height_ndvi_median": height_features.get("ndvi_median"),
                        "vegetation_fraction": height_features.get("vegetation_fraction"),
                        "height_valid_pixel_count": height_features.get(
                            "height_valid_pixel_count"
                        ),
                        "height_total_pixel_count": height_features.get(
                            "height_total_pixel_count"
                        ),
                        "mixed_pixel_risk": height_features.get("mixed_pixel_risk"),
                        "height_purity_gate_passed": height_features.get(
                            "height_purity_gate_passed"
                        ),
                        "height_purity_gate_reasons": height_features.get(
                            "height_purity_gate_reasons", []
                        ),
                        "height_mask_configuration": height_features.get(
                            "height_mask_configuration"
                        ),
                        "scene_quality_score": assessment.scene_quality_score,
                        "min_pixel_requirement_met": assessment.min_pixel_requirement_met,
                        "aoi_coverage_requirement_met": assessment.aoi_coverage_requirement_met,
                        "scl_required_met": assessment.scl_required_met,
                        "quality_status": assessment.quality_status,
                        "quality_reasons": list(assessment.quality_reasons),
                        "accepted_for_timeseries": assessment.accepted_for_timeseries,
                        "processing_status": "processed",
                    }
                )
                if (
                    config.spatial_segmentation_enabled
                    and ndvi_values is not None
                    and raster_data.inside_aoi_mask is not None
                    and raster_data.spatial_transform is not None
                    and raster_data.spatial_aoi_geometry is not None
                    and raster_data.spatial_crs is not None
                ):
                    accepted_mask = np.isfinite(ndvi_values)
                    rejection_reasons = np.full(ndvi_values.shape, "", dtype=object)
                    inside_mask = np.asarray(raster_data.inside_aoi_mask, dtype=bool)
                    rejection_reasons[inside_mask & ~accepted_mask] = (
                        "QUALITY_MASK_REJECTED"
                    )
                    if raster_data.scl_values is not None:
                        scl_rejected = inside_mask & np.isin(
                            raster_data.scl_values,
                            list(SCL_EXCLUDED_CLASSES),
                        )
                        rejection_reasons[scl_rejected] = "SCL_REJECTED"
                    spatial_segmentation_inputs[scene.item_id] = (
                        SpatialRasterObservation(
                            item_id=scene.item_id,
                            datetime=str(scene_record["datetime"]),
                            ndvi=ndvi_values,
                            valid_mask=accepted_mask,
                            inside_aoi_mask=inside_mask,
                            transform=raster_data.spatial_transform,
                            aoi_geometry=raster_data.spatial_aoi_geometry,
                            crs=raster_data.spatial_crs,
                            crs_is_projected=bool(
                                raster_data.spatial_crs_is_projected
                            ),
                            scene_quality_score=assessment.scene_quality_score,
                            quality_status=assessment.quality_status,
                            cloud_cover=scene_record.get("cloud_cover"),
                            rejection_reasons=rejection_reasons,
                            has_scl=raster_data.scl_asset is not None,
                            scl_values=(
                                np.asarray(raster_data.scl_values).copy()
                                if raster_data.scl_values is not None
                                else None
                            ),
                            scl_class_percentages=dict(
                                raster_data.scl_class_percentages
                            ),
                            aoi_coverage_percentage=(
                                raster_data.aoi_coverage_percentage
                            ),
                            partial_raster_coverage=(
                                raster_data.partial_raster_coverage
                            ),
                        )
                    )
                processed_item_ids.append(scene.item_id)
                warnings.extend(
                    {"item_id": scene.item_id, "message": message}
                    for message in raster_data.quality_messages
                )
                if statistics is not None:
                    quality_observation_records.append(
                        {
                            "item_id": scene.item_id,
                            "datetime": scene_record["datetime"],
                            "cloud_cover": scene_record["cloud_cover"],
                            "platform": scene_record["platform"],
                            "tile": scene_record["tile"],
                            "red_asset": raster_data.red_asset,
                            "nir_asset": raster_data.nir_asset,
                            "scl_asset": raster_data.scl_asset,
                            "scl_class_percentages": raster_data.scl_class_percentages,
                            "ndvi_mean": statistics.mean,
                            "ndvi_median": statistics.median,
                            "ndvi_std": statistics.std,
                            "red_median_reflectance": height_features.get(
                                "red_median_reflectance"
                            ),
                            "nir_median_reflectance": height_features.get(
                                "nir_median_reflectance"
                            ),
                            "height_ndvi_median": height_features.get("ndvi_median"),
                            "vegetation_fraction": height_features.get(
                                "vegetation_fraction"
                            ),
                            "height_valid_pixel_count": height_features.get(
                                "height_valid_pixel_count"
                            ),
                            "height_total_pixel_count": height_features.get(
                                "height_total_pixel_count"
                            ),
                            "mixed_pixel_risk": height_features.get("mixed_pixel_risk"),
                            "height_purity_gate_passed": height_features.get(
                                "height_purity_gate_passed"
                            ),
                            "height_purity_gate_reasons": height_features.get(
                                "height_purity_gate_reasons", []
                            ),
                            "height_mask_configuration": height_features.get(
                                "height_mask_configuration"
                            ),
                            "ndvi_min": statistics.minimum,
                            "ndvi_max": statistics.maximum,
                            "valid_pixel_count": statistics.valid_pixel_count,
                            "total_pixel_count": raster_data.total_pixel_count,
                            "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                            "partial_raster_coverage": raster_data.partial_raster_coverage,
                            "valid_pixel_percentage": statistics.valid_pixel_percentage,
                            "local_valid_pixel_percentage": assessment.local_valid_pixel_percentage,
                            "local_invalid_pixel_percentage": assessment.local_invalid_pixel_percentage,
                            "scene_quality_score": assessment.scene_quality_score,
                            "min_pixel_requirement_met": assessment.min_pixel_requirement_met,
                            "aoi_coverage_requirement_met": assessment.aoi_coverage_requirement_met,
                            "scl_required_met": assessment.scl_required_met,
                            "quality_status": assessment.quality_status,
                            "quality_reasons": list(assessment.quality_reasons),
                            "accepted_for_timeseries": assessment.accepted_for_timeseries,
                        }
                    )
            except Exception as exc:  # Uma cena nao deve impedir as seguintes.
                message = str(exc)
                scene_record.update(
                    {
                        "quality_status": "unknown",
                        "quality_reasons": ["processing_error"],
                        "accepted_for_timeseries": False,
                        "processing_status": "failed",
                        "error": message,
                    }
                )
                errors.append({"code": "PROCESSING_ERROR", "item_id": scene.item_id, "message": message})
                failed_item_ids.append(scene.item_id)
            scene_records.append(scene_record)
    except Exception as exc:
        fatal_error = str(exc)
        errors.append({"code": "SATELLITE_PROVIDER_ERROR", "message": fatal_error})

    selected_observations, discarded_after_quality = select_quality_assessed_observations(
        quality_observation_records,
        max_scenes=config.max_scenes,
        scene_order=config.scene_order,
    )
    selected_ids = {str(record["item_id"]) for record in selected_observations}
    discarded_after_quality_ids = {
        str(record["item_id"]) for record in discarded_after_quality
    }
    scene_by_id = {str(record["item_id"]): record for record in scene_records}
    for record in scene_records:
        item_id = str(record["item_id"])
        record["selected_after_quality"] = item_id in selected_ids
        if not record.get("accepted_for_timeseries"):
            record["exclusion_reasons"] = list(record.get("quality_reasons") or [])
        elif item_id in discarded_after_quality_ids:
            record["exclusion_reasons"] = ["max_scenes_limit_after_quality"]

    daily_result = aggregate_daily_observations(
        selected_observations,
        strategy=config.daily_aggregation,
    )
    candidates_by_day: dict[str, list[dict[str, Any]]] = {}
    for record in scene_records:
        if not record.get("datetime"):
            continue
        candidates_by_day.setdefault(_record_date(record), []).append(record)
    for day_audit in daily_result.audit["days"]:
        candidates = sorted(
            candidates_by_day.get(day_audit["date"], []),
            key=lambda record: str(record.get("item_id", "")),
        )
        day_audit["all_candidate_item_ids"] = [
            str(record.get("item_id", "")) for record in candidates
        ]
        day_audit["candidate_ranking_inputs"] = [
            {
                "item_id": record.get("item_id"),
                "accepted": bool(record.get("accepted_for_timeseries")),
                "scene_quality_score": record.get("scene_quality_score"),
                "valid_pixel_percentage": record.get("valid_pixel_percentage"),
                "aoi_coverage_percentage": record.get("aoi_coverage_percentage"),
                "cloud_cover": record.get("cloud_cover"),
            }
            for record in candidates
        ]
    temporal_result = diagnose_temporal_consistency(
        daily_result.observations,
        min_deviation=config.temporal_outlier_min_deviation,
        mad_multiplier=config.temporal_outlier_mad_multiplier,
        return_ratio=config.temporal_return_ratio,
    )
    raw_daily_records = temporal_result.raw_daily_timeseries
    analysis_records = temporal_result.analysis_timeseries
    selected_area_m2 = float(resolved_aoi.metadata["area_square_meters"])
    effective_analysis_area_m2 = None
    effective_analysis_pct = None
    if analysis_records:
        accounting_record = analysis_records[-1]
        accounting_item_id = accounting_record.get("aggregation_selected_item_id")
        accounting_inputs = spatial_accounting_inputs.get(str(accounting_item_id))
        try:
            if accounting_inputs is None:
                raise ValueError(
                    "A agregacao selecionada nao referencia uma unica cena raster."
                )
            effective_analysis_area_m2 = calculate_mask_intersection_area(
                **accounting_inputs
            )
            effective_analysis_pct = (
                effective_analysis_area_m2 / selected_area_m2 * 100.0
            )
            accounting_record["effective_analysis_area_m2"] = (
                effective_analysis_area_m2
            )
            selected_scene_record = scene_by_id.get(str(accounting_item_id))
            if selected_scene_record is not None:
                selected_scene_record["effective_analysis_area_m2"] = (
                    effective_analysis_area_m2
                )
        except Exception as exc:
            warnings.append(
                {
                    "code": "EFFECTIVE_AREA_UNAVAILABLE",
                    "message": (
                        "A observacao operacional selecionada nao permitiu "
                        f"contabilidade espacial: {exc}"
                    ),
                }
            )
    for daily_record in raw_daily_records:
        source_ids = list(daily_record.get("aggregation_source_item_ids") or [])
        selected_item_id = daily_record.get("aggregation_selected_item_id")
        included_source_ids = (
            source_ids
            if daily_record.get("daily_aggregation") == "median"
            else [selected_item_id]
        )
        for item_id in source_ids:
            scene_record = scene_by_id.get(str(item_id))
            if scene_record is None:
                continue
            if item_id not in included_source_ids:
                scene_record["exclusion_reasons"] = ["not_selected_by_daily_aggregation"]
                continue
            scene_record["temporal_outlier_suspected"] = bool(
                daily_record.get("temporal_outlier_suspected")
            )
            scene_record["included_in_analysis"] = bool(
                daily_record.get("included_in_analysis")
            )
            scene_record["exclusion_reasons"] = list(
                daily_record.get("exclusion_reasons") or []
            )

    quality_summary = summarize_scene_quality(scene_records)
    analysis_quality = calculate_analysis_quality(
        analysis_records,
        raw_records=raw_daily_records,
        processed_scene_count=len(processed_item_ids),
        rejected_scene_count=quality_summary["rejected_scene_count"],
        max_gap_days=config.max_gap_days,
        min_observations=config.min_observations,
    )
    for record in analysis_records:
        record["analysis_quality_status"] = analysis_quality["status"]

    thresholds = RecommendationThresholds(
        decision_min_observations=config.decision_min_observations,
        high_vegetation_percentile=config.high_vegetation_percentile,
        significant_drop_absolute=config.significant_drop_absolute,
        significant_drop_relative_percentage=config.significant_drop_relative_percentage,
        trend_window=config.trend_window,
        max_gap_days=config.max_gap_days,
        recent_intervention_days=config.recent_intervention_days,
    )
    assert config.end_date is not None
    recommendation_result = recommend_cut(
        RecommendationInput(
            observations=analysis_records,
            thresholds=thresholds,
            reference_date=config.end_date,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        )
    )
    recommendation = recommendation_result.to_dict()
    multisource = None
    if config.multisource_enabled:
        try:
            multisource = deps.collect_multisource(config, aoi_geojson)
        except Exception as exc:
            multisource = {
                "enabled": True,
                "fusion_mode": config.multisource_fusion_mode,
                "official_recommendation_changed": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "configuration": {
                    "sentinel1_enabled": config.sentinel1_enabled,
                    "sentinel1_collection": config.sentinel1_collection,
                    "sentinel1_max_scenes": config.sentinel1_max_scenes,
                    "analysis_period": config.datetime_range,
                },
                "sources": [
                    {
                        "source": "sentinel-1",
                        "status": "error",
                        "quality": None,
                        "coverage": None,
                        "observed_at": None,
                        "observations": [],
                        "metrics": {},
                        "provenance": {
                            "provider": "sentinel-1",
                            "error_type": type(exc).__name__,
                        },
                        "warnings": ["Auxiliary provider failed while collecting evidence."],
                    }
                ],
            }
        for source in multisource.get("sources", []) if multisource else []:
            if source.get("status") in {"unavailable", "error"}:
                warnings.append(
                    {
                        "code": "MULTISOURCE_SOURCE_UNAVAILABLE",
                        "source": source.get("source"),
                        "message": "Fonte auxiliar indisponivel; a analise Sentinel-2 continuou.",
                    }
                )
    spatial_segmentation = None
    if config.spatial_segmentation_enabled:
        try:
            spatial_segmentation = deps.segment_spatial(
                observations=list(spatial_segmentation_inputs.values()),
                daily_records=analysis_records,
                config=config,
                selected_area_m2=selected_area_m2,
                effective_analysis_area_m2=effective_analysis_area_m2,
            )
        except Exception as exc:
            spatial_segmentation = {
                "status": "unavailable",
                "experimental": True,
                "section_length_m": config.spatial_section_length_m,
                "effective_coverage_pct": None,
                "zones": [],
            }
            warnings.append(
                {
                    "code": "SPATIAL_SEGMENTATION_UNAVAILABLE",
                    "message": f"Segmentacao espacial experimental indisponivel: {exc}",
                }
            )
        if (
            config.spatial_regularization_enabled
            and spatial_segmentation.get("status") == "experimental"
        ):
            spatial_shadow = spatial_segmentation
            raw_segmentation = spatial_shadow.get(
                "raw_segmentation", spatial_shadow
            )
            try:
                spatial_segmentation = deps.regularize_spatial(raw_segmentation)
                spatial_segmentation["official_recommendation_changed"] = False
                if "segment_first_temporal" in spatial_shadow:
                    spatial_segmentation["segment_first_temporal"] = spatial_shadow[
                        "segment_first_temporal"
                    ]
            except Exception as exc:
                spatial_segmentation = {
                    "status": "experimental",
                    "mode": "shadow",
                    "official_recommendation_changed": False,
                    "raw_segmentation": raw_segmentation,
                    "operational_segmentation": {
                        "status": "unavailable",
                        "error": str(exc),
                    },
                    "regularization": {
                        "status": "unavailable",
                        "recommended_mmu": None,
                        "warnings": ["SPATIAL_REGULARIZATION_UNAVAILABLE"],
                    },
                }
                warnings.append(
                    {
                        "code": "SPATIAL_REGULARIZATION_UNAVAILABLE",
                        "message": (
                            "Regularizacao espacial experimental indisponivel: "
                            f"{exc}"
                        ),
                    }
                )
    if config.height_estimation_enabled:
        height_source_records = [
            record
            for record in scene_records
            if record.get("included_in_analysis")
            and record.get("vegetation_fraction") is not None
        ]
        if height_source_records:
            height_source = max(
                height_source_records, key=lambda record: str(record.get("datetime") or "")
            )
            try:
                height_estimation = deps.estimate_height(
                    {
                        "red_reflectance": height_source.get(
                            "red_median_reflectance"
                        ),
                        "nir_reflectance": height_source.get(
                            "nir_median_reflectance"
                        ),
                        "ndvi": height_source.get("height_ndvi_median"),
                        "vegetation_fraction": height_source[
                            "vegetation_fraction"
                        ],
                        "height_valid_pixel_count": height_source.get(
                            "height_valid_pixel_count"
                        ),
                        "height_total_pixel_count": height_source.get(
                            "height_total_pixel_count"
                        ),
                        "mixed_pixel_risk": height_source.get("mixed_pixel_risk"),
                        "height_purity_gate_passed": height_source.get(
                            "height_purity_gate_passed"
                        ),
                        "height_purity_gate_reasons": height_source.get(
                            "height_purity_gate_reasons", []
                        ),
                        "height_mask_configuration": height_source.get(
                            "height_mask_configuration"
                        ),
                    }
                )
            except Exception as exc:
                height_estimation = unavailable_height_estimation()
                warnings.append(
                    {
                        "code": "HEIGHT_ESTIMATION_UNAVAILABLE",
                        "message": f"Estimativa experimental indisponivel: {exc}",
                    }
                )
        else:
            height_estimation = unavailable_height_estimation()
    else:
        height_estimation = disabled_height_estimation()
    overall_status = determine_overall_status(
        fatal_error=fatal_error,
        processed_scene_count=len(processed_item_ids),
        accepted_scene_count=len(analysis_records),
        min_observations=config.min_observations,
    )
    if not candidate_scenes and fatal_error is None:
        errors.append(
            {
                "code": "NO_SCENES_FOUND",
                "message": "Nenhuma cena foi encontrada para os filtros informados.",
            }
        )
    if overall_status == "insufficient_observations":
        warnings.append(
            {
                "code": "INSUFFICIENT_OBSERVATIONS",
                "message": f"Observacoes aceitas insuficientes ({len(analysis_records)}/{config.min_observations}).",
            }
        )

    quality_report = {
        "configuration": {
            "max_candidate_scenes": config.max_candidate_scenes,
            "effective_max_candidate_scenes": config.effective_max_candidate_scenes,
            "max_scenes": config.max_scenes,
            "min_valid_pixel_percentage": config.min_valid_pixel_percentage,
            "min_valid_pixel_count": config.min_valid_pixel_count,
            "min_aoi_coverage_percentage": config.min_aoi_coverage_percentage,
            "scl_required": True,
            "temporal_outlier_min_deviation": config.temporal_outlier_min_deviation,
            "temporal_outlier_mad_multiplier": config.temporal_outlier_mad_multiplier,
            "temporal_return_ratio": config.temporal_return_ratio,
            "experimental": True,
        },
        "candidate_scene_count": len(candidate_scenes),
        "candidate_scenes_discarded_by_safety_limit": discarded_candidate_scenes,
        "processed_scenes": scene_records,
        "rejected_scenes": quality_summary["rejected_scenes"],
        "observations_discarded_after_quality_limit": discarded_after_quality,
        "raw_daily_timeseries": raw_daily_records,
        "analysis_timeseries": analysis_records,
        "outliers": temporal_result.outliers,
        "analysis_quality": analysis_quality,
        "limitations": [
            "Os limiares de qualidade sao experimentais e ainda exigem validacao de campo.",
            "As classes SCL auditam contaminacao; nao identificam altura ou especie vegetal.",
        ],
    }

    summary = {
        "analysis_id": identifier,
        "parameters": config.to_dict(),
        "endpoint": config.endpoint,
        "collection": config.collection,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
        "overall_status": overall_status,
        "scene_order": config.scene_order,
        "quality_thresholds": config.quality_thresholds,
        "aoi": resolved_aoi.metadata,
        "scene_count_found": total_matches,
        "candidate_scene_count": len(candidate_scenes),
        "scene_count_selected": len(selected_observations),
        "scene_count_not_selected_due_to_limit": (
            len(discarded_candidate_scenes) + len(discarded_after_quality)
        ),
        "scene_count_not_processed_due_to_candidate_limit": len(discarded_candidate_scenes),
        "scene_count_not_selected_after_quality": len(discarded_after_quality),
        "scene_count_processed": len(processed_item_ids),
        "scene_count_ignored": len(failed_item_ids),
        "first_selected_datetime": selected_observations[0]["datetime"] if selected_observations else None,
        "last_selected_datetime": selected_observations[-1]["datetime"] if selected_observations else None,
        "date_range_effectively_processed": _effective_date_range(analysis_records),
        "daily_aggregation": daily_result.audit,
        "raw_daily_observation_count": len(raw_daily_records),
        "daily_observation_count": len(analysis_records),
        "analysis_quality": analysis_quality,
        "recommendation_thresholds": config.recommendation_thresholds,
        "cut_recommendation": recommendation_result.to_summary_dict(),
        "height_estimation": height_estimation,
        "spatial_accounting": {
            "selected_area_m2": selected_area_m2,
            "effective_analysis_area_m2": effective_analysis_area_m2,
            "effective_analysis_pct": effective_analysis_pct,
            "metric_weighting_changed": False,
        },
        "scenes_discarded_by_limit": [
            *discarded_candidate_scenes,
            *[
                {
                    "item_id": record.get("item_id"),
                    "datetime": record.get("datetime"),
                    "cloud_cover": record.get("cloud_cover"),
                    "reason": "max_scenes_limit_after_quality",
                }
                for record in discarded_after_quality
            ],
        ],
        "ignored_item_ids": failed_item_ids,
        "quality_messages": warnings,
        "errors_by_scene": [error for error in errors if error.get("item_id")],
        "fatal_error": fatal_error,
        "processed_item_ids": processed_item_ids,
        "trend_interpretation": None,
        **quality_summary,
    }
    if spatial_segmentation is not None:
        summary["spatial_segmentation"] = spatial_segmentation
    if multisource is not None:
        summary["multisource"] = multisource

    try:
        artifact_paths = deps.write_outputs(
            run_directory,
            scene_records,
            analysis_records,
            summary,
            resolved_aoi.output_geojson,
            recommendation,
            raw_daily_records=raw_daily_records,
            quality_report=quality_report,
        )
    except Exception as exc:
        errors.append({"code": "PROCESSING_ERROR", "message": f"Falha ao salvar resultados: {exc}"})
        return AnalysisResult(
            identifier,
            "failed",
            1,
            recommendation,
            resolved_aoi.metadata,
            to_json_compatible(summary),
            to_json_compatible(analysis_records),
            to_json_compatible(scene_records),
            selected_area_m2=selected_area_m2,
            effective_analysis_area_m2=effective_analysis_area_m2,
            effective_analysis_pct=effective_analysis_pct,
            spatial_segmentation=spatial_segmentation,
            multisource=multisource,
            height_estimation=height_estimation,
            warnings=warnings,
            errors=errors,
            run_directory=run_directory,
        )

    public_artifacts = {
        "scenes_csv": artifact_paths["scenes"],
        "timeseries_csv": artifact_paths["timeseries"],
        "summary": artifact_paths["summary"],
        "chart": artifact_paths["plot"],
        "aoi": artifact_paths["aoi"],
        "recommendation_json": artifact_paths["recommendation_json"],
        "recommendation_csv": artifact_paths["recommendation_csv"],
        "quality_report": artifact_paths["quality_report"],
        "raw_timeseries_csv": artifact_paths["raw_timeseries"],
    }
    if multisource is not None:
        try:
            public_artifacts["multisource_evidence"] = (
                deps.write_multisource_artifact(run_directory, multisource)
            )
        except Exception as exc:
            warnings.append(
                {
                    "code": "MULTISOURCE_ARTIFACT_UNAVAILABLE",
                    "message": (
                        "Evidencia multissensor coletada, mas o artefato nao pode ser salvo: "
                        f"{type(exc).__name__}."
                    ),
                }
            )
    status = {
        "success": "completed",
        "insufficient_observations": "insufficient_observations",
        "failed": "failed",
    }[overall_status]
    return AnalysisResult(
        identifier,
        status,
        1 if overall_status == "failed" else 0,
        recommendation,
        resolved_aoi.metadata,
        to_json_compatible(summary),
        to_json_compatible(analysis_records),
        to_json_compatible(scene_records),
        selected_area_m2=selected_area_m2,
        effective_analysis_area_m2=effective_analysis_area_m2,
        effective_analysis_pct=effective_analysis_pct,
        spatial_segmentation=spatial_segmentation,
        multisource=multisource,
        height_estimation=height_estimation,
        artifacts=public_artifacts,
        warnings=warnings,
        errors=errors,
        run_directory=run_directory,
    )
