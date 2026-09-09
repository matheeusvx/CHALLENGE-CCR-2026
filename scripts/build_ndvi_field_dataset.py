"""Alinha a serie NDVI de satelite com a condicao observada em campo.

Para cada par (quilometro, data de levantamento) monta uma amostra de treino:

* a AOI e a uniao dos poligonos de rocada daquele quilometro, ou seja, area de
  vegetacao real informada pela concessionaria, e nao um buffer arbitrario;
* as features vem da cena Sentinel-2 valida mais proxima da data da vistoria,
  extraidas pelo mesmo caminho do pipeline operacional
  (`datasets.training_scenes.query_training_scenes`);
* o rotulo vem do unifilar da mesma data, agregado no quilometro.

**Resolucao quilometrica, e nao por posicao transversal.** Os poligonos trazem
metodo de rocada e km, mas nao a posicao na secao; sem o eixo da rodovia nao ha
como derivar essa associacao com seguranca. O proprio projeto ja registra essa
limitacao em `datasets/spatial_matching.py`, onde os poligonos candidatos sao
explicitamente tratados como candidatos e nunca como ground truth.

Uso::

    python -m scripts.build_ndvi_field_dataset --alignment-days 5
    python -m scripts.build_ndvi_field_dataset --km 0 1 2 --force

A execucao consulta o Microsoft Planetary Computer e le janelas dos COGs, entao
depende de rede e leva alguns segundos por quilometro. E incremental: pares ja
gravados sao ignorados, salvo com ``--force``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from shapely.geometry import shape
from shapely.ops import unary_union
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.satellite_monitoring.database import (
    MowingPolygon,
    NdviFieldSample,
    init_database,
    session_scope,
)
from src.satellite_monitoring.database.analytics import kilometre_context, survey_dates
from src.satellite_monitoring.datasets.training_scenes import query_training_scenes

DEFAULT_ALIGNMENT_DAYS = 5


@dataclass(frozen=True)
class AoiForKm:
    km: int
    geometry: Any
    polygon_count: int
    area_m2: float


def aoi_for_km(session: Session, km: int) -> AoiForKm | None:
    """Uniao dos poligonos de rocada do quilometro."""

    rows = session.execute(
        select(MowingPolygon.geometry, MowingPolygon.area_m2).where(
            MowingPolygon.km == km
        )
    ).all()
    geometrias = []
    area = 0.0
    for raw_geometry, polygon_area in rows:
        if not raw_geometry:
            continue
        try:
            geometria = shape(raw_geometry)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if geometria.is_empty:
            continue
        if not geometria.is_valid:
            geometria = geometria.buffer(0)
        if geometria.is_empty:
            continue
        geometrias.append(geometria)
        area += float(polygon_area or 0.0)

    if not geometrias:
        return None
    return AoiForKm(
        km=km,
        geometry=unary_union(geometrias),
        polygon_count=len(geometrias),
        area_m2=area,
    )


def _scene_date(record: Mapping[str, Any]) -> date | None:
    raw = record.get("datetime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def select_aligned_scene(
    records: Sequence[Mapping[str, Any]], observed_on: date
) -> Mapping[str, Any] | None:
    """Cena aceita mais proxima da vistoria, desempatando pela qualidade.

    Cenas rejeitadas pelos portoes de qualidade do pipeline sao descartadas: a
    amostra so faz sentido se o NDVI daquele dia seria aceito numa analise real.
    """

    candidatas = []
    for record in records:
        if not record.get("accepted_for_timeseries"):
            continue
        if record.get("ndvi_mean") is None:
            continue
        observada = _scene_date(record)
        if observada is None:
            continue
        candidatas.append((abs((observada - observed_on).days), record, observada))

    if not candidatas:
        return None
    distancia, melhor, observada = min(
        candidatas,
        key=lambda item: (item[0], -float(item[1].get("scene_quality_score") or 0.0)),
    )
    resultado = dict(melhor)
    resultado["_alignment_days"] = distancia
    resultado["_scene_date"] = observada.isoformat()
    return resultado


def effective_alignment_days(requested: int, dates: Sequence[date]) -> tuple[int, str | None]:
    """Impede que duas vistorias disputem a mesma cena.

    Com janelas sobrepostas, a mesma cena Sentinel-2 pode ficar mais proxima de
    dois levantamentos distintos e gerar linhas com features identicas e rotulos
    diferentes. A janela e limitada a menos da metade do menor intervalo entre
    vistorias para que isso nao ocorra.
    """

    if len(dates) < 2:
        return requested, None
    menor_intervalo = min(
        (posterior - anterior).days for anterior, posterior in zip(dates, dates[1:])
    )
    limite = max(1, (menor_intervalo - 1) // 2)
    if requested <= limite:
        return requested, None
    aviso = (
        f"janela reduzida de {requested} para {limite} dia(s): as vistorias estao "
        f"a {menor_intervalo} dia(s) e janelas maiores fariam duas datas "
        "compartilharem a mesma cena"
    )
    return limite, aviso


def build_sample(
    session: Session, km: int, observed_on: date, alignment_days: int
) -> NdviFieldSample | None:
    aoi = aoi_for_km(session, km)
    if aoi is None:
        return None

    contexto = kilometre_context(session, km, on=observed_on)
    if contexto.last_survey_on is None or contexto.applicable_count == 0:
        return None

    janela = timedelta(days=alignment_days)
    registros, erro = query_training_scenes(
        aoi.geometry, observed_on - janela, observed_on + janela
    )
    if erro:
        raise RuntimeError(f"km {km} em {observed_on}: {erro}")

    cena = select_aligned_scene(registros, observed_on)
    if cena is None:
        return None

    classes = [
        posicao["height_class"]
        for posicao in contexto.positions
        if posicao["height_class"].isdigit()
    ]
    pior = max(classes, default=None)
    share = contexto.non_compliant_share

    return NdviFieldSample(
        km=km,
        observed_on=observed_on,
        scene_item_id=str(cena.get("item_id") or "") or None,
        scene_datetime=str(cena.get("datetime") or "") or None,
        alignment_days=int(cena["_alignment_days"]),
        aoi_polygon_count=aoi.polygon_count,
        aoi_area_m2=aoi.area_m2,
        ndvi_mean=cena.get("ndvi_mean"),
        ndvi_median=cena.get("ndvi_median"),
        ndvi_std=cena.get("ndvi_std"),
        ndvi_min=cena.get("ndvi_min"),
        ndvi_max=cena.get("ndvi_max"),
        valid_pixel_percentage=cena.get("valid_pixel_percentage"),
        valid_pixel_count=cena.get("valid_pixel_count"),
        aoi_coverage_percentage=cena.get("aoi_coverage_percentage"),
        scene_quality_score=cena.get("scene_quality_score"),
        quality_status=cena.get("quality_status"),
        accepted_for_timeseries=bool(cena.get("accepted_for_timeseries")),
        worst_height_class=pior,
        applicable_count=contexto.applicable_count,
        non_compliant_count=contexto.non_compliant_count,
        non_compliant_share=None if share is None else round(share, 4),
        non_compliant_any=contexto.non_compliant_count > 0,
        created_at=datetime.now(),
    )


def existing_pairs(session: Session) -> set[tuple[int, date]]:
    rows = session.execute(
        select(NdviFieldSample.km, NdviFieldSample.observed_on)
    ).all()
    return {(int(km), observed_on) for km, observed_on in rows}


def build_dataset(
    *,
    alignment_days: int = DEFAULT_ALIGNMENT_DAYS,
    only_km: Sequence[int] | None = None,
    limit: int | None = None,
    force: bool = False,
    progress: bool = True,
) -> dict[str, int]:
    contadores = {"gravadas": 0, "sem_cena": 0, "sem_poligono": 0, "ja_existentes": 0, "erros": 0}

    init_database()
    with session_scope() as session:
        datas = survey_dates(session)
        kms = sorted(
            {
                int(km)
                for km in session.execute(select(MowingPolygon.km).distinct()).scalars()
                if km is not None
            }
        )
        if only_km:
            kms = [km for km in kms if km in set(only_km)]
        ja_gravados = set() if force else existing_pairs(session)

    alignment_days, aviso = effective_alignment_days(alignment_days, datas)
    if aviso and progress:
        print(f"  aviso: {aviso}")

    processadas = 0
    for observed_on in datas:
        for km in kms:
            if limit is not None and processadas >= limit:
                return contadores
            if (km, observed_on) in ja_gravados:
                contadores["ja_existentes"] += 1
                continue
            processadas += 1
            try:
                with session_scope() as session:
                    # A linha anterior sai antes da tentativa: se o rebuild nao
                    # produzir amostra, um resultado obsoleto nao pode sobreviver.
                    session.execute(
                        delete(NdviFieldSample)
                        .where(NdviFieldSample.km == km)
                        .where(NdviFieldSample.observed_on == observed_on)
                    )
                    amostra = build_sample(session, km, observed_on, alignment_days)
                    if amostra is None:
                        aoi = aoi_for_km(session, km)
                        chave = "sem_poligono" if aoi is None else "sem_cena"
                        contadores[chave] += 1
                        if progress:
                            print(f"  km {km:>2} {observed_on}: {chave}")
                        continue
                    session.add(amostra)
                    contadores["gravadas"] += 1
                    if progress:
                        print(
                            f"  km {km:>2} {observed_on}: NDVI "
                            f"{amostra.ndvi_mean:.3f} | cena a "
                            f"{amostra.alignment_days}d | nao conformes "
                            f"{amostra.non_compliant_count}/{amostra.applicable_count}"
                        )
            except Exception as exc:  # pragma: no cover - depende de rede
                contadores["erros"] += 1
                if progress:
                    print(f"  km {km:>2} {observed_on}: ERRO {type(exc).__name__}: {exc}")
    return contadores


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment-days", type=int, default=DEFAULT_ALIGNMENT_DAYS)
    parser.add_argument("--km", type=int, nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    if arguments.alignment_days <= 0:
        parser.error("--alignment-days deve ser positivo")

    print("Construindo dataset NDVI x campo...")
    contadores = build_dataset(
        alignment_days=arguments.alignment_days,
        only_km=arguments.km,
        limit=arguments.limit,
        force=arguments.force,
    )
    print()
    print("Resumo:")
    for nome in sorted(contadores):
        print(f"  {nome}: {contadores[nome]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
