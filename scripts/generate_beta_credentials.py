"""Interactively generate the two Beta accounts without exposing passwords."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.api.app.beta_security import email_digest, hash_password


def _secret(label: str) -> str:
    first = getpass.getpass(f"Senha para {label}: ")
    second = getpass.getpass("Confirme a senha: ")
    if len(first) < 12 or first != second:
        raise SystemExit("As senhas devem coincidir e ter pelo menos 12 caracteres.")
    return first


def main() -> None:
    pepper = os.getenv("BETA_AUTH_EMAIL_PEPPER") or getpass.getpass(
        "BETA_AUTH_EMAIL_PEPPER (entrada oculta): "
    )
    if len(pepper) < 32:
        raise SystemExit("BETA_AUTH_EMAIL_PEPPER deve ter pelo menos 32 caracteres.")
    for scope, label in (("GROUP", "Equipe do Projeto"), ("MOTIVA", "Equipe Motiva")):
        email = input(f"E-mail para {label}: ").strip()
        if not email:
            raise SystemExit("O e-mail não pode estar vazio.")
        password = _secret(label)
        print(f"BETA_{scope}_EMAIL_DIGEST={email_digest(email, pepper)}")
        print(f"BETA_{scope}_PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
