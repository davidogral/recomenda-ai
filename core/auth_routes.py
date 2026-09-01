# -*- coding: utf-8 -*-
"""Autenticação da API — cadastro, login (e-mail+senha), verificação de e-mail,
reset de senha e exclusão de conta. Sessão via cookie assinado (Flask-Login),
CSRF via Flask-WTF (header `X-CSRFToken`).

`init_auth(app, limiter)` liga tudo:
  - `SECRET_KEY` (obrigatória em produção; dev cai num fallback com aviso)
  - LoginManager + user_loader
  - CSRFProtect (isenta /metrics, /health e as rotas GET públicas)
  - blueprint `/auth/*`
"""

from __future__ import annotations

import os
import secrets

from flask import Blueprint, jsonify, redirect, request, url_for

from core import mailer, users

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _me(u) -> dict:
    return {"user_id": u.id, "email": u.email, "email_verified": u.email_verified}


def _verify_link(token: str) -> str:
    return request.url_root.rstrip("/") + url_for("auth.verify", token=token)


def _reset_link(token: str) -> str:
    base = request.url_root.rstrip("/")
    return f"{base}/?reset={token}"


# ------------------------------------------------------------------- rotas
@bp.post("/register")
def register():
    from flask_login import login_user

    data = request.get_json(silent=True) or {}
    try:
        user = users.create_user(data.get("email", ""), data.get("password", ""))
    except users.EmailInUse:
        return jsonify({"error": "Já existe uma conta com esse e-mail."}), 409
    except (users.InvalidEmail, users.WeakPassword) as e:
        return jsonify({"error": str(e)}), 400
    token = users.make_token("verify", user["user_id"])
    mailer.send_verify(user["email"], _verify_link(token))
    login_user(users.load_auth_user(str(user["user_id"])), remember=True)
    return jsonify(
        {"user": user, "needs_verification": True, "email_delivery": "smtp" if mailer.is_configured() else "console"}
    ), 201


@bp.get("/verify/<token>")
def verify(token: str):
    uid = users.read_token("verify", token)
    if uid is None:
        return redirect("/?verify=invalid")
    users.mark_verified(uid)
    return redirect("/?verify=ok")


@bp.post("/resend-verification")
def resend_verification():
    from flask_login import current_user

    if not current_user.is_authenticated:
        return jsonify({"error": "Faça login primeiro."}), 401
    if current_user.email_verified:
        return jsonify({"ok": True, "already": True})
    token = users.make_token("verify", current_user.id)
    mailer.send_verify(current_user.email, _verify_link(token))
    return jsonify({"ok": True})


@bp.post("/login")
def login():
    from flask_login import login_user

    data = request.get_json(silent=True) or {}
    user = users.authenticate(data.get("email", ""), data.get("password", ""))
    if not user:
        return jsonify({"error": "E-mail ou senha incorretos."}), 401
    login_user(users.load_auth_user(str(user["user_id"])), remember=bool(data.get("remember", True)))
    return jsonify({"user": user})


@bp.post("/logout")
def logout():
    from flask_login import logout_user

    logout_user()
    return "", 204


@bp.get("/me")
def me():
    from flask_login import current_user

    if not current_user.is_authenticated:
        return jsonify({"user": None}), 200
    return jsonify({"user": _me(current_user)})


@bp.post("/forgot")
def forgot():
    data = request.get_json(silent=True) or {}
    user = users.get_user_by_email(data.get("email", ""))
    if user:  # não vaza se o e-mail existe
        token = users.make_token("reset", user["user_id"])
        mailer.send_reset(user["email"], _reset_link(token))
    return jsonify({"ok": True, "message": "Se o e-mail existir, enviamos um link."})


@bp.post("/reset")
def reset():
    data = request.get_json(silent=True) or {}
    uid = users.read_token("reset", data.get("token", ""))
    if uid is None:
        return jsonify({"error": "Link inválido ou expirado."}), 400
    try:
        users.set_password(uid, data.get("password", ""))
    except users.WeakPassword as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.post("/delete")
def delete_account():
    from flask_login import current_user, logout_user

    if not current_user.is_authenticated:
        return jsonify({"error": "Faça login primeiro."}), 401
    data = request.get_json(silent=True) or {}
    if not users.authenticate(current_user.email, data.get("password", "")):
        return jsonify({"error": "Senha incorreta."}), 403
    uid = current_user.id
    logout_user()
    users.delete_user(uid)
    return "", 204


@bp.get("/csrf")
def csrf():
    from flask_wtf.csrf import generate_csrf

    return jsonify({"csrf_token": generate_csrf()})


# ------------------------------------------------------------------- setup
def init_auth(app, limiter=None) -> None:
    from flask_login import LoginManager
    from flask_wtf.csrf import CSRFProtect

    secret = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        secret = secrets.token_hex(32)
        app.logger.warning(
            "SECRET_KEY não definida — usando chave efêmera "
            "(sessões e tokens não sobrevivem a restart). Defina em produção."
        )
    app.config["SECRET_KEY"] = secret
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault(
        "SESSION_COOKIE_SECURE", os.environ.get("RECOMENDAI_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
    )
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)

    users.init_db()

    lm = LoginManager()
    lm.init_app(app)
    lm.user_loader(users.load_auth_user)

    @lm.unauthorized_handler
    def _unauth():
        return jsonify({"error": "Autenticação necessária."}), 401

    # CSRF em TODA rota state-changing (auth + dados). O frontend busca o token
    # em GET /auth/csrf e manda no header X-CSRFToken (ver apiFetch no index.html).
    from flask_wtf.csrf import CSRFError

    csrf = CSRFProtect()
    csrf.init_app(app)

    @app.errorhandler(CSRFError)
    def _csrf_err(e):
        return jsonify({"error": "Token CSRF ausente ou inválido. Recarregue a página."}), 400

    app.register_blueprint(bp)

    if limiter is not None:
        for rule, lim in (
            ("auth.login", "10 per minute"),
            ("auth.register", "5 per hour;20 per day"),
            ("auth.forgot", "5 per hour"),
            ("auth.reset", "10 per hour"),
        ):
            vf = app.view_functions.get(rule)
            if vf:
                app.view_functions[rule] = limiter.limit(lim)(vf)
