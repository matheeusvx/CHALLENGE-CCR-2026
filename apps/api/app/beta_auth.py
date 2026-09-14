"""Authentication boundary for the two-account private Beta."""

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import RLock

from fastapi import Header, Request

from .beta_security import email_digest, issue_token, verify_password, verify_token
from .exceptions import ApiError

SESSION_COOKIE = "motiva_beta_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PROXY_HEADER = "X-Beta-Proxy-Secret"


def beta_auth_enabled() -> bool:
    return os.getenv("BETA_AUTH_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class AuthenticatedAccount:
    scope: str
    label: str


@dataclass(frozen=True)
class BetaAuthConfig:
    email_pepper: str
    token_secret: str
    proxy_secret: str
    group_email_digest: str
    group_password_hash: str
    motiva_email_digest: str
    motiva_password_hash: str

    @classmethod
    def load(cls) -> "BetaAuthConfig":
        names = {
            "email_pepper": "BETA_AUTH_EMAIL_PEPPER",
            "token_secret": "BETA_AUTH_TOKEN_SECRET",
            "proxy_secret": "BETA_PROXY_SECRET",
            "group_email_digest": "BETA_GROUP_EMAIL_DIGEST",
            "group_password_hash": "BETA_GROUP_PASSWORD_HASH",
            "motiva_email_digest": "BETA_MOTIVA_EMAIL_DIGEST",
            "motiva_password_hash": "BETA_MOTIVA_PASSWORD_HASH",
        }
        values = {field: os.getenv(name, "").strip() for field, name in names.items()}
        if any(not value for value in values.values()):
            raise ApiError(
                "BETA_AUTH_NOT_CONFIGURED",
                "A autenticacao da Beta nao esta configurada.",
                status_code=503,
            )
        secret_names = ("email_pepper", "token_secret", "proxy_secret")
        if any(len(values[name]) < 32 for name in secret_names):
            raise ApiError(
                "BETA_AUTH_NOT_CONFIGURED",
                "A autenticacao da Beta nao esta configurada.",
                status_code=503,
            )
        if values["group_email_digest"] == values["motiva_email_digest"]:
            raise ApiError(
                "BETA_AUTH_NOT_CONFIGURED",
                "A autenticacao da Beta nao esta configurada.",
                status_code=503,
            )
        return cls(**values)

    def credentials(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("group", self.group_email_digest, self.group_password_hash),
            ("motiva", self.motiva_email_digest, self.motiva_password_hash),
        )


LABELS = {"group": "Equipe do Projeto", "motiva": "Equipe Motiva"}


def require_trusted_proxy(
    x_beta_proxy_secret: str | None = Header(default=None, alias=PROXY_HEADER),
) -> None:
    if not beta_auth_enabled():
        return
    expected = BetaAuthConfig.load().proxy_secret
    if not x_beta_proxy_secret or not hmac.compare_digest(
        x_beta_proxy_secret, expected
    ):
        raise ApiError("UNTRUSTED_PROXY", "Acesso nao autorizado.", status_code=403)


def require_authenticated_account(
    request: Request,
    x_beta_proxy_secret: str | None = Header(default=None, alias=PROXY_HEADER),
) -> AuthenticatedAccount:
    require_trusted_proxy(x_beta_proxy_secret)
    if not beta_auth_enabled():
        raise ApiError(
            "AUTH_DISABLED",
            "A autenticacao da Beta esta desativada.",
            status_code=503,
        )
    config = BetaAuthConfig.load()
    token = request.cookies.get(SESSION_COOKIE)
    scope = verify_token(token or "", config.token_secret)
    if scope is None:
        raise ApiError("UNAUTHENTICATED", "Sessao invalida ou expirada.", status_code=401)
    return AuthenticatedAccount(scope=scope, label=LABELS[scope])


def authenticate_credentials(email: str, password: str) -> AuthenticatedAccount | None:
    config = BetaAuthConfig.load()
    candidate = email_digest(email, config.email_pepper)
    matched: AuthenticatedAccount | None = None
    # Verify one password hash regardless of whether the e-mail exists, reducing
    # the usefulness of response timing for account enumeration.
    for scope, configured_digest, password_hash in config.credentials():
        if hmac.compare_digest(candidate, configured_digest):
            matched = AuthenticatedAccount(scope=scope, label=LABELS[scope])
            selected_hash = password_hash
            break
    else:
        selected_hash = config.group_password_hash
    if not verify_password(password, selected_hash):
        return None
    return matched


def create_session_token(account: AuthenticatedAccount) -> str:
    return issue_token(
        account.scope,
        BetaAuthConfig.load().token_secret,
        ttl=SESSION_TTL_SECONDS,
    )


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def consume(
        self, category: str, key: str, *, limit: int, window_seconds: int
    ) -> bool:
        now = time.monotonic()
        identity = (category, key)
        with self._lock:
            events = self._events[identity]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def allows(
        self, category: str, key: str, *, limit: int, window_seconds: int
    ) -> bool:
        now = time.monotonic()
        identity = (category, key)
        with self._lock:
            events = self._events[identity]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            return len(events) < limit

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowLimiter()


def enforce_rate_limit(
    category: str,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    record: bool = True,
) -> None:
    if not beta_auth_enabled():
        return
    allowed = (
        rate_limiter.consume(
            category, scope, limit=limit, window_seconds=window_seconds
        )
        if record
        else rate_limiter.allows(
            category, scope, limit=limit, window_seconds=window_seconds
        )
    )
    if not allowed:
        raise ApiError("RATE_LIMIT_EXCEEDED", "Limite temporario excedido.", status_code=429)
