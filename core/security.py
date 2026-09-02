# -*- coding: utf-8 -*-
"""Segredo de assinatura e modo de execução — fonte única.

`SECRET_KEY` assina o cookie de sessão (Flask), o cookie *remember* (Flask-Login)
e os tokens de e-mail/reset (`itsdangerous`, em `core/users.py`). Antes isso
vivia duplicado em dois lugares, com fallbacks diferentes — um deles (`"dev-insecure-"
+ pid`) adivinhável, o que tornava um token de reset forjável se `SECRET_KEY`
não estivesse setada em produção.

Regras:
  - Produção (`RECOMENDAI_ENV=production`): `SECRET_KEY` (ou `FLASK_SECRET_KEY`)
    é **obrigatória** — sem ela `resolve_secret_key()` levanta e o processo não sobe.
  - Dev: se não houver, gera a chave uma vez e guarda em `data/.secret_key`
    (fora do git, `0600`) — sessões e tokens sobrevivem a restart.
"""

from __future__ import annotations

import os
import secrets

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEV_KEY_PATH = os.environ.get("RECOMENDAI_SECRET_KEY_FILE", os.path.join(_PROJECT_ROOT, "data", ".secret_key"))

_TRUTHY = ("1", "true", "yes", "on")


def is_production() -> bool:
    return os.environ.get("RECOMENDAI_ENV", "dev").strip().lower() in ("production", "prod")


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def cookie_secure() -> bool:
    """Cookies de sessão/remember marcados `Secure`? `RECOMENDAI_COOKIE_SECURE`
    força/desliga explicitamente; sem ela, liga sozinho em produção."""
    raw = os.environ.get("RECOMENDAI_COOKIE_SECURE")
    if raw is not None:
        return raw.strip().lower() in _TRUTHY
    return is_production()


class MissingSecretKey(RuntimeError):
    """`RECOMENDAI_ENV=production` sem `SECRET_KEY` no ambiente."""


def resolve_secret_key() -> str:
    secret = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if secret:
        return secret
    if is_production():
        raise MissingSecretKey(
            "SECRET_KEY não definida e RECOMENDAI_ENV=production. Gere uma "
            'chave de 32+ bytes: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    # dev: chave persistente por checkout, fora do git
    try:
        with open(_DEV_KEY_PATH, encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    generated = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_DEV_KEY_PATH), exist_ok=True)
        with open(_DEV_KEY_PATH, "w", encoding="utf-8") as fh:
            fh.write(generated + "\n")
        os.chmod(_DEV_KEY_PATH, 0o600)
    except OSError:
        pass  # sem disco de escrita → chave efêmera por processo (o chamador avisa)
    return generated
