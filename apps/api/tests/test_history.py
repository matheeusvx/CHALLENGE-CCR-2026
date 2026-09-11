"""Testes do historico persistido de analises."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.dependencies import analysis_registry, get_analysis_service
from apps.api.app.main import app
from apps.api.tests.conftest import VALID_GEOMETRY, make_result
from src.satellite_monitoring.database import session_scope
from src.satellite_monitoring.database.models import (
    Alert,
    AlertEvent,
    Analysis,
    MonitoredSection,
)


def _clear_history() -> None:
    with session_scope() as session:
        # Operational alerts intentionally retain FKs to their source analyses.
        session.query(AlertEvent).delete()
        session.query(Alert).delete()
        session.query(MonitoredSection).delete()
        session.query(Analysis).delete()


def _run(client: TestClient, valid_payload: dict) -> str:
    analysis_id = str(uuid4())

    def service(*_, analysis_id: str, **__):
        return make_result(analysis_id)

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json=valid_payload)
    assert response.status_code == 200
    return response.json()["analysis_id"]


def test_run_grava_a_analise_no_historico(
    client: TestClient, valid_payload: dict
) -> None:
    _clear_history()
    analysis_id = _run(client, valid_payload)

    response = client.get("/api/analyses")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 1
    assert body["offset"] == 0
    item = body["items"][0]
    assert item["analysis_id"] == analysis_id
    assert item["decision"] == "nao_cortar"
    assert item["confidence"] == "high"
    assert item["status"] == "completed"
    assert item["period_start"] == "2026-05-01"
    assert item["period_end"] == "2026-08-04"


def test_historico_devolve_a_analise_completa_com_geometria(
    client: TestClient, valid_payload: dict
) -> None:
    _clear_history()
    analysis_id = _run(client, valid_payload)

    response = client.get(f"/api/analyses/{analysis_id}")
    assert response.status_code == 200
    body = response.json()

    assert body["analysis_id"] == analysis_id
    assert body["geometry"] == VALID_GEOMETRY
    assert body["result"]["recommendation"]["decision"] == "nao_cortar"
    assert body["result"]["analysis_period"]["strategy"] == "explicit"
    assert body["created_at"]


def test_historico_filtra_por_decisao(client: TestClient, valid_payload: dict) -> None:
    _clear_history()
    _run(client, valid_payload)

    encontrados = client.get("/api/analyses", params={"decision": "nao_cortar"})
    assert encontrados.status_code == 200
    assert encontrados.json()["total"] == 1

    vazios = client.get("/api/analyses", params={"decision": "cortar"})
    assert vazios.status_code == 200
    assert vazios.json()["total"] == 0
    assert vazios.json()["items"] == []


def test_analise_inexistente_devolve_erro_estruturado(client: TestClient) -> None:
    response = client.get(f"/api/analyses/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"


def test_artefato_continua_acessivel_apos_perder_o_cache(
    client: TestClient, valid_payload: dict, tmp_path
) -> None:
    """A rota de artefatos recupera o caminho gravado no historico."""

    _clear_history()
    analysis_id = str(uuid4())
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    artifact = run_directory / "summary.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")

    def service(*_, analysis_id: str, **__):
        result = make_result(analysis_id, run_directory=run_directory)
        result.artifacts["summary"] = str(artifact)
        return result

    app.dependency_overrides[get_analysis_service] = lambda: service
    created = client.post("/api/analyses/run", json=valid_payload)
    assert created.status_code == 200
    analysis_id = created.json()["analysis_id"]

    # Simula o reinicio da API: o cache em memoria e descartado.
    analysis_registry._results.clear()

    response = client.get(f"/api/analyses/{analysis_id}/artifacts/summary")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_resposta_traz_apoio_do_historico_sem_alterar_a_decisao(
    client: TestClient, valid_payload: dict
) -> None:
    """O apoio e aditivo e degrada quando nao ha dado de campo para o trecho."""

    _clear_history()
    analysis_id = str(uuid4())

    def service(*_, analysis_id: str, **__):
        return make_result(analysis_id)

    app.dependency_overrides[get_analysis_service] = lambda: service
    response = client.post("/api/analyses/run", json=valid_payload)
    assert response.status_code == 200
    body = response.json()

    # A decisao do satelite permanece intacta.
    assert body["recommendation"]["decision"] == "nao_cortar"
    assert body["recommendation"]["confidence"] == "high"

    apoio = body["decision_support"]
    assert apoio["experimental"] is True
    # Sem dados de referencia carregados, o apoio se declara sem base.
    assert apoio["status"] == "insufficient_history"
    assert apoio["suggestion"] is None
    assert apoio["score"] is None


def test_apoio_fica_gravado_no_payload_do_historico(
    client: TestClient, valid_payload: dict
) -> None:
    """O payload guardado deve conter a mesma resposta que o operador viu."""

    _clear_history()
    analysis_id = _run(client, valid_payload)

    ao_vivo = client.post("/api/analyses/run", json=valid_payload).json()
    assert "decision_support" in ao_vivo

    detalhe = client.get(f"/api/analyses/{analysis_id}").json()
    # Regressao: o apoio era anexado depois de gravar e sumia ao reabrir.
    assert "decision_support" in detalhe["result"]
    assert detalhe["result"]["decision_support"]["status"] == (
        ao_vivo["decision_support"]["status"]
    )
