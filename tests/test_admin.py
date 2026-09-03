# -*- coding: utf-8 -*-
"""Painel /admin (allowlist RECOMENDAI_ADMIN_EMAILS) + exportação LGPD."""

import uuid


def _email(p="u"):
    return f"{p}{uuid.uuid4().hex[:10]}@example.com"


def _register(client, csrf, email):
    return client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )


def test_register_requires_privacy_consent(client, csrf):
    r = client.post(
        "/auth/register",
        json={"email": _email(), "password": "password123"},  # sem accepted_privacy
        headers={"X-CSRFToken": csrf()},
    )
    assert r.status_code == 400


def test_privacy_page_is_public(client):
    r = client.get("/privacidade")
    assert r.status_code == 200
    assert b"Pol" in r.data  # "Política de Privacidade"


def test_admin_is_404_for_anon_and_normal_user(client, csrf):
    assert client.get("/admin").status_code == 404
    assert client.get("/admin/api/users").status_code == 404

    _register(client, csrf, _email())  # loga como usuário comum
    assert client.get("/admin").status_code == 404
    assert client.get("/admin/api/users").status_code == 404


def test_admin_access_with_allowlist(client, csrf, monkeypatch):
    admin = _email("admin")
    monkeypatch.setenv("RECOMENDAI_ADMIN_EMAILS", admin)
    _register(client, csrf, admin)

    assert client.get("/admin").status_code == 200
    ov = client.get("/admin/api/overview").get_json()
    assert ov["users"]["total"] >= 1

    users = client.get("/admin/api/users").get_json()["users"]
    assert any(u["email"] == admin for u in users)
    assert all("password_hash" not in u for u in users)  # nunca vaza o hash


def test_admin_analytics(client, csrf, monkeypatch):
    from core import events

    assert client.get("/admin/api/analytics").status_code == 404  # não-admin

    admin = _email("admin")
    monkeypatch.setenv("RECOMENDAI_ADMIN_EMAILS", admin)
    _register(client, csrf, admin)

    events.log("search", query="nada aqui zzz", n_results=0)
    d = client.get("/admin/api/analytics?days=7").get_json()
    assert d["days"] == 7
    assert set(d) >= {"by_kind", "by_day", "top_queries", "zero_result", "filters", "latency", "engagement_by_day"}
    assert "engaged" in d["users"]


def test_admin_actions(client, csrf, monkeypatch):
    from core import users as users_mod

    admin = _email("admin")
    monkeypatch.setenv("RECOMENDAI_ADMIN_EMAILS", admin)

    # cria o alvo numa sessão separada
    import app

    c2 = app.app.test_client()
    t2 = c2.get("/auth/csrf").get_json()["csrf_token"]
    target = _email("target")
    c2.post(
        "/auth/register",
        json={"email": target, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": t2},
    )
    tid = users_mod.get_user_by_email(target)["user_id"]

    _register(client, csrf, admin)  # loga admin

    assert client.post(f"/admin/api/users/{tid}/deactivate", headers={"X-CSRFToken": csrf()}).status_code == 200
    assert users_mod.get_user(tid)["is_active"] is False
    assert client.post(f"/admin/api/users/{tid}/reactivate", headers={"X-CSRFToken": csrf()}).status_code == 200
    assert users_mod.get_user(tid)["is_active"] is True

    assert client.post(f"/admin/api/users/{tid}/delete", headers={"X-CSRFToken": csrf()}).status_code == 200
    assert users_mod.get_user_by_email(target) is None


def test_export_own_data(client, csrf):
    email = _email()
    _register(client, csrf, email)
    client.post(
        "/ratings",
        json={"tmdb_id": 603, "rating": 4.5, "review": "clássico"},
        headers={"X-CSRFToken": csrf()},
    )
    r = client.get("/auth/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    d = r.get_json()
    assert d["account"]["email"] == email
    assert any(x["tmdb_id"] == 603 for x in d["ratings"])
    assert "password_hash" not in d["account"]


def test_export_requires_login(client):
    assert client.get("/auth/export").status_code == 401
