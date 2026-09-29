"""Testes offline do endpoint de chat do GuIA."""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes import guia as guia_route


class _FakeGuiaAssistant:
    instances: list["_FakeGuiaAssistant"] = []

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.__class__.instances.append(self)

    def enviar_mensagem(self, message: str) -> str:
        self.messages.append(message)
        return f"resposta-{len(self.messages)}:{message}"


@pytest.fixture(autouse=True)
def mocked_guia(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Impede instancia ou chamada ao cliente Groq real em todos os testes."""
    guia_route._sessoes.clear()
    _FakeGuiaAssistant.instances.clear()
    monkeypatch.setattr(guia_route, "GuiaAssistant", _FakeGuiaAssistant)
    yield
    guia_route._sessoes.clear()


def test_chat_accepts_valid_message_with_mocked_assistant(client: TestClient) -> None:
    response = client.post(
        "/api/guia/chat",
        json={"mensagem": "Como faço uma análise?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resposta"] == "resposta-1:Como faço uma análise?"
    assert body["session_id"]
    assert len(_FakeGuiaAssistant.instances) == 1


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [("", 422), ("   ", 400)],
)
def test_chat_rejects_empty_message(
    client: TestClient, message: str, expected_status: int
) -> None:
    response = client.post("/api/guia/chat", json={"mensagem": message})

    assert response.status_code == expected_status
    assert _FakeGuiaAssistant.instances == []


def test_returned_session_id_can_be_reused(client: TestClient) -> None:
    first = client.post(
        "/api/guia/chat",
        json={"mensagem": "Primeira pergunta"},
    )
    session_id = first.json()["session_id"]

    second = client.post(
        "/api/guia/chat",
        json={"mensagem": "Segunda pergunta", "session_id": session_id},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {
        "resposta": "resposta-2:Segunda pergunta",
        "session_id": session_id,
    }
    assert len(_FakeGuiaAssistant.instances) == 1
    assert _FakeGuiaAssistant.instances[0].messages == [
        "Primeira pergunta",
        "Segunda pergunta",
    ]
