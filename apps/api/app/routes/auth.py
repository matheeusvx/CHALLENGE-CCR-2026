"""Login, logout and session discovery for the private Beta."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Response

from ..beta_auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    AuthenticatedAccount,
    BetaAuthConfig,
    authenticate_credentials,
    beta_auth_enabled,
    create_session_token,
    enforce_rate_limit,
    require_authenticated_account,
    require_trusted_proxy,
)
from ..beta_security import email_digest
from ..exceptions import ApiError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=1024)


class SessionResponse(BaseModel):
    scope: str
    label: str


def _session_response(account: AuthenticatedAccount) -> SessionResponse:
    return SessionResponse(scope=account.scope, label=account.label)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    response: Response,
    _trusted: None = Depends(require_trusted_proxy),
) -> SessionResponse:
    if not beta_auth_enabled():
        raise ApiError(
            "AUTH_DISABLED",
            "A autenticacao da Beta esta desativada.",
            status_code=503,
        )
    config = BetaAuthConfig.load()
    attempt_key = email_digest(payload.email, config.email_pepper)
    account = authenticate_credentials(payload.email, payload.password)
    if account is None:
        enforce_rate_limit("login-invalid", attempt_key, limit=5, window_seconds=15 * 60)
        raise ApiError(
            "INVALID_CREDENTIALS", "E-mail ou senha inválidos.", status_code=401
        )
    token = create_session_token(account)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("BETA_AUTH_COOKIE_SECURE", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        samesite="lax",
        path="/",
    )
    return _session_response(account)


@router.get("/me", response_model=SessionResponse)
def me(
    account: AuthenticatedAccount = Depends(require_authenticated_account),
) -> SessionResponse:
    return _session_response(account)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    _account: AuthenticatedAccount = Depends(require_authenticated_account),
) -> Response:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=os.getenv("BETA_AUTH_COOKIE_SECURE", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        samesite="lax",
        path="/",
    )
    response.status_code = 204
    return response
