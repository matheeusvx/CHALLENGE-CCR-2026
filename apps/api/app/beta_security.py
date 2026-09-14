"""Primitive cryptographic operations for the private Beta authentication."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


ALLOWED_SCOPES = frozenset({"group", "motiva"})
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_digest(email: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"), normalize_email(email).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
        p=SCRYPT_P, dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        (
            "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
            _b64encode(salt), _b64encode(derived),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_raw, r_raw, p_raw, salt_raw, expected_raw = encoded.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        salt = _b64decode(salt_raw)
        expected = _b64decode(expected_raw)
        if len(salt) != 16 or len(expected) != SCRYPT_DKLEN:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_token(
    scope: str, secret: str, *, now: int | None = None, ttl: int = 28800
) -> str:
    if scope not in ALLOWED_SCOPES:
        raise ValueError("Unknown account scope.")
    issued_at = int(time.time() if now is None else now)
    payload = _b64encode(
        json.dumps(
            {"scope": scope, "iat": issued_at, "exp": issued_at + ttl},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    )
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return f"{payload}.{_b64encode(signature.digest())}"


def verify_token(token: str, secret: str, *, now: int | None = None) -> str | None:
    try:
        payload_raw, signature_raw = token.split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), payload_raw.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_raw)):
            return None
        payload: dict[str, Any] = json.loads(_b64decode(payload_raw))
        current = int(time.time() if now is None else now)
        scope = payload.get("scope")
        if (
            scope not in ALLOWED_SCOPES
            or int(payload["iat"]) > current
            or int(payload["exp"]) <= current
        ):
            return None
        return str(scope)
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        UnicodeError,
        binascii.Error,
    ):
        return None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
