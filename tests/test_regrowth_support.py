"""Testes do modelo de apoio por historico de rebrota."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.satellite_monitoring.database import (
    DEFAULT_HEIGHT_LIMIT_CM,
    STRICT_HEIGHT_LIMIT_CM,
    CrossSectionPosition,
    FieldConditionObservation,
    Highway,
    KmMarker,
    Segment,
    init_database,
    reset_engine,
    session_scope,
)
from src.satellite_monitoring.database.analytics import (
    build_transition_dataset,
    kilometre_context,
    survey_dates,
    worst_case_position,
)
from src.satellite_monitoring.decision_support import (
    build_decision_support,
    load_support_model,
)
from src.satellite_monitoring.models.regrowth_support import (
    MAX_FIELD_DATA_AGE_DAYS,
    agreement_with,
    classify_support,
    model_feature_vector,
    score_non_compliance,
    support_confidence,
)

PRIMEIRA = date(2026, 3, 13)
SEGUNDA = date(2026, 3, 20)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    reset_engine()
    init_database()
    load_support_model.cache_clear()
    yield
    reset_engine()
    load_support_model.cache_clear()


@pytest.fixture
def modelo_falso(tmp_path):
    """Artefato minimo: quanto maior a classe e menor o limite, maior o score."""

    caminho = tmp_path / "modelo.json"
    caminho.write_text(
        json.dumps(
            {
                "model_version": "regrowth-support-test",
                "features": ["current_height_class", "height_limit_cm"],
                "intercept": 0.0,
                "coefficients": [3.0, -3.0],
                "scaler_mean": [2.0, 20.0],
                "scaler_scale": [1.0, 10.0],
            }
        ),
        encoding="utf-8",
    )
    return caminho


def _seed(session, *, segunda_data: date = SEGUNDA) -> None:
    highway = Highway(code="SP-021", name="Rodoanel")
    session.add(highway)
    session.flush()
    session.add(
        KmMarker(highway_id=highway.id, km=5, longitude=-46.773, latitude=-23.441)
    )

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
    session.add_all([dispositivo, lateral])
    session.flush()

    # km 5: dispositivo com classe 2 (nao conforme para 10 cm) e lateral classe 1.
    plano = [
        (dispositivo, 5000, "1", "2"),
        (lateral, 5000, "3", "1"),
        (lateral, 5500, "1", "1"),
    ]
    for position, chainage, classe_a, classe_b in plano:
        segment = Segment(
            highway_id=highway.id,
            position_id=position.id,
            chainage_start_m=chainage,
            chainage_end_m=chainage + 500,
        )
        session.add(segment)
        session.flush()
        for observed_on, classe in ((PRIMEIRA, classe_a), (segunda_data, classe_b)):
            session.add(
                FieldConditionObservation(
                    segment_id=segment.id,
                    observed_on=observed_on,
                    height_class=classe,
                    applicable=classe != "X",
                )
            )


def test_dataset_de_transicoes_liga_levantamentos_consecutivos(database):
    with session_scope() as session:
        _seed(session)

    with session_scope() as session:
        assert survey_dates(session) == [PRIMEIRA, SEGUNDA]
        amostras = build_transition_dataset(session)

    assert len(amostras) == 3
    assert {amostra.elapsed_days for amostra in amostras} == {7}

    por_posicao = {(a.position_code, a.current_height_class): a for a in amostras}
    # Dispositivo: classe 2 sob limite de 10 cm e nao conformidade.
    dispositivo = por_posicao[("1.1", "1")]
    assert dispositivo.next_height_class == "2"
    assert dispositivo.non_compliant_next is True
    # Lateral: classe 1 sob limite de 30 cm esta conforme.
    lateral = por_posicao[("1.4", "3")]
    assert lateral.next_height_class == "1"
    assert lateral.non_compliant_next is False


def test_celulas_nao_aplicaveis_ficam_fora_do_treino(database):
    with session_scope() as session:
        highway = Highway(code="SP-021")
        position = CrossSectionPosition(code="1.5", description="PISTA EXTERNA")
        session.add_all([highway, position])
        session.flush()
        segment = Segment(
            highway_id=highway.id,
            position_id=position.id,
            chainage_start_m=0,
            chainage_end_m=500,
        )
        session.add(segment)
        session.flush()
        for observed_on in (PRIMEIRA, SEGUNDA):
            session.add(
                FieldConditionObservation(
                    segment_id=segment.id,
                    observed_on=observed_on,
                    height_class="X",
                    applicable=False,
                )
            )

    with session_scope() as session:
        assert build_transition_dataset(session) == []


def test_contexto_do_quilometro_resume_a_ultima_vistoria(database):
    with session_scope() as session:
        _seed(session)

    with session_scope() as session:
        contexto = kilometre_context(session, 5)

    assert contexto.last_survey_on == SEGUNDA
    assert contexto.applicable_count == 3
    # Apenas o dispositivo (classe 2 sob limite de 10 cm) descumpre.
    assert contexto.non_compliant_count == 1
    assert contexto.non_compliant_share == pytest.approx(1 / 3)

    critico = worst_case_position(contexto)
    assert critico is not None
    assert critico["position_code"] == "1.1"


def test_apoio_disponivel_quando_a_vistoria_e_recente(database, modelo_falso):
    with session_scope() as session:
        _seed(session)

    with session_scope() as session:
        apoio = build_decision_support(
            session,
            km=5,
            reference_date=SEGUNDA,
            decision="cortar",
            model_path=modelo_falso,
        )

    assert apoio.status == "available"
    assert apoio.reference_km == 5
    assert apoio.days_since_survey == 0
    assert apoio.suggestion in {"cortar", "nao_cortar", "inconclusivo"}
    assert apoio.score is not None
    assert apoio.context["non_compliant_count"] == 1
    assert any("Ponto mais critico" in fator for fator in apoio.factors)


def test_apoio_vira_descritivo_quando_o_dado_de_campo_envelhece(database, modelo_falso):
    with session_scope() as session:
        _seed(session)

    tarde = date(2026, 9, 3)
    with session_scope() as session:
        apoio = build_decision_support(
            session,
            km=5,
            reference_date=tarde,
            decision="cortar",
            model_path=modelo_falso,
        )

    assert apoio.status == "stale_field_data"
    assert apoio.suggestion is None
    assert apoio.score is None
    assert apoio.days_since_survey == (tarde - SEGUNDA).days
    assert apoio.days_since_survey > MAX_FIELD_DATA_AGE_DAYS
    # Mesmo sem sugerir, o contexto descritivo continua util.
    assert apoio.context["non_compliant_count"] == 1


def test_apoio_sem_historico_para_o_quilometro(database, modelo_falso):
    with session_scope() as session:
        _seed(session)

    with session_scope() as session:
        apoio = build_decision_support(
            session, km=27, reference_date=SEGUNDA, model_path=modelo_falso
        )

    assert apoio.status == "insufficient_history"
    assert apoio.suggestion is None


def test_apoio_sem_quilometro_associado(database, modelo_falso):
    with session_scope() as session:
        apoio = build_decision_support(
            session, km=None, reference_date=SEGUNDA, model_path=modelo_falso
        )

    assert apoio.status == "insufficient_history"
    assert apoio.reference_km is None


def test_modelo_ausente_nao_quebra_o_apoio(database, tmp_path):
    with session_scope() as session:
        _seed(session)

    with session_scope() as session:
        apoio = build_decision_support(
            session,
            km=5,
            reference_date=SEGUNDA,
            model_path=tmp_path / "inexistente.json",
        )

    assert apoio.status == "unavailable"
    assert apoio.suggestion is None


def test_score_cresce_com_a_classe_e_cai_com_o_limite(modelo_falso):
    modelo = json.loads(modelo_falso.read_text(encoding="utf-8"))

    baixo = score_non_compliance(
        model_feature_vector({"current_height_class": 1, "height_limit_cm": 30}), modelo
    )
    alto = score_non_compliance(
        model_feature_vector({"current_height_class": 3, "height_limit_cm": 10}), modelo
    )
    assert 0.0 <= baixo < alto <= 1.0


@pytest.mark.parametrize(
    ("score", "esperado"),
    [(0.05, "nao_cortar"), (0.5, "inconclusivo"), (0.95, "cortar")],
)
def test_classificacao_tem_faixa_de_abstencao(score, esperado):
    assert classify_support(score) == esperado


def test_confianca_so_sobe_nos_extremos():
    assert support_confidence(0.05) == "medium"
    assert support_confidence(0.5) == "low"
    assert support_confidence(0.95) == "medium"


@pytest.mark.parametrize(
    ("decisao", "sugestao", "esperado"),
    [
        ("cortar", "cortar", "concorda"),
        ("cortar", "nao_cortar", "diverge"),
        ("inconclusivo", "cortar", "indeterminado"),
        ("cortar", "inconclusivo", "indeterminado"),
        (None, "cortar", None),
        ("cortar", None, None),
    ],
)
def test_concordancia_com_a_decisao_do_satelite(decisao, sugestao, esperado):
    assert agreement_with(decisao, sugestao) == esperado


def test_score_invalido_e_rejeitado():
    with pytest.raises(ValueError):
        classify_support(1.5)
    with pytest.raises(ValueError):
        model_feature_vector({"current_height_class": float("nan"), "height_limit_cm": 30})
