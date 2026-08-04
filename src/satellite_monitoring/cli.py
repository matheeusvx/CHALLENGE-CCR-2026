"""Interface de linha de comando do monitoramento Sentinel-2 por NDVI."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import (
    DEFAULT_DAILY_AGGREGATION,
    DEFAULT_DECISION_MIN_OBSERVATIONS,
    DEFAULT_HIGH_VEGETATION_PERCENTILE,
    DEFAULT_MAX_SCENES,
    DEFAULT_MAX_GAP_DAYS,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_MIN_VALID_PIXEL_PERCENTAGE,
    DEFAULT_RECENT_INTERVENTION_DAYS,
    DEFAULT_SCENE_ORDER,
    DEFAULT_SIGNIFICANT_DROP_ABSOLUTE,
    DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE,
    DEFAULT_TREND_WINDOW,
    MonitoringConfig,
    parse_iso_date,
)
from .cut_recommendation import (
    RecommendationInput,
    RecommendationResult,
    RecommendationThresholds,
    aggregate_daily_observations,
    recommend_cut,
)
from .geometry import resolve_aoi
from .indices import InsufficientValidPixelsError, analyze_ndvi
from .outputs import create_run_directory, write_outputs
from .quality import assess_scene_quality, determine_overall_status, summarize_scene_quality
from .raster_processing import read_scene_bands
from .stac_client import Scene, search_scenes


def _date_argument(value: str) -> date:
    try:
        return parse_iso_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera uma serie temporal real de NDVI com Sentinel-2 L2A."
    )
    parser.add_argument(
        "--geometry-file",
        type=Path,
        help="Arquivo GeoJSON EPSG:4326 com Polygon, MultiPolygon ou Features poligonais.",
    )
    parser.add_argument("--latitude", type=float, help="Latitude do centro circular em EPSG:4326.")
    parser.add_argument("--longitude", type=float, help="Longitude do centro circular em EPSG:4326.")
    parser.add_argument("--radius-meters", type=float, help="Raio da area circular em metros.")
    parser.add_argument("--start-date", type=_date_argument, required=True, help="Data inicial no formato AAAA-MM-DD.")
    parser.add_argument("--end-date", type=_date_argument, required=True, help="Data final no formato AAAA-MM-DD.")
    parser.add_argument(
        "--max-cloud-cover",
        type=float,
        default=20.0,
        help="Cobertura global maxima de nuvens do item, entre 0 e 100.",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=DEFAULT_MAX_SCENES,
        help="Quantidade maxima de cenas processadas.",
    )
    parser.add_argument(
        "--scene-order",
        choices=["newest", "oldest"],
        default=DEFAULT_SCENE_ORDER,
        help="Prioriza as cenas mais recentes ou mais antigas antes do limite.",
    )
    parser.add_argument(
        "--min-valid-pixel-percentage",
        type=float,
        default=DEFAULT_MIN_VALID_PIXEL_PERCENTAGE,
        help="Percentual minimo de pixels validos para aceitar uma cena.",
    )
    parser.add_argument(
        "--include-low-quality-scenes",
        action="store_true",
        help="Inclui na serie cenas abaixo do percentual minimo, com marcacao low.",
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=DEFAULT_MIN_OBSERVATIONS,
        help="Quantidade minima de observacoes aceitas para uma serie suficiente.",
    )
    parser.add_argument(
        "--daily-aggregation",
        choices=["best", "median", "none"],
        default=DEFAULT_DAILY_AGGREGATION,
        help="Consolidacao de cenas aceitas do mesmo dia.",
    )
    parser.add_argument(
        "--decision-min-observations",
        type=int,
        default=DEFAULT_DECISION_MIN_OBSERVATIONS,
        help="Minimo de observacoes consolidadas para a recomendacao experimental.",
    )
    parser.add_argument(
        "--high-vegetation-percentile",
        type=float,
        default=DEFAULT_HIGH_VEGETATION_PERCENTILE,
        help="Percentil local minimo considerado nivel alto de vegetacao.",
    )
    parser.add_argument(
        "--significant-drop-absolute",
        type=float,
        default=DEFAULT_SIGNIFICANT_DROP_ABSOLUTE,
        help="Queda absoluta minima de NDVI considerada significativa.",
    )
    parser.add_argument(
        "--significant-drop-relative-percentage",
        type=float,
        default=DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE,
        help="Queda relativa minima considerada significativa, em percentual.",
    )
    parser.add_argument(
        "--trend-window",
        type=int,
        default=DEFAULT_TREND_WINDOW,
        help="Quantidade de observacoes recentes usada na tendencia linear.",
    )
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=DEFAULT_MAX_GAP_DAYS,
        help="Intervalo maximo aceitavel entre observacoes, em dias.",
    )
    parser.add_argument(
        "--recent-intervention-days",
        type=int,
        default=DEFAULT_RECENT_INTERVENTION_DAYS,
        help="Janela para considerar uma possivel intervencao recente.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/satellite_monitoring"),
        help="Diretorio raiz dos resultados.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _config_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> MonitoringConfig:
    try:
        return MonitoringConfig(
            latitude=args.latitude,
            longitude=args.longitude,
            radius_meters=args.radius_meters,
            start_date=args.start_date,
            end_date=args.end_date,
            geometry_file=args.geometry_file,
            max_cloud_cover=args.max_cloud_cover,
            max_scenes=args.max_scenes,
            scene_order=args.scene_order,
            min_valid_pixel_percentage=args.min_valid_pixel_percentage,
            include_low_quality_scenes=args.include_low_quality_scenes,
            min_observations=args.min_observations,
            daily_aggregation=args.daily_aggregation,
            decision_min_observations=args.decision_min_observations,
            high_vegetation_percentile=args.high_vegetation_percentile,
            significant_drop_absolute=args.significant_drop_absolute,
            significant_drop_relative_percentage=(
                args.significant_drop_relative_percentage
            ),
            trend_window=args.trend_window,
            max_gap_days=args.max_gap_days,
            recent_intervention_days=args.recent_intervention_days,
            output_root=args.output_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("argparse encerra a execucao em parser.error") from exc


def parse_config(argv: Sequence[str] | None = None) -> MonitoringConfig:
    """Interpreta a CLI e valida que exatamente um modo de geometria foi usado."""
    parser = build_parser()
    return _config_from_args(parser.parse_args(argv), parser)


def _new_scene_record(scene: Scene) -> dict[str, Any]:
    record = scene.to_record()
    record.update(
        {
            "valid_pixel_percentage": None,
            "valid_pixel_count": None,
            "total_pixel_count": None,
            "aoi_coverage_percentage": None,
            "partial_raster_coverage": None,
            "quality_status": "unknown",
            "quality_reasons": [],
            "accepted_for_timeseries": False,
            "processing_status": "processing",
            "error": None,
        }
    )
    return record


def _effective_date_range(ndvi_records: list[dict[str, Any]]) -> dict[str, str | None]:
    if not ndvi_records:
        return {"start": None, "end": None}
    dates = sorted(record["datetime"] for record in ndvi_records)
    return {"start": dates[0], "end": dates[-1]}


REASON_MESSAGES = {
    "recent_significant_drop_confirmed": "Queda recente significativa confirmada por observacao posterior.",
    "quality_acceptable_during_drop": "As observacoes envolvidas na queda possuem qualidade aceitavel.",
    "current_percentile_below_or_equal_50": "O NDVI atual esta abaixo ou no centro do historico local.",
    "stable_or_decreasing_recent_trend": "A tendencia recente esta estavel ou decrescente.",
    "current_percentile_at_or_above_high_threshold": "O NDVI atual esta no nivel alto do historico local.",
    "positive_or_stable_high_recent_trend": "A tendencia recente esta positiva ou estavel em nivel alto.",
    "no_recent_confirmed_significant_drop": "Nao ha queda significativa recente confirmada.",
    "insufficient_observations": "Ha poucas observacoes diarias para uma decisao.",
    "excessive_observation_gap": "Existe intervalo excessivo entre observacoes.",
    "no_recent_observation": "Nao existe observacao suficientemente recente.",
    "insufficient_valid_pixel_percentage": "Ha observacao com percentual insuficiente de pixels validos.",
    "partial_aoi_coverage": "Ha cobertura parcial relevante da area de interesse.",
    "unacceptable_observation_quality": "A qualidade das observacoes nao e suficiente.",
    "contradictory_series": "A serie recente apresenta movimentos contraditorios.",
    "insufficient_trend_data": "Nao ha dados suficientes para calcular a tendencia configurada.",
    "unconfirmed_possible_drop": "Uma possivel queda ainda nao possui observacao posterior de confirmacao.",
    "criteria_between_cut_and_no_cut": "Os indicadores ficaram entre cortar e nao cortar.",
}


def print_recommendation(result: RecommendationResult) -> None:
    """Exibe a decisao experimental e suas ressalvas sem depender de cores."""
    confidence = {"high": "ALTA", "medium": "MEDIA", "low": "BAIXA"}[result.confidence]
    print("=" * 40)
    print("RECOMENDACAO EXPERIMENTAL")
    print("Area: faixa lateral gramada")
    print(f"Decisao: {result.recommendation.upper()}")
    print(f"Confianca: {confidence}")
    print("=" * 40)
    print("\nMotivos:")
    codes = [*result.reasons, *result.blocking_reasons]
    for code in codes or ["criteria_between_cut_and_no_cut"]:
        print(f"- {REASON_MESSAGES.get(code, code)}")
    print("\nAviso:")
    print("O resultado utiliza indicadores espectrais e historicos.")
    print("O Sentinel-2 nao mede diretamente a altura da grama.")
    print("A recomendacao exige validacao de campo.")


def run(config: MonitoringConfig) -> int:
    """Executa o fluxo completo e retorna um codigo apropriado ao processo."""
    try:
        resolved_aoi = resolve_aoi(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Falha na geometria da area de interesse: {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc)
    run_directory = create_run_directory(config.output_root)
    aoi_geojson = resolved_aoi.geometry

    scene_records: list[dict[str, Any]] = []
    ndvi_records: list[dict[str, Any]] = []
    quality_messages: list[dict[str, str]] = []
    errors_by_scene: list[dict[str, str]] = []
    processed_item_ids: list[str] = []
    failed_item_ids: list[str] = []
    discarded_by_limit: list[dict[str, Any]] = []
    selected_scenes: list[Scene] = []
    total_matches = 0
    fatal_error: str | None = None

    print("Consultando cenas reais no Microsoft Planetary Computer...")
    try:
        search_result = search_scenes(config, aoi_geojson)
        total_matches = search_result.total_matches
        selected_scenes = search_result.scenes
        discarded_by_limit = [
            {
                "item_id": scene.item_id,
                "datetime": scene.datetime.isoformat(),
                "cloud_cover": scene.item.properties.get("eo:cloud_cover"),
                "reason": "max_scenes_limit",
            }
            for scene in search_result.discarded_scenes
        ]
        print(
            f"Cenas encontradas: {total_matches}; selecionadas: {len(selected_scenes)}; "
            f"ordem: {config.scene_order}."
        )

        for position, scene in enumerate(selected_scenes, start=1):
            print(f"[{position}/{len(selected_scenes)}] Processando {scene.item_id}...")
            scene_record = _new_scene_record(scene)

            try:
                raster_data = read_scene_bands(scene.item, aoi_geojson)
                statistics = None
                try:
                    _, statistics = analyze_ndvi(
                        raster_data.red,
                        raster_data.nir,
                        raster_data.valid_mask,
                        raster_data.total_pixel_count,
                    )
                except InsufficientValidPixelsError:
                    pass

                valid_pixel_count = statistics.valid_pixel_count if statistics else 0
                valid_pixel_percentage = statistics.valid_pixel_percentage if statistics else 0.0
                cloud_cover = scene_record["cloud_cover"]
                assessment = assess_scene_quality(
                    valid_pixel_percentage=valid_pixel_percentage,
                    min_valid_pixel_percentage=config.min_valid_pixel_percentage,
                    medium_threshold=config.medium_quality_threshold,
                    high_threshold=config.high_quality_threshold,
                    has_scl=raster_data.scl_asset is not None,
                    cloud_cover=cloud_cover,
                    max_cloud_cover=config.max_cloud_cover,
                    partial_raster_coverage=raster_data.partial_raster_coverage,
                    has_valid_ndvi_pixels=statistics is not None,
                    include_low_quality_scenes=config.include_low_quality_scenes,
                )
                scene_record.update(
                    {
                        "valid_pixel_percentage": valid_pixel_percentage,
                        "valid_pixel_count": valid_pixel_count,
                        "total_pixel_count": raster_data.total_pixel_count,
                        "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                        "partial_raster_coverage": raster_data.partial_raster_coverage,
                        "quality_status": assessment.quality_status,
                        "quality_reasons": list(assessment.quality_reasons),
                        "accepted_for_timeseries": assessment.accepted_for_timeseries,
                        "processing_status": "processed",
                    }
                )
                processed_item_ids.append(scene.item_id)

                for message in raster_data.quality_messages:
                    quality_messages.append({"item_id": scene.item_id, "message": message})

                if assessment.accepted_for_timeseries and statistics is not None:
                    ndvi_records.append(
                        {
                            "item_id": scene.item_id,
                            "datetime": scene_record["datetime"],
                            "cloud_cover": cloud_cover,
                            "platform": scene_record["platform"],
                            "tile": scene_record["tile"],
                            "red_asset": raster_data.red_asset,
                            "nir_asset": raster_data.nir_asset,
                            "scl_asset": raster_data.scl_asset,
                            "ndvi_mean": statistics.mean,
                            "ndvi_median": statistics.median,
                            "ndvi_std": statistics.std,
                            "ndvi_min": statistics.minimum,
                            "ndvi_max": statistics.maximum,
                            "valid_pixel_count": statistics.valid_pixel_count,
                            "total_pixel_count": raster_data.total_pixel_count,
                            "aoi_coverage_percentage": raster_data.aoi_coverage_percentage,
                            "partial_raster_coverage": raster_data.partial_raster_coverage,
                            "valid_pixel_percentage": statistics.valid_pixel_percentage,
                            "quality_status": assessment.quality_status,
                            "quality_reasons": list(assessment.quality_reasons),
                            "accepted_for_timeseries": True,
                        }
                    )
                    print(
                        f"  Aceita ({assessment.quality_status}): NDVI medio "
                        f"{statistics.mean:.4f}; pixels validos {valid_pixel_percentage:.1f}%."
                    )
                else:
                    reasons = ", ".join(assessment.quality_reasons) or "quality_policy"
                    print(f"  Rejeitada por qualidade: {reasons}.")

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
                errors_by_scene.append({"item_id": scene.item_id, "error": message})
                failed_item_ids.append(scene.item_id)
                print(f"  Falha no processamento: {message}", file=sys.stderr)

            scene_records.append(scene_record)

    except Exception as exc:
        fatal_error = str(exc)
        print(f"Falha impeditiva na consulta: {fatal_error}", file=sys.stderr)

    daily_result = aggregate_daily_observations(
        ndvi_records,
        strategy=config.daily_aggregation,
    )
    daily_records = daily_result.observations
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
    recommendation = recommend_cut(
        RecommendationInput(
            observations=daily_records,
            thresholds=thresholds,
            reference_date=config.end_date,
            min_valid_pixel_percentage=config.min_valid_pixel_percentage,
        )
    )

    quality_summary = summarize_scene_quality(scene_records)
    overall_status = determine_overall_status(
        fatal_error=fatal_error,
        processed_scene_count=len(processed_item_ids),
        accepted_scene_count=len(daily_records),
        min_observations=config.min_observations,
    )
    if overall_status == "insufficient_observations":
        print(
            "Aviso: observacoes aceitas insuficientes "
            f"({len(daily_records)}/{config.min_observations}). "
            "Nenhuma interpretacao de tendencia foi gerada.",
            file=sys.stderr,
        )

    finished_at = datetime.now(timezone.utc)
    summary = {
        "parameters": config.to_dict(),
        "endpoint": config.endpoint,
        "collection": config.collection,
        "started_at": started_at,
        "finished_at": finished_at,
        "overall_status": overall_status,
        "scene_order": config.scene_order,
        "quality_thresholds": config.quality_thresholds,
        "aoi": resolved_aoi.metadata,
        "scene_count_found": total_matches,
        "scene_count_selected": len(selected_scenes),
        "scene_count_not_selected_due_to_limit": len(discarded_by_limit),
        "scene_count_processed": len(processed_item_ids),
        "scene_count_ignored": len(failed_item_ids),
        "first_selected_datetime": selected_scenes[0].datetime.isoformat() if selected_scenes else None,
        "last_selected_datetime": selected_scenes[-1].datetime.isoformat() if selected_scenes else None,
        "date_range_effectively_processed": _effective_date_range(daily_records),
        "daily_aggregation": daily_result.audit,
        "daily_observation_count": len(daily_records),
        "recommendation_thresholds": config.recommendation_thresholds,
        "cut_recommendation": recommendation.to_summary_dict(),
        "scenes_discarded_by_limit": discarded_by_limit,
        "ignored_item_ids": failed_item_ids,
        "quality_messages": quality_messages,
        "errors_by_scene": errors_by_scene,
        "fatal_error": fatal_error,
        "processed_item_ids": processed_item_ids,
        "trend_interpretation": None,
        **quality_summary,
    }

    try:
        write_outputs(
            run_directory,
            scene_records,
            daily_records,
            summary,
            resolved_aoi.output_geojson,
            recommendation.to_dict(),
        )
    except Exception as exc:
        print(f"Falha ao salvar os resultados: {exc}", file=sys.stderr)
        return 1

    print(f"Resultados salvos em: {run_directory.resolve()}")
    print_recommendation(recommendation)
    if overall_status == "failed":
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_config(argv))


if __name__ == "__main__":
    raise SystemExit(main())
