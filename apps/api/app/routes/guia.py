"""Rota do assistente Guia: chat de apoio operacional."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.guia_assistant import GuiaAssistant

router = APIRouter(prefix="/api/guia", tags=["guia"])

# Uma instância de GuiaAssistant por sessão de conversa (histórico isolado).
# O registro é local ao processo, no mesmo espírito do registro de análises.
_sessoes: dict[str, GuiaAssistant] = {}


class ChatRequest(BaseModel):
    mensagem: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None)


class ChatResponse(BaseModel):
    resposta: str
    session_id: str


def _get_assistant(session_id: str | None) -> tuple[str, GuiaAssistant]:
    sid = session_id or uuid.uuid4().hex
    if sid not in _sessoes:
        _sessoes[sid] = GuiaAssistant()
    return sid, _sessoes[sid]


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    mensagem = payload.mensagem.strip()
    if not mensagem:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    try:
        sid, assistant = _get_assistant(payload.session_id)
        resposta = assistant.enviar_mensagem(mensagem)
    except ValueError as exc:
        # Ex.: chave da API ausente na configuração do servidor.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # não expõe detalhes internos ao cliente
        raise HTTPException(
            status_code=502,
            detail="O assistente não conseguiu responder agora. Tente de novo.",
        ) from exc

    return ChatResponse(resposta=resposta, session_id=sid)
