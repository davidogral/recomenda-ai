# -*- coding: utf-8 -*-
"""core/security.py — resolução do segredo de assinatura e flags de cookie.

`init_auth` (Flask-Login + CSRF + cookies) é exercido de ponta a ponta em
test_auth.py; aqui isolamos as regras de ambiente."""

import pytest

from core import security


def test_missing_secret_key_is_fatal_in_production(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("RECOMENDAI_ENV", "production")
    with pytest.raises(security.MissingSecretKey):
        security.resolve_secret_key()


def test_env_secret_key_wins_in_production(monkeypatch):
    monkeypatch.setenv("RECOMENDAI_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    assert security.resolve_secret_key() == "x" * 64


def test_cookie_secure_follows_env(monkeypatch):
    monkeypatch.delenv("RECOMENDAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("RECOMENDAI_ENV", "production")
    assert security.cookie_secure() is True
    monkeypatch.setenv("RECOMENDAI_ENV", "dev")
    assert security.cookie_secure() is False
    monkeypatch.setenv("RECOMENDAI_COOKIE_SECURE", "1")  # override explícito ganha
    assert security.cookie_secure() is True


def test_init_auth_hardens_cookie_flags(monkeypatch):
    from flask import Flask

    from core import auth_routes

    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("RECOMENDAI_COOKIE_SECURE", "1")
    app = Flask(__name__)
    auth_routes.init_auth(app)

    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
    assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
