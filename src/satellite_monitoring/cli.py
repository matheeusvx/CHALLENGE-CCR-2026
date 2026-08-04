"""Interface de linha de comando do monitoramento Sentinel-2 por NDVI."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from .config import MonitoringConfig, parse_iso_date
from .geometry import create_aoi_geojson
from .indices import analyze_ndvi
from .outputs import create_run_directory, write_outputs
from .raster_processing import read_scene_bands
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
    parser.add_argument("--latitude", type=float, required=True, help="Latitude da area em EPSG:4326.")
    parser.add_argument("--longitude", type=float, required=True, help="Longitude da area em EPSG:4326.")
    parser.add_argument("--radius-meters", type=float, required=True, help="Raio da area de interesse em metros.")
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
        default=10,
        help="Quantidade maxima de cenas processadas.",
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
            max_cloud_cover=args.max_cloud_cover,
            max_scenes=args.max_scenes,
            output_root=args.output_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("argparse encerra a execucao em parser.error") from exc


def run(config: MonitoringConfig) -> int:
    """Executa o fluxo completo e retorna um codigo apropriado ao processo."""
    started_at = datetime.now(timezone.utc)
    run_directory = create_run_directory(config.output_root)
    aoi_geojson = create_aoi_geojson(
        config.latitude,
        config.longitude,
        config.radius_meters,
    )

    scene_records: list[dict] = []
    ndvi_records: list[dict] = []
    quality_messages: list[dict[str, str]] = []
    errors_by_scene: list[dict[str, str]] = []
    processed_item_ids: list[str] = []
    ignored_item_ids: list[str] = []
    total_matches = 0
    selected_count = 0
    fatal_error: str | None = None

    print("Consultando cenas reais no Microsoft Planetary Computer...")
    try:
        search_result = search_scenes(config, aoi_geojson)
        total_matches = search_result.total_matches
        selected_count = len(search_result.scenes)
        print(f"Cenas encontradas: {total_matches}; selecionadas: {selected_count}.")

        for position, scene in enumerate(search_result.scenes, start=1):
            print(f"[{position}/{selected_count}] Processando {scene.item_id}...")
            scene_record = scene.to_record()
            scene_record.update({"status": "processing", "error": None})

            try:
                raster_data = read_scene_bands(scene.item, aoi_geojson)
                _, statistics = analyze_ndvi(
                    raster_data.red,
                    raster_data.nir,
                    raster_data.valid_mask,
                    raster_data.aoi_pixel_count,
                )

                for message in raster_data.quality_messages:
                    quality_messages.append({"item_id": scene.item_id, "message": message})

                metadata = scene.to_record()
                ndvi_records.append(
                    {
                        "item_id": scene.item_id,
                        "datetime": metadata["datetime"],
                        "cloud_cover": metadata["cloud_cover"],
                        "platform": metadata["platform"],
                        "tile": metadata["tile"],
                        "red_asset": raster_data.red_asset,
                        "nir_asset": raster_data.nir_asset,
                        "scl_asset": raster_data.scl_asset,
                        "ndvi_mean": statistics.mean,
                        "ndvi_median": statistics.median,
                        "ndvi_std": statistics.std,
                        "ndvi_min": statistics.minimum,
                        "ndvi_max": statistics.maximum,
                        "valid_pixel_count": statistics.valid_pixel_count,
                        "valid_pixel_percentage": statistics.valid_pixel_percentage,
                    }
                )
                scene_record["status"] = "processed"
                processed_item_ids.append(scene.item_id)
                print(
                    f"  NDVI medio: {statistics.mean:.4f}; "
                    f"pixels validos: {statistics.valid_pixel_count}."
                )
            except Exception as exc:  # Uma cena nao deve impedir as seguintes.
                message = str(exc)
                scene_record.update({"status": "failed", "error": message})
                errors_by_scene.append({"item_id": scene.item_id, "error": message})
                ignored_item_ids.append(scene.item_id)
                print(f"  Cena ignorada: {message}", file=sys.stderr)

            scene_records.append(scene_record)

    except Exception as exc:
        fatal_error = str(exc)
        print(f"Falha impeditiva na consulta: {fatal_error}", file=sys.stderr)

    finished_at = datetime.now(timezone.utc)
    summary = {
        "parameters": config.to_dict(),
        "endpoint": config.endpoint,
        "collection": config.collection,
        "started_at": started_at,
        "finished_at": finished_at,
        "scene_count_found": total_matches,
        "scene_count_selected": selected_count,
        "scene_count_not_selected_due_to_limit": max(total_matches - selected_count, 0),
        "scene_count_processed": len(processed_item_ids),
        "scene_count_ignored": len(ignored_item_ids),
        "ignored_item_ids": ignored_item_ids,
        "quality_messages": quality_messages,
        "errors_by_scene": errors_by_scene,
        "fatal_error": fatal_error,
        "processed_item_ids": processed_item_ids,
    }

    try:
        write_outputs(run_directory, scene_records, ndvi_records, summary)
    except Exception as exc:
        print(f"Falha ao salvar os resultados: {exc}", file=sys.stderr)
        return 1

    print(f"Resultados salvos em: {run_directory.resolve()}")
    if fatal_error is not None or not processed_item_ids:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args, parser)
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
