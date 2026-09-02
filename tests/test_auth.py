# -*- coding: utf-8 -*-
"""Fluxo de autenticação: cadastro, CSRF, isolamento entre usuários, reset, exclusão."""

import uuid


def _email():
    return f"u{uuid.uuid4().hex[:10]}@example.com"


def test_register_login_logout(client, csrf):
    email = _email()
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    assert r.status_code == 201 and r.get_json()["needs_verification"] is True
    assert client.get("/auth/me").get_json()["user"]["email"] == email

    client.post("/auth/logout", headers={"X-CSRFToken": csrf()})
    assert client.get("/auth/me").get_json()["user"] is None
    assert client.get("/ratings").status_code == 401  # rota protegida

    r = client.post("/auth/login", json={"email": email, "password": "password123"}, headers={"X-CSRFToken": csrf()})
    assert r.status_code == 200


def test_csrf_required_on_state_change(client):
    r = client.post("/auth/login", json={"email": "x@y.z", "password": "whatever9"})
    assert r.status_code == 400  # sem X-CSRFToken


def test_weak_password_and_dupe_email(client, csrf):
    email = _email()
    assert (
        client.post(
            "/auth/register",
            json={"email": email, "password": "short", "accepted_privacy": True},
            headers={"X-CSRFToken": csrf()},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/auth/register",
            json={"email": email, "password": "password123", "accepted_privacy": True},
            headers={"X-CSRFToken": csrf()},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/auth/register",
            json={"email": email, "password": "password123", "accepted_privacy": True},
            headers={"X-CSRFToken": csrf()},
        ).status_code
        == 409
    )


def test_user_data_isolation(client, csrf):
    import app

    a, b = _email(), _email()
    client.post(
        "/auth/register",
        json={"email": a, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    client.post("/lists", json={"name": "lista da a"}, headers={"X-CSRFToken": csrf()})
    client.post("/auth/logout", headers={"X-CSRFToken": csrf()})

    c2 = app.app.test_client()
    t2 = c2.get("/auth/csrf").get_json()["csrf_token"]
    rb = c2.post(
        "/auth/register",
        json={"email": b, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": t2},
    )
    assert rb.status_code == 201, rb.get_json()
    body = c2.get("/lists").get_json()
    assert body.get("lists") == [], body  # não vê a lista da conta A


def test_password_reset_flow(client, csrf):
    from core import users

    email = _email()
    rr = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    assert rr.status_code == 201, rr.get_json()
    uid = users.get_user_by_email(email)["user_id"]
    token = users.make_token("reset", uid)
    r = client.post("/auth/reset", json={"token": token, "password": "brandnew999"}, headers={"X-CSRFToken": csrf()})
    assert r.status_code == 200
    assert users.authenticate(email, "brandnew999") is not None
    assert users.authenticate(email, "password123") is None


def test_delete_account(client, csrf):
    from core import users

    email = _email()
    client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    assert client.post("/auth/delete", json={"password": "nope"}, headers={"X-CSRFToken": csrf()}).status_code == 403
    assert (
        client.post("/auth/delete", json={"password": "password123"}, headers={"X-CSRFToken": csrf()}).status_code
        == 204
    )
    assert users.get_user_by_email(email) is None


def test_forgot_does_not_enumerate(client, csrf):
    """`/auth/forgot` responde idêntico para e-mail conhecido e desconhecido."""
    known = _email()
    client.post(
        "/auth/register",
        json={"email": known, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    client.post("/auth/logout", headers={"X-CSRFToken": csrf()})

    r_known = client.post("/auth/forgot", json={"email": known}, headers={"X-CSRFToken": csrf()})
    r_unknown = client.post("/auth/forgot", json={"email": _email()}, headers={"X-CSRFToken": csrf()})
    assert r_known.status_code == r_unknown.status_code == 200
    assert r_known.get_json() == r_unknown.get_json()


def test_reset_token_is_single_use(client, csrf):
    from core import users

    email = _email()
    client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    uid = users.get_user_by_email(email)["user_id"]
    token = users.make_token("reset", uid)

    r1 = client.post("/auth/reset", json={"token": token, "password": "brandnew999"}, headers={"X-CSRFToken": csrf()})
    assert r1.status_code == 200
    # o mesmo link não funciona de novo — o hash da senha mudou
    r2 = client.post("/auth/reset", json={"token": token, "password": "another8888"}, headers={"X-CSRFToken": csrf()})
    assert r2.status_code == 400
    assert users.authenticate(email, "brandnew999") is not None
    assert users.authenticate(email, "another8888") is None


def test_reset_token_dies_on_password_change(client, csrf):
    from core import users

    email = _email()
    client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": csrf()},
    )
    uid = users.get_user_by_email(email)["user_id"]
    token = users.make_token("reset", uid)
    users.set_password(uid, "changed12345")  # troca por fora → link pendente morre

    r = client.post("/auth/reset", json={"token": token, "password": "willnotwork1"}, headers={"X-CSRFToken": csrf()})
    assert r.status_code == 400


def test_verify_token_is_single_use(client):
    from core import users

    email = _email()
    c = client
    t = c.get("/auth/csrf").get_json()["csrf_token"]
    c.post(
        "/auth/register",
        json={"email": email, "password": "password123", "accepted_privacy": True},
        headers={"X-CSRFToken": t},
    )
    uid = users.get_user_by_email(email)["user_id"]
    token = users.make_token("verify", uid)

    r1 = c.get(f"/auth/verify/{token}")
    assert r1.status_code == 302 and "verify=ok" in r1.headers["Location"]
    assert users.get_user(uid)["email_verified"] is True

    r2 = c.get(f"/auth/verify/{token}")  # e-mail já confirmado → link morto
    assert "verify=invalid" in r2.headers["Location"]
