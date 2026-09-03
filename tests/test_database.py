"""Testes da camada de persistencia do historico operacional."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.satellite_monitoring.database import (
    DEFAULT_HEIGHT_LIMIT_CM,
    STRICT_HEIGHT_LIMIT_CM,
    Analysis,
    CrossSectionPosition,
    FieldConditionObservation,
    Highway,
    KmMarker,
    Segment,
    count_analyses,
    delete_analysis,
    exceeds_height_limit,
    get_analysis,
    init_database,
    list_analyses,
    nearest_km,
    reset_engine,
    save_analysis,
    session_scope,
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    reset_engine()
    init_database()
    yield
    reset_engine()


def _payload(analysis_id: str = "11111111-1111-4111-8111-111111111111") -> dict:
    return {
        "analysis_id": analysis_id,
        "status": "completed",
        "recommendation": {
            "decision": "cortar",
            "confidence": "medium",
            "experimental": True,
            "summary": "Vegetacao em nivel alto.",
            "reasons": ["current_percentile_at_or_above_high_threshold"],
            "blocking_reasons": [],
            "limitations": [],
            "metrics": {
                "observation_count": 6,
                "current_ndvi_mean": 0.42,
                "current_percentile": 91.67,
                "recent_trend": 0.0021,
                "recent_trend_status": "positive",
            },
        },
        "analysis_period": {
            "start_date": "2026-07-10",
            "end_date": "2026-08-10",
            "timezone": "America/Sao_Paulo",
            "strategy": "previous_calendar_month",
        },
        "selected_area_m2": 12450.0,
        "effective_analysis_area_m2": 11800.2,
        "effective_analysis_pct": 94.78,
        "aoi": {
            "source": "geojson_inline",
            "centroid": {"longitude": -46.7368, "latitude": -23.4162},
        },
        "summary": {"analysis_quality": {"score": 92, "status": "high"}},
        "timeseries": [
            {"datetime": "2026-07-12T00:00:00Z", "ndvi_mean": 0.38, "ndvi_median": 0.37},
            {"datetime": "2026-08-01T00:00:00Z", "ndvi_mean": 0.42, "ndvi_median": 0.41},
        ],
        "scenes": [],
        "artifacts": {},
        "warnings": [],
        "errors": [],
    }


def test_save_analysis_registra_decisao_e_serie(database):
    with session_scope() as session:
        record = save_analysis(
            session,
            _payload(),
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
            run_directory="outputs/satellite_monitoring/20260810_120000",
            artifacts={"summary": "outputs/.../summary.json"},
            created_at=datetime(2026, 8, 10, 12, 0, 0),
        )
        assert record.decision == "cortar"
        assert record.confidence == "medium"

    with session_scope() as session:
        stored = get_analysis(session, "11111111-1111-4111-8111-111111111111")
        assert stored is not None
        assert stored.status == "completed"
        assert stored.period_start == date(2026, 7, 10)
        assert stored.period_end == date(2026, 8, 10)
        assert stored.observation_count == 6
        assert stored.current_percentile == pytest.approx(91.67)
        assert stored.recent_trend_status == "positive"
        assert stored.analysis_quality_status == "high"
        assert stored.selected_area_m2 == pytest.approx(12450.0)
        assert stored.geometry["type"] == "Polygon"
        assert stored.artifacts == {"summary": "outputs/.../summary.json"}
        assert [item.observed_on for item in stored.observations] == [
            date(2026, 7, 12),
            date(2026, 8, 1),
        ]
        assert stored.observations[1].ndvi_mean == pytest.approx(0.42)


def test_save_analysis_e_idempotente(database):
    for _ in range(2):
        with session_scope() as session:
            save_analysis(session, _payload())

    with session_scope() as session:
        assert count_analyses(session) == 1
        stored = get_analysis(session, "11111111-1111-4111-8111-111111111111")
        assert stored is not None
        assert len(stored.observations) == 2


def test_listagem_ordena_do_mais_recente_e_filtra_por_decisao(database):
    with session_scope() as session:
        primeiro = _payload("11111111-1111-4111-8111-111111111111")
        save_analysis(session, primeiro, created_at=datetime(2026, 8, 1, 10, 0))

        segundo = _payload("22222222-2222-4222-8222-222222222222")
        segundo["recommendation"]["decision"] = "nao_cortar"
        save_analysis(session, segundo, created_at=datetime(2026, 8, 5, 10, 0))

    with session_scope() as session:
        registros = list_analyses(session)
        assert [item.id for item in registros] == [
            "22222222-2222-4222-8222-222222222222",
            "11111111-1111-4111-8111-111111111111",
        ]
        assert count_analyses(session) == 2

        apenas_cortar = list_analyses(session, decision="cortar")
        assert [item.id for item in apenas_cortar] == [
            "11111111-1111-4111-8111-111111111111"
        ]
        assert count_analyses(session, decision="cortar") == 1


def test_remocao_do_historico(database):
    with session_scope() as session:
        save_analysis(session, _payload())

    with session_scope() as session:
        assert delete_analysis(session, "11111111-1111-4111-8111-111111111111") is True

    with session_scope() as session:
        assert count_analyses(session) == 0
        assert get_analysis(session, "11111111-1111-4111-8111-111111111111") is None


def test_nearest_km_usa_os_marcos_cadastrados(database):
    with session_scope() as session:
        highway = Highway(code="SP-021", name="Rodoanel")
        session.add(highway)
        session.flush()
        session.add_all(
            [
                KmMarker(highway_id=highway.id, km=0, longitude=-46.736768, latitude=-23.416207),
                KmMarker(highway_id=highway.id, km=10, longitude=-46.801891, latitude=-23.476402),
            ]
        )

    with session_scope() as session:
        assert nearest_km(session, -46.7370, -23.4165) == 0
        assert nearest_km(session, -46.8020, -23.4760) == 10
        assert nearest_km(session, None, None) is None


def test_analise_guarda_o_km_mais_proximo(database):
    with session_scope() as session:
        highway = Highway(code="SP-021", name="Rodoanel")
        session.add(highway)
        session.flush()
        session.add(
            KmMarker(highway_id=highway.id, km=0, longitude=-46.736768, latitude=-23.416207)
        )

    with session_scope() as session:
        save_analysis(session, _payload())

    with session_scope() as session:
        stored = get_analysis(session, "11111111-1111-4111-8111-111111111111")
        assert stored is not None
        assert stored.nearest_km == 0


@pytest.mark.parametrize(
    ("height_class", "limit", "esperado"),
    [
        ("1", DEFAULT_HEIGHT_LIMIT_CM, False),
        ("2", DEFAULT_HEIGHT_LIMIT_CM, False),
        ("3", DEFAULT_HEIGHT_LIMIT_CM, True),
        ("1", STRICT_HEIGHT_LIMIT_CM, False),
        ("2", STRICT_HEIGHT_LIMIT_CM, True),
        ("3", STRICT_HEIGHT_LIMIT_CM, True),
        ("X", DEFAULT_HEIGHT_LIMIT_CM, None),
    ],
)
def test_limite_contratual_depende_do_local(height_class, limit, esperado):
    assert exceeds_height_limit(height_class, limit) is esperado


def test_observacao_de_campo_usa_o_limite_da_posicao(database):
    with session_scope() as session:
        highway = Highway(code="SP-021", name="Rodoanel")
        dispositivo = CrossSectionPosition(
            code="1.1",
            description="CANT. DISPOSITIVO EXT.",
            side="externa",
            kind="dispositivo",
            height_limit_cm=STRICT_HEIGHT_LIMIT_CM,
        )
        lateral = CrossSectionPosition(
            code="1.4",
            description="CANT. LATERAL EXTERNA",
            side="externa",
            kind="lateral",
            height_limit_cm=DEFAULT_HEIGHT_LIMIT_CM,
        )
        session.add_all([highway, dispositivo, lateral])
        session.flush()

        for position in (dispositivo, lateral):
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
                    observed_on=date(2026, 3, 13),
                    height_class="2",
                    height_min_cm=10.0,
                    height_max_cm=30.0,
                )
            )

    with session_scope() as session:
        observacoes = {
            item.segment.position.code: item
            for item in session.query(FieldConditionObservation).all()
        }
        # A mesma classe 2 (10 a 30 cm) descumpre em dispositivo e cumpre na lateral.
        assert observacoes["1.1"].non_compliant is True
        assert observacoes["1.4"].non_compliant is False
        assert observacoes["1.1"].above_threshold is False


def test_payload_sem_identificador_e_rejeitado(database):
    with session_scope() as session:
        with pytest.raises(ValueError):
            save_analysis(session, {"status": "completed"})
