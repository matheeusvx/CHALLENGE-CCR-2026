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
    "status",
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
    "valid_pixel_percentage",
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
        axis.plot(dates, frame["ndvi_mean"], marker="o", label="Media")
        axis.plot(dates, frame["ndvi_median"], marker="s", label="Mediana")
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

    pd.DataFrame(scene_records, columns=SCENE_COLUMNS).to_csv(paths["scenes"], index=False)
    pd.DataFrame(ndvi_records, columns=TIMESERIES_COLUMNS).to_csv(paths["timeseries"], index=False)

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
