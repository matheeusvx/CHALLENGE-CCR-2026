"""Interface de linha de comando do monitoramento Sentinel-2 por NDVI."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from .config import (
    DEFAULT_DAILY_AGGREGATION,
    DEFAULT_DECISION_MIN_OBSERVATIONS,
    DEFAULT_HIGH_VEGETATION_PERCENTILE,
    DEFAULT_MAX_GAP_DAYS,
    DEFAULT_MAX_SCENES,
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
from .cut_recommendation import RecommendationResult
from .raster_processing import read_scene_bands
from .service import (
    InvalidAnalysisGeometryError,
    PipelineDependencies,
    ProgressEvent,
    run_monitoring_analysis,
)
from .stac_client import search_scenes


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
    parser.add_argument("--max-cloud-cover", type=float, default=20.0, help="Cobertura global maxima de nuvens, entre 0 e 100.")
    parser.add_argument("--max-scenes", type=int, default=DEFAULT_MAX_SCENES, help="Quantidade maxima de cenas processadas.")
    parser.add_argument("--scene-order", choices=["newest", "oldest"], default=DEFAULT_SCENE_ORDER, help="Prioriza cenas recentes ou antigas antes do limite.")
    parser.add_argument("--min-valid-pixel-percentage", type=float, default=DEFAULT_MIN_VALID_PIXEL_PERCENTAGE, help="Percentual minimo de pixels validos.")
    parser.add_argument("--include-low-quality-scenes", action="store_true", help="Inclui cenas abaixo do limite com marcacao low.")
    parser.add_argument("--min-observations", type=int, default=DEFAULT_MIN_OBSERVATIONS, help="Minimo de observacoes aceitas para uma serie suficiente.")
    parser.add_argument("--daily-aggregation", choices=["best", "median", "none"], default=DEFAULT_DAILY_AGGREGATION, help="Consolidacao de cenas aceitas do mesmo dia.")
    parser.add_argument("--decision-min-observations", type=int, default=DEFAULT_DECISION_MIN_OBSERVATIONS, help="Minimo de observacoes para a recomendacao.")
    parser.add_argument("--high-vegetation-percentile", type=float, default=DEFAULT_HIGH_VEGETATION_PERCENTILE, help="Percentil local minimo considerado alto.")
    parser.add_argument("--significant-drop-absolute", type=float, default=DEFAULT_SIGNIFICANT_DROP_ABSOLUTE, help="Queda absoluta minima de NDVI.")
    parser.add_argument("--significant-drop-relative-percentage", type=float, default=DEFAULT_SIGNIFICANT_DROP_RELATIVE_PERCENTAGE, help="Queda relativa minima em percentual.")
    parser.add_argument("--trend-window", type=int, default=DEFAULT_TREND_WINDOW, help="Observacoes recentes usadas na tendencia.")
    parser.add_argument("--max-gap-days", type=int, default=DEFAULT_MAX_GAP_DAYS, help="Intervalo maximo entre observacoes.")
    parser.add_argument("--recent-intervention-days", type=int, default=DEFAULT_RECENT_INTERVENTION_DAYS, help="Janela de possivel intervencao recente.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/satellite_monitoring"), help="Diretorio raiz dos resultados.")
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
            significant_drop_relative_percentage=args.significant_drop_relative_percentage,
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
    "unconfirmed_possible_drop": "Uma possivel queda ainda nao possui confirmacao posterior.",
    "criteria_between_cut_and_no_cut": "Os indicadores ficaram entre cortar e nao cortar.",
}


def print_recommendation(result: RecommendationResult | dict[str, object]) -> None:
    """Exibe a decisao experimental e suas ressalvas sem depender de cores."""
    if isinstance(result, RecommendationResult):
        recommendation = result.recommendation
        confidence_value = result.confidence
        reasons = [*result.reasons, *result.blocking_reasons]
    else:
        recommendation = str(result.get("recommendation", "inconclusivo"))
        confidence_value = str(result.get("confidence", "low"))
        reasons = [*(result.get("reasons") or []), *(result.get("blocking_reasons") or [])]
    confidence = {"high": "ALTA", "medium": "MEDIA", "low": "BAIXA"}[confidence_value]
    print("=" * 40)
    print("RECOMENDACAO EXPERIMENTAL")
    print("Area: faixa lateral gramada")
    print(f"Decisao: {recommendation.upper()}")
    print(f"Confianca: {confidence}")
    print("=" * 40)
    print("\nMotivos:")
    for code in reasons or ["criteria_between_cut_and_no_cut"]:
        print(f"- {REASON_MESSAGES.get(str(code), str(code))}")
    print("\nAviso:")
    print("O resultado utiliza indicadores espectrais e historicos.")
    print("O Sentinel-2 nao mede diretamente a altura da grama.")
    print("A recomendacao exige validacao de campo.")


def _print_progress(event: ProgressEvent) -> None:
    if event.kind == "scene_started" and event.current is not None:
        print(f"[{event.current}/{event.total}] {event.message}")
    elif event.kind in {"search_started", "search_completed"}:
        print(event.message)


def run(config: MonitoringConfig) -> int:
    """Executa o servico e traduz o resultado para a experiencia da CLI."""
    try:
        result = run_monitoring_analysis(
            config,
            dependencies=PipelineDependencies(
                search_scenes=search_scenes,
                read_scene_bands=read_scene_bands,
            ),
            on_progress=_print_progress,
        )
    except InvalidAnalysisGeometryError as exc:
        print(f"Falha na geometria da area de interesse: {exc}", file=sys.stderr)
        return 2

    for error in result.errors:
        print(f"Falha: {error.get('message', error)}", file=sys.stderr)
    for warning in result.warnings:
        if warning.get("code") == "INSUFFICIENT_OBSERVATIONS":
            print(f"Aviso: {warning['message']}", file=sys.stderr)
    if result.run_directory is not None:
        print(f"Resultados salvos em: {result.run_directory.resolve()}")
    print_recommendation(result.recommendation)
    return result.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_config(argv))


if __name__ == "__main__":
    raise SystemExit(main())
