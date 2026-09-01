# -*- coding: utf-8 -*-
"""Fixtures compartilhadas: isola os SQLite de usuário num tmpdir e fixa a
SECRET_KEY, para os testes não tocarem `data/user.db`."""

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="recomendaai-tests-")
os.environ.setdefault("USER_DB_PATH", os.path.join(_TMP, "user.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-prod")
os.environ.setdefault("RECOMENDAI_NO_WARMUP", "1")
os.environ.setdefault("RECOMENDAI_TMDB_NAMES", "0")


@pytest.fixture()
def client():
    import app

    # TESTING desliga o CSRF do Flask-WTF por padrão — forçamos ligado para
    # os testes exercitarem o caminho real.
    app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    # rate limit compartilha o bucket por IP entre todos os test clients →
    # desliga para os testes (o comportamento do limite tem teste próprio).
    try:
        app.limiter.enabled = False
    except Exception:
        pass
    return app.app.test_client()


@pytest.fixture()
def csrf(client):
    def _t():
        return client.get("/auth/csrf").get_json()["csrf_token"]

    return _t
