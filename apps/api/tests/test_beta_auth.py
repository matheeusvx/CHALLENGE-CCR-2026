"""Security and account-scope integration tests for the private Beta."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.beta_auth import PROXY_HEADER, rate_limiter
from apps.api.app.beta_security import email_digest, hash_password
from apps.api.app.main import create_app
from apps.api.app.dependencies import get_analysis_service, get_automatic_analysis_coordinator
from apps.api.tests.conftest import make_result
from apps.api.tests.test_automatic_analysis import BOUNDS, CENTER, _coordinator
from src.satellite_monitoring.database import (
    Alert,
    Analysis,
    associate_analysis_scope,
    session_scope,
)

PROXY_SECRET = "test-proxy-secret-with-sufficient-entropy"
GROUP_EMAIL = "group@example.test"
MOTIVA_EMAIL = "motiva@example.test"
GROUP_PASSWORD = "group-password-for-tests"
MOTIVA_PASSWORD = "motiva-password-for-tests"


@pytest.fixture
def beta_env(monkeypatch: pytest.MonkeyPatch):
    pepper = "test-email-pepper-with-sufficient-entropy"
    values = {
        "BETA_AUTH_ENABLED": "true",
        "BETA_AUTH_COOKIE_SECURE": "false",
        "BETA_AUTH_EMAIL_PEPPER": pepper,
        "BETA_AUTH_TOKEN_SECRET": "test-token-secret-with-sufficient-entropy",
        "BETA_PROXY_SECRET": PROXY_SECRET,
        "BETA_GROUP_EMAIL_DIGEST": email_digest(GROUP_EMAIL, pepper),
        "BETA_GROUP_PASSWORD_HASH": hash_password(GROUP_PASSWORD),
        "BETA_MOTIVA_EMAIL_DIGEST": email_digest(MOTIVA_EMAIL, pepper),
        "BETA_MOTIVA_PASSWORD_HASH": hash_password(MOTIVA_PASSWORD),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    rate_limiter.clear()
    yield
    rate_limiter.clear()


def _client() -> TestClient:
    client = TestClient(create_app(), raise_server_exceptions=False)
    client.headers[PROXY_HEADER] = PROXY_SECRET
    return client


def _login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_both_accounts_login_and_cookie_is_http_only(beta_env) -> None:
    for email, password, scope, label in (
        (GROUP_EMAIL, GROUP_PASSWORD, "group", "Equipe do Projeto"),
        (MOTIVA_EMAIL, MOTIVA_PASSWORD, "motiva", "Equipe Motiva"),
    ):
        with _client() as client:
            response = _login(client, email.upper(), password)
            assert response.status_code == 200
            assert response.json() == {"scope": scope, "label": label}
            cookie = response.headers["set-cookie"]
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert client.get("/api/auth/me").json()["scope"] == scope


def test_invalid_login_is_generic_and_rate_limited(beta_env) -> None:
    with _client() as client:
        wrong_password = _login(client, GROUP_EMAIL, "incorrect")
        unknown_email = _login(client, "unknown@example.test", "incorrect")
        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json()["error"]["message"] == "E-mail ou senha inválidos."
        assert unknown_email.json()["error"]["message"] == "E-mail ou senha inválidos."

        rate_limiter.clear()
        statuses = [
            _login(client, GROUP_EMAIL, "incorrect").status_code for _ in range(6)
        ]
        assert statuses == [401, 401, 401, 401, 401, 429]


def test_proxy_session_logout_health_and_docs_protection(beta_env) -> None:
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as direct:
        assert direct.get("/api/health").status_code == 200
        assert direct.get("/docs").status_code == 404
        assert direct.get("/openapi.json").status_code == 404
        assert direct.get("/api/analyses").status_code == 403
        assert direct.get(
            "/api/analyses", headers={PROXY_HEADER: PROXY_SECRET}
        ).status_code == 401

    with _client() as client:
        assert _login(client, GROUP_EMAIL, GROUP_PASSWORD).status_code == 200
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401


def test_authenticated_scopes_isolate_history_alerts_and_legacy_rows(beta_env) -> None:
    shared_id = str(uuid4())
    legacy_id = str(uuid4())
    alert_id = str(uuid4())
    created_at = datetime(2026, 9, 1, 12)
    with session_scope() as session:
        shared = Analysis(
            id=shared_id,
            created_at=created_at,
            status="completed",
            decision="cortar",
            confidence="medium",
            experimental=True,
            payload={"analysis_id": shared_id, "status": "completed"},
        )
        legacy = Analysis(
            id=legacy_id,
            created_at=created_at,
            status="completed",
            experimental=True,
        )
        session.add_all([shared, legacy])
        session.flush()
        associate_analysis_scope(session, shared, "group", created_at=created_at)
        session.add(
            Alert(
                id=alert_id,
                operator_scope_id="group",
                type="CUT_PENDING",
                severity="high",
                status="new",
                subject_kind="geometry",
                subject_key=f"geometry:{shared_id}",
                analysis_id=shared_id,
                last_analysis_id=shared_id,
                current_recommendation="cortar",
                first_detected_at=created_at,
                last_seen_at=created_at,
                updated_at=created_at,
                open_key=f"group|open:{shared_id}",
                version=1,
                metadata_json={},
            )
        )

    with _client() as group, _client() as motiva:
        assert _login(group, GROUP_EMAIL, GROUP_PASSWORD).status_code == 200
        assert _login(motiva, MOTIVA_EMAIL, MOTIVA_PASSWORD).status_code == 200

        group_ids = {item["analysis_id"] for item in group.get("/api/analyses").json()["items"]}
        motiva_ids = {item["analysis_id"] for item in motiva.get("/api/analyses").json()["items"]}
        assert shared_id in group_ids and shared_id not in motiva_ids
        assert legacy_id not in group_ids | motiva_ids
        assert alert_id in {item["id"] for item in group.get("/api/alerts").json()["items"]}
        assert alert_id not in {item["id"] for item in motiva.get("/api/alerts").json()["items"]}
        assert motiva.patch(
            f"/api/alerts/{alert_id}", json={"status": "seen", "version": 1}
        ).status_code == 404

        with session_scope() as session:
            associate_analysis_scope(session, shared_id, "motiva", created_at=created_at)
        assert shared_id in {
            item["analysis_id"] for item in motiva.get("/api/analyses").json()["items"]
        }
        assert group.delete(f"/api/analyses/{shared_id}").status_code == 200
        assert shared_id in {
            item["analysis_id"] for item in motiva.get("/api/analyses").json()["items"]
        }


def test_authenticated_scopes_reuse_automatic_science_cache(beta_env, tmp_path) -> None:
    coordinator = _coordinator(tmp_path)
    executions = 0

    def service(*_, analysis_id: str, **__):
        nonlocal executions
        executions += 1
        return make_result(analysis_id)

    app = create_app()
    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: coordinator
    app.dependency_overrides[get_analysis_service] = lambda: service
    payload = {"bounds": BOUNDS, "center": CENTER, "zoom": 17}
    with TestClient(app, raise_server_exceptions=False) as group, TestClient(
        app, raise_server_exceptions=False
    ) as motiva:
        group.headers[PROXY_HEADER] = PROXY_SECRET
        motiva.headers[PROXY_HEADER] = PROXY_SECRET
        assert _login(group, GROUP_EMAIL, GROUP_PASSWORD).status_code == 200
        assert _login(motiva, MOTIVA_EMAIL, MOTIVA_PASSWORD).status_code == 200
        first = group.post("/api/analyses/automatic", json=payload)
        second = motiva.post("/api/analyses/automatic", json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["status"] == "analysis_started"
        assert second.json()["status"] == "cache_hit"
        assert second.json()["analysis_id"] == first.json()["analysis_id"]
        assert executions == 1
        analysis_id = first.json()["analysis_id"]
        assert analysis_id in {
            item["analysis_id"] for item in group.get("/api/analyses").json()["items"]
        }
        assert analysis_id in {
            item["analysis_id"] for item in motiva.get("/api/analyses").json()["items"]
        }
