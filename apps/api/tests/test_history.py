"""Testes do historico persistido de analises."""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from apps.api.app.dependencies import (
    analysis_registry,
    get_analysis_service,
    get_manual_road_geometry_provider,
)
from apps.api.app.main import app
from apps.api.tests.conftest import VALID_GEOMETRY, make_result
from src.satellite_monitoring.database import session_scope
from src.satellite_monitoring.database.models import (
    Alert,
    AlertEvent,
    Analysis,
    MonitoredSection,
)
from src.satellite_monitoring.road_geometry import LocalGeoJsonRoadGeometryProvider


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


def _manual_road_fixture(tmp_path):
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32723", always_xy=True)
    reverse = Transformer.from_crs("EPSG:32723", "EPSG:4326", always_xy=True)
    origin_x, origin_y = forward.transform(-47.0, -23.0)
    road_line = transform(
        reverse.transform,
        LineString([(origin_x - 500, origin_y), (origin_x + 500, origin_y)]),
    )
    center_lng, center_lat = reverse.transform(origin_x, origin_y + 8)
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "feature_id": "test:SP-348:axis-1",
                    "road_id": "SP-348",
                    "road_ref": "SP-348",
                    "road_name": "Rodovia dos Bandeirantes",
                    "geometry_status": "valid",
                    "source": "TEST",
                },
                "geometry": mapping(road_line),
            }
        ],
    }
    path = tmp_path / "manual-roads.geojson"
    path.write_text(json.dumps(document), encoding="utf-8")
    delta = 0.00005
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [center_lng - delta, center_lat - delta],
            [center_lng + delta, center_lat - delta],
            [center_lng + delta, center_lat + delta],
            [center_lng - delta, center_lat + delta],
            [center_lng - delta, center_lat - delta],
        ]],
    }
    return LocalGeoJsonRoadGeometryProvider(path), geometry


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
    assert item["analysis_trigger"] == "manual"
    assert item["road_ref"] == "SP-330"
    assert item["road_name"]


def test_manual_com_contexto_rodoviario_persiste_badge_sem_mudar_identidade(
    client: TestClient, valid_payload: dict, tmp_path
) -> None:
    _clear_history()
    provider, geometry = _manual_road_fixture(tmp_path)
    app.dependency_overrides[get_manual_road_geometry_provider] = lambda: provider
    valid_payload["geometry"] = geometry

    analysis_id = _run(client, valid_payload)

    item = client.get("/api/analyses").json()["items"][0]
    assert item["analysis_id"] == analysis_id
    assert item["road_ref"] == "SP-348"
    assert item["road_name"] == "Rodovia dos Bandeirantes"
    with session_scope() as session:
        persisted = session.get(Analysis, analysis_id)
        assert persisted is not None
        assert persisted.subject_kind == "geometry"
        assert persisted.subject_key.startswith("geometry:v1:")
        assert persisted.road_id == "SP-348"
        assert persisted.axis_id == "test:SP-348:axis-1"
        assert persisted.section_id is not None


def test_manual_sem_correspondencia_confiavel_continua_sem_rodovia(
    client: TestClient, valid_payload: dict, tmp_path
) -> None:
    _clear_history()
    path = tmp_path / "empty-roads.geojson"
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )
    provider = LocalGeoJsonRoadGeometryProvider(path)
    app.dependency_overrides[get_manual_road_geometry_provider] = lambda: provider

    analysis_id = _run(client, valid_payload)

    item = client.get("/api/analyses").json()["items"][0]
    assert item["analysis_id"] == analysis_id
    assert item["road_ref"] is None
    assert item["road_name"] is None
    with session_scope() as session:
        persisted = session.get(Analysis, analysis_id)
        assert persisted is not None
        assert persisted.subject_kind == "geometry"


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


def test_exclusao_individual_oculta_sem_apagar_analise_ou_fk(
    client: TestClient, valid_payload: dict
) -> None:
    _clear_history()
    analysis_id = _run(client, valid_payload)
    with session_scope() as session:
        analysis = session.get(Analysis, analysis_id)
        assert analysis is not None
        alert = Alert(
            id=str(uuid4()), type="CUT_PENDING", severity="medium", status="new",
            subject_kind=analysis.subject_kind or "geometry",
            subject_key=analysis.subject_key or "test-subject",
            analysis_id=analysis_id,
            first_detected_at=analysis.created_at,
            last_seen_at=analysis.created_at,
            updated_at=analysis.created_at,
            open_key=f"test:{analysis_id}", metadata_json={},
        )
        session.add(alert)
        alert_id = alert.id

    hidden = client.delete(f"/api/analyses/{analysis_id}")
    assert hidden.status_code == 200
    assert hidden.json() == {"analysis_id": analysis_id, "hidden": True}
    assert client.delete(f"/api/analyses/{analysis_id}").status_code == 200
    assert client.get("/api/analyses").json()["total"] == 0
    assert client.get(f"/api/analyses/{analysis_id}").status_code == 200

    with session_scope() as session:
        analysis = session.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.hidden_from_history_at is not None
        assert session.get(Alert, alert_id) is not None

    future_id = _run(client, valid_payload)
    visible = client.get("/api/analyses").json()
    assert visible["total"] == 1
    assert visible["items"][0]["analysis_id"] == future_id


def test_exclusao_individual_inexistente_retorna_404(client: TestClient) -> None:
    response = client.delete(f"/api/analyses/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ANALYSIS_NOT_FOUND"


def test_limpar_historico_oculta_somente_visiveis_e_preserva_alertas(
    client: TestClient, valid_payload: dict
) -> None:
    _clear_history()
    first_id = _run(client, valid_payload)
    second_id = _run(client, valid_payload)
    with session_scope() as session:
        first = session.get(Analysis, first_id)
        assert first is not None
        session.add(Alert(
            id=str(uuid4()), type="CUT_PENDING", severity="medium", status="new",
            subject_kind=first.subject_kind or "geometry",
            subject_key=first.subject_key or "test-subject",
            analysis_id=first_id,
            first_detected_at=first.created_at,
            last_seen_at=first.created_at,
            updated_at=first.created_at,
            open_key=f"clear-test:{first_id}", metadata_json={},
        ))

    cleared = client.delete("/api/analyses")
    assert cleared.status_code == 200
    assert cleared.json() == {"hidden_count": 2}
    assert client.delete("/api/analyses").json() == {"hidden_count": 0}
    assert client.get("/api/analyses").json()["total"] == 0
    with session_scope() as session:
        assert session.query(Analysis).count() == 2
        assert session.query(Alert).count() == 1
        assert session.query(AlertEvent).count() >= 0
        assert session.query(MonitoredSection).count() >= 1
        assert all(
            item.hidden_from_history_at is not None
            for item in session.query(Analysis).all()
        )


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
