"""Persistencia tabular, JSON e grafica das execucoes do pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCENE_COLUMNS = [
    "item_id",
    "datetime",
    "cloud_cover",
    "platform",
    "tile",
    "available_assets",
    "global_cloud_cover",
    "valid_pixel_percentage",
    "local_valid_pixel_percentage",
    "local_invalid_pixel_percentage",
    "valid_pixel_count",
    "total_pixel_count",
    "aoi_coverage_percentage",
    "partial_raster_coverage",
    "has_scl",
    "scl_class_percentages",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "red_median_reflectance",
    "nir_median_reflectance",
    "reflectance_scale_source",
    "reflectance_scale",
    "reflectance_offset",
    "height_ndvi_median",
    "vegetation_fraction",
    "height_valid_pixel_count",
    "height_total_pixel_count",
    "mixed_pixel_risk",
    "height_purity_gate_passed",
    "height_purity_gate_reasons",
    "height_mask_configuration",
    "scene_quality_score",
    "min_pixel_requirement_met",
    "aoi_coverage_requirement_met",
    "scl_required_met",
    "quality_status",
    "quality_reasons",
    "accepted_for_timeseries",
    "selected_after_quality",
    "temporal_outlier_suspected",
    "included_in_analysis",
    "exclusion_reasons",
    "processing_status",
    "error",
]

TIMESERIES_COLUMNS = [
    "item_id",
    "datetime",
    "cloud_cover",
    "platform",
    "tile",
    "red_asset",
    "nir_asset",
    "scl_asset",
    "ndvi_mean",
    "ndvi_median",
    "ndvi_std",
    "red_median_reflectance",
    "nir_median_reflectance",
    "height_ndvi_median",
    "vegetation_fraction",
    "height_valid_pixel_count",
    "height_total_pixel_count",
    "mixed_pixel_risk",
    "height_purity_gate_passed",
    "height_purity_gate_reasons",
    "height_mask_configuration",
    "ndvi_min",
    "ndvi_max",
    "valid_pixel_count",
    "total_pixel_count",
    "valid_pixel_percentage",
    "local_valid_pixel_percentage",
    "local_invalid_pixel_percentage",
    "aoi_coverage_percentage",
    "partial_raster_coverage",
    "scene_quality_score",
    "min_pixel_requirement_met",
    "aoi_coverage_requirement_met",
    "scl_required_met",
    "scl_class_percentages",
    "quality_status",
    "quality_reasons",
    "accepted_for_timeseries",
    "daily_aggregation",
    "aggregation_scene_count",
    "aggregation_source_item_ids",
    "aggregation_selected_item_id",
    "aggregation_selection_reason",
    "difference_from_previous",
    "difference_to_next",
    "rolling_median_3",
    "deviation_from_rolling_median",
    "local_mad",
    "temporal_outlier_suspected",
    "included_in_analysis",
    "exclusion_reasons",
    "analysis_quality_status",
]

RECOMMENDATION_COLUMNS = [
    "recommendation",
    "confidence",
    "experimental",
    "area_type",
    "observation_count",
    "current_ndvi",
    "historical_median",
    "current_percentile",
    "recent_trend",
    "significant_drop_detected",
    "days_since_significant_drop",
    "reasons",
    "blocking_reasons",
]


def create_run_directory(output_root: str | Path, timestamp: datetime | None = None) -> Path:
    """Cria um diretorio exclusivo por execucao com nome baseado em data/hora."""
    output_root = Path(output_root)
    execution_time = timestamp or datetime.now()
    base_name = execution_time.strftime("%Y%m%d_%H%M%S")

    for suffix in range(1000):
        name = base_name if suffix == 0 else f"{base_name}_{suffix:02d}"
        run_directory = output_root / name
        try:
            run_directory.mkdir(parents=True, exist_ok=False)
            return run_directory
        except FileExistsError:
            continue
    raise FileExistsError("Nao foi possivel criar um diretorio exclusivo para a execucao.")


def to_json_compatible(value: Any) -> Any:
    """Converte recursivamente tipos NumPy e objetos comuns para JSON estrito."""
    if isinstance(value, dict):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_compatible(item) for item in value]
    if isinstance(value, np.ndarray):
        return [to_json_compatible(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return to_json_compatible(value.item())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _write_timeseries_plot(
    records: list[dict[str, Any]],
    output_path: Path,
    recommendation: dict[str, Any],
) -> None:
    # Um cache temporario evita falhas em perfis Windows sem permissao de escrita.
    matplotlib_cache = Path(tempfile.gettempdir()) / "satellite-monitoring-matplotlib"
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(9, 4.5))
    if records:
        frame = pd.DataFrame(records).sort_values("datetime")
        dates = pd.to_datetime(frame["datetime"], utc=True)
        regular_mask = frame["quality_status"] != "low"
        regular = frame.loc[regular_mask]
        regular_dates = dates.loc[regular_mask]
        low_quality = frame.loc[~regular_mask]
        low_quality_dates = dates.loc[~regular_mask]

        if not regular.empty:
            axis.plot(regular_dates, regular["ndvi_mean"], marker="o", label="Media aceita")
            axis.plot(
                regular_dates,
                regular["ndvi_median"],
                marker="s",
                label="Mediana aceita",
            )
        if not low_quality.empty:
            axis.scatter(
                low_quality_dates,
                low_quality["ndvi_mean"],
                marker="X",
                s=70,
                label="Media low (incluida)",
            )
            axis.scatter(
                low_quality_dates,
                low_quality["ndvi_median"],
                marker="P",
                s=60,
                label="Mediana low (incluida)",
            )
        if len(frame) == 1:
            axis.set_xlim(dates.iloc[0] - timedelta(days=1), dates.iloc[0] + timedelta(days=1))
        metrics = recommendation.get("metrics", {})
        historical_median = metrics.get("historical_median")
        if historical_median is not None:
            axis.axhline(
                historical_median,
                color="dimgray",
                linestyle="--",
                linewidth=1.2,
                label="Mediana historica",
            )
        drop_dates = set(metrics.get("significant_drop_dates") or [])
        drop_mask = dates.dt.date.astype(str).isin(drop_dates)
        if drop_mask.any():
            axis.scatter(
                dates.loc[drop_mask],
                frame.loc[drop_mask, "ndvi_mean"],
                marker="v",
                s=80,
                color="firebrick",
                label="Queda significativa",
                zorder=4,
            )
        axis.scatter(
            [dates.iloc[-1]],
            [frame.iloc[-1]["ndvi_mean"]],
            marker="*",
            s=130,
            color="black",
            label="Observacao atual",
            zorder=5,
        )
        axis.legend()
    else:
        axis.text(0.5, 0.5, "Nenhuma cena processada", ha="center", va="center")

    decision = str(recommendation.get("recommendation", "inconclusivo")).upper()
    axis.set_title(f"Serie diaria de NDVI | Recomendacao: {decision}")
    axis.set_xlabel("Data")
    axis.set_ylabel("NDVI")
    axis.set_ylim(-1.0, 1.0)
    axis.grid(alpha=0.3)
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)


def _csv_frame(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    """Cria um DataFrame cronologico e serializa listas de motivos de qualidade."""
    normalized: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        reasons = row.get("quality_reasons")
        if isinstance(reasons, (list, tuple, set)):
            row["quality_reasons"] = ";".join(str(reason) for reason in reasons)
        exclusion_reasons = row.get("exclusion_reasons")
        if isinstance(exclusion_reasons, (list, tuple, set)):
            row["exclusion_reasons"] = ";".join(
                str(reason) for reason in exclusion_reasons
            )
        scl_statistics = row.get("scl_class_percentages")
        if isinstance(scl_statistics, dict):
            row["scl_class_percentages"] = json.dumps(
                to_json_compatible(scl_statistics),
                ensure_ascii=False,
                sort_keys=True,
            )
        source_ids = row.get("aggregation_source_item_ids")
        if isinstance(source_ids, (list, tuple, set)):
            row["aggregation_source_item_ids"] = ";".join(
                str(item_id) for item_id in source_ids
            )
        normalized.append(row)

    frame = pd.DataFrame(normalized, columns=columns)
    if not frame.empty and "datetime" in frame:
        frame = frame.sort_values("datetime", kind="stable")
    return frame


def write_outputs(
    run_directory: str | Path,
    scene_records: list[dict[str, Any]],
    ndvi_records: list[dict[str, Any]],
    summary: dict[str, Any],
    aoi_geojson: dict[str, Any],
    recommendation: dict[str, Any],
    *,
    raw_daily_records: list[dict[str, Any]] | None = None,
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Salva os artefatos tabulares, espaciais, JSON e grafico da execucao."""
    run_directory = Path(run_directory)
    run_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "scenes": run_directory / "scenes.csv",
        "timeseries": run_directory / "ndvi_timeseries.csv",
        "raw_timeseries": run_directory / "raw_daily_timeseries.csv",
        "summary": run_directory / "summary.json",
        "quality_report": run_directory / "quality_report.json",
        "plot": run_directory / "ndvi_timeseries.png",
        "aoi": run_directory / "aoi.geojson",
        "recommendation_json": run_directory / "cut_recommendation.json",
        "recommendation_csv": run_directory / "cut_recommendation.csv",
    }

    _csv_frame(scene_records, SCENE_COLUMNS).to_csv(paths["scenes"], index=False)
    _csv_frame(ndvi_records, TIMESERIES_COLUMNS).to_csv(paths["timeseries"], index=False)
    _csv_frame(
        raw_daily_records if raw_daily_records is not None else ndvi_records,
        TIMESERIES_COLUMNS,
    ).to_csv(
        paths["raw_timeseries"], index=False
    )

    with paths["summary"].open("w", encoding="utf-8") as file:
        json.dump(
            to_json_compatible(summary),
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")

    with paths["quality_report"].open("w", encoding="utf-8") as file:
        json.dump(
            to_json_compatible(quality_report or {}),
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")

    with paths["aoi"].open("w", encoding="utf-8") as file:
        json.dump(
            to_json_compatible(aoi_geojson),
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")

    with paths["recommendation_json"].open("w", encoding="utf-8") as file:
        json.dump(
            to_json_compatible(recommendation),
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")

    metrics = recommendation.get("metrics", {})
    recommendation_row = {
        "recommendation": recommendation.get("recommendation"),
        "confidence": recommendation.get("confidence"),
        "experimental": recommendation.get("experimental"),
        "area_type": recommendation.get("area_type"),
        "observation_count": metrics.get("observation_count"),
        "current_ndvi": metrics.get("current_ndvi_mean"),
        "historical_median": metrics.get("historical_median"),
        "current_percentile": metrics.get("current_percentile"),
        "recent_trend": metrics.get("recent_trend"),
        "significant_drop_detected": metrics.get("significant_drop_detected"),
        "days_since_significant_drop": metrics.get("days_since_significant_drop"),
        "reasons": ";".join(recommendation.get("reasons") or []),
        "blocking_reasons": ";".join(recommendation.get("blocking_reasons") or []),
    }
    pd.DataFrame([recommendation_row], columns=RECOMMENDATION_COLUMNS).to_csv(
        paths["recommendation_csv"],
        index=False,
    )

    _write_timeseries_plot(ndvi_records, paths["plot"], recommendation)
    return paths
