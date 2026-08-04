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
    "valid_pixel_percentage",
    "valid_pixel_count",
    "total_pixel_count",
    "quality_status",
    "quality_reasons",
    "accepted_for_timeseries",
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
    "ndvi_min",
    "ndvi_max",
    "valid_pixel_count",
    "total_pixel_count",
    "valid_pixel_percentage",
    "quality_status",
    "quality_reasons",
    "accepted_for_timeseries",
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


def _write_timeseries_plot(records: list[dict[str, Any]], output_path: Path) -> None:
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
        axis.legend()
    else:
        axis.text(0.5, 0.5, "Nenhuma cena processada", ha="center", va="center")

    axis.set_title("Serie temporal de NDVI")
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
) -> dict[str, Path]:
    """Salva os quatro artefatos previstos para uma execucao."""
    run_directory = Path(run_directory)
    run_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "scenes": run_directory / "scenes.csv",
        "timeseries": run_directory / "ndvi_timeseries.csv",
        "summary": run_directory / "summary.json",
        "plot": run_directory / "ndvi_timeseries.png",
    }

    _csv_frame(scene_records, SCENE_COLUMNS).to_csv(paths["scenes"], index=False)
    _csv_frame(ndvi_records, TIMESERIES_COLUMNS).to_csv(paths["timeseries"], index=False)

    with paths["summary"].open("w", encoding="utf-8") as file:
        json.dump(
            to_json_compatible(summary),
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        file.write("\n")

    _write_timeseries_plot(ndvi_records, paths["plot"])
    return paths
