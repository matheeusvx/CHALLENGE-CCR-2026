"""Testes do alinhamento entre NDVI de satelite e condicao observada em campo."""

from __future__ import annotations

from datetime import date

import pytest

from scripts import build_ndvi_field_dataset as builder
from src.satellite_monitoring.database import (
    DEFAULT_HEIGHT_LIMIT_CM,
    STRICT_HEIGHT_LIMIT_CM,
    CrossSectionPosition,
    FieldConditionObservation,
    Highway,
    MowingPolygon,
    NdviFieldSample,
    Segment,
    init_database,
    reset_engine,
    session_scope,
)

PRIMEIRA = date(2026, 3, 13)
SEGUNDA = date(2026, 3, 20)

QUADRADO = {
    "type": "Polygon",
    "coordinates": [
        [
            [-46.740, -23.420],
            [-46.739, -23.420],
            [-46.739, -23.419],
            [-46.740, -23.419],
            [-46.740, -23.420],
        ]
    ],
}


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    reset_engine()
    init_database()
    yield
    reset_engine()


def _cena(item_id: str, datetime_iso: str, **overrides):
    registro = {
        "item_id": item_id,
        "datetime": datetime_iso,
        "ndvi_mean": 0.42,
        "ndvi_median": 0.41,
        "ndvi_std": 0.05,
        "ndvi_min": 0.10,
        "ndvi_max": 0.70,
        "valid_pixel_percentage": 92.0,
        "valid_pixel_count": 140,
        "aoi_coverage_percentage": 99.0,
        "scene_quality_score": 88.0,
        "quality_status": "high",
        "accepted_for_timeseries": True,
    }
    registro.update(overrides)
    return registro


def _seed(session, *, com_poligono: bool = True) -> None:
    highway = Highway(code="SP-021", name="Rodoanel")
    dispositivo = CrossSectionPosition(
        code="1.1",
        description="CANT. DISPOSITIVO EXT.",
        kind="dispositivo",
        height_limit_cm=STRICT_HEIGHT_LIMIT_CM,
    )
    lateral = CrossSectionPosition(
        code="1.4",
        description="CANT. LATERAL EXTERNA",
        kind="lateral",
        height_limit_cm=DEFAULT_HEIGHT_LIMIT_CM,
    )
    session.add_all([highway, dispositivo, lateral])
    session.flush()

    if com_poligono:
        session.add(
            MowingPolygon(
                highway_id=highway.id,
                km=0,
                method="Apenas manual",
                area_m2=1200.0,
                geometry=QUADRADO,
            )
        )

    # Dispositivo em classe 2 descumpre o limite de 10 cm; lateral em 1 cumpre.
    for position, classe in ((dispositivo, "2"), (lateral, "1")):
        segment = Segment(
            highway_id=highway.id,
            position_id=position.id,
            chainage_start_m=0,
            chainage_end_m=500,
        )
        session.add(segment)
        session.flush()
        session.add(
            FieldConditionObservation(
                segment_id=segment.id,
                observed_on=PRIMEIRA,
                height_class=classe,
                applicable=True,
            )
        )


def test_amostra_alinha_cena_mais_proxima_e_rotulo_do_campo(database, monkeypatch):
    with session_scope() as session:
        _seed(session)

    def falsa_consulta(geometry, start_date, end_date):
        return (
            [
                _cena("S2_LONGE", "2026-03-10T13:00:00Z", ndvi_mean=0.99),
                _cena("S2_PERTO", "2026-03-14T13:00:00Z", ndvi_mean=0.42),
            ],
            None,
        )

    monkeypatch.setattr(builder, "query_training_scenes", falsa_consulta)

    with session_scope() as session:
        amostra = builder.build_sample(session, km=0, observed_on=PRIMEIRA, alignment_days=3)
        assert amostra is not None
        session.add(amostra)

    with session_scope() as session:
        gravada = session.query(NdviFieldSample).one()

    assert gravada.km == 0
    assert gravada.observed_on == PRIMEIRA
    # Escolhe a cena de 14/03 (1 dia) e nao a de 10/03 (3 dias).
    assert gravada.scene_item_id == "S2_PERTO"
    assert gravada.alignment_days == 1
    assert gravada.ndvi_mean == pytest.approx(0.42)
    assert gravada.aoi_polygon_count == 1
    assert gravada.aoi_area_m2 == pytest.approx(1200.0)
    # Rotulo do km: apenas o dispositivo descumpre.
    assert gravada.applicable_count == 2
    assert gravada.non_compliant_count == 1
    assert gravada.non_compliant_share == pytest.approx(0.5)
    assert gravada.non_compliant_any is True
    assert gravada.worst_height_class == "2"


def test_cenas_reprovadas_na_qualidade_sao_descartadas(database, monkeypatch):
    with session_scope() as session:
        _seed(session)

    def so_reprovadas(geometry, start_date, end_date):
        return (
            [
                _cena("S2_RUIM", "2026-03-13T13:00:00Z", accepted_for_timeseries=False),
                _cena("S2_SEM_NDVI", "2026-03-13T13:00:00Z", ndvi_mean=None),
            ],
            None,
        )

    monkeypatch.setattr(builder, "query_training_scenes", so_reprovadas)

    with session_scope() as session:
        assert builder.build_sample(session, km=0, observed_on=PRIMEIRA, alignment_days=3) is None


def test_km_sem_poligono_nao_gera_amostra(database, monkeypatch):
    with session_scope() as session:
        _seed(session, com_poligono=False)

    monkeypatch.setattr(
        builder, "query_training_scenes", lambda *_: ([_cena("S2", "2026-03-13T13:00:00Z")], None)
    )

    with session_scope() as session:
        assert builder.build_sample(session, km=0, observed_on=PRIMEIRA, alignment_days=3) is None


def test_data_sem_observacao_de_campo_nao_gera_amostra(database, monkeypatch):
    with session_scope() as session:
        _seed(session)

    monkeypatch.setattr(
        builder, "query_training_scenes", lambda *_: ([_cena("S2", "2026-03-20T13:00:00Z")], None)
    )

    with session_scope() as session:
        # Nao ha unifilar registrado nesta data.
        assert builder.build_sample(session, km=0, observed_on=SEGUNDA, alignment_days=3) is None


def test_erro_da_consulta_de_cenas_e_propagado(database, monkeypatch):
    with session_scope() as session:
        _seed(session)

    monkeypatch.setattr(builder, "query_training_scenes", lambda *_: ([], "HTTPError: 503"))

    with session_scope() as session:
        with pytest.raises(RuntimeError, match="503"):
            builder.build_sample(session, km=0, observed_on=PRIMEIRA, alignment_days=3)


def test_janela_e_reduzida_para_nao_compartilhar_cena_entre_vistorias():
    # Vistorias a 7 dias: janela de 5 faria as duas disputarem a mesma cena.
    janela, aviso = builder.effective_alignment_days(5, [PRIMEIRA, SEGUNDA])
    assert janela == 3
    assert aviso is not None
    assert "reduzida" in aviso

    # Janela ja segura permanece intacta.
    janela, aviso = builder.effective_alignment_days(2, [PRIMEIRA, SEGUNDA])
    assert janela == 2
    assert aviso is None

    # Com um unico levantamento nao ha risco de sobreposicao.
    janela, aviso = builder.effective_alignment_days(9, [PRIMEIRA])
    assert janela == 9
    assert aviso is None


def test_selecao_desempata_pela_qualidade_da_cena():
    escolhida = builder.select_aligned_scene(
        [
            _cena("S2_A", "2026-03-14T13:00:00Z", scene_quality_score=70.0),
            _cena("S2_B", "2026-03-14T13:00:00Z", scene_quality_score=95.0),
        ],
        PRIMEIRA,
    )
    assert escolhida is not None
    assert escolhida["item_id"] == "S2_B"


def test_aoi_une_os_poligonos_do_quilometro(database):
    with session_scope() as session:
        _seed(session)
        highway_id = session.query(Highway).one().id
        session.add(
            MowingPolygon(
                highway_id=highway_id,
                km=0,
                method="Spider, com ancoragem",
                area_m2=800.0,
                geometry=QUADRADO,
            )
        )

    with session_scope() as session:
        aoi = builder.aoi_for_km(session, 0)

    assert aoi is not None
    assert aoi.polygon_count == 2
    assert aoi.area_m2 == pytest.approx(2000.0)
    assert not aoi.geometry.is_empty
