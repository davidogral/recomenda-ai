# -*- coding: utf-8 -*-
"""Contas de usuário — cadastro, login (e-mail + senha), verificação e reset.

Fica no mesmo SQLite dos dados pessoais (`data/user.db`). Senha com **Argon2id**
(default do `argon2-cffi`), com rehash automático quando os parâmetros mudam.
Tokens de verificação de e-mail e de reset de senha são assinados com
`itsdangerous` sobre a `SECRET_KEY` (via `core.security`), com validade **e**
amarrados a um estado mutável da conta (ver `_token_fingerprint`) — trocar a
senha invalida tokens de reset pendentes; confirmar o e-mail invalida o link de
verificação. Na prática: **uso único**.

CLI utilitário:
    python -m core.users create  <email> <senha>
    python -m core.users verify  <email>            # marca e-mail como verificado
    python -m core.users passwd  <email> <nova>
    python -m core.users claim   <email>            # reassocia dados órfãos (user_id=1) a este usuário
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional

from core.user_data import _connect  # mesmo banco / migração de schema

_MIN_PASSWORD = 8
TOKEN_MAX_AGE = int(os.environ.get("RECOMENDAI_TOKEN_MAX_AGE", str(60 * 60 * 24)))  # 24 h

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hasher():
    from argon2 import PasswordHasher

    return PasswordHasher()


def _serializer(salt: str):
    from itsdangerous import URLSafeTimedSerializer

    from core.security import resolve_secret_key

    return URLSafeTimedSerializer(resolve_secret_key(), salt=f"recomendaai-{salt}")


_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    """Hash Argon2 fixo para gastar o mesmo tempo quando o e-mail não existe —
    fecha o canal lateral de timing que distinguia 'sem conta' de 'senha errada'."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hasher().hash("timing-equalizer-not-a-real-password")
    return _DUMMY_HASH


class InvalidEmail(ValueError):
    pass


class WeakPassword(ValueError):
    pass


class EmailInUse(ValueError):
    pass


def normalize_email(email: str) -> str:
    from email_validator import EmailNotValidError, validate_email

    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as e:
        raise InvalidEmail(str(e)) from e


def init_db() -> None:
    """Cria a tabela `users` (idempotente)."""
    with _connect() as conn:  # _connect() já roda o schema + migração de user_data
        conn.executescript(_USERS_SCHEMA)


def _row_to_user(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    d["email_verified"] = bool(d["email_verified"])
    d["is_active"] = bool(d["is_active"])
    return d


def create_user(email: str, password: str) -> dict:
    email = normalize_email(email)
    if len(password or "") < _MIN_PASSWORD:
        raise WeakPassword(f"A senha precisa de pelo menos {_MIN_PASSWORD} caracteres.")
    init_db()
    now = _now()
    ph = _hasher().hash(password)
    with _connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (email, ph, now, now),
            )
        except sqlite3.IntegrityError as e:
            raise EmailInUse("Já existe uma conta com esse e-mail.") from e
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_user(row)


def get_user(user_id: int) -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    init_db()
    try:
        email = normalize_email(email)
    except InvalidEmail:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return _row_to_user(row) if row else None


def authenticate(email: str, password: str) -> Optional[dict]:
    """Devolve o usuário se e-mail+senha batem e a conta está ativa; senão None.
    Reidrata o hash Argon2 se os parâmetros mudaram."""
    from argon2.exceptions import VerifyMismatchError

    init_db()
    try:
        email = normalize_email(email)
    except InvalidEmail:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        ph = _hasher()
        if not row or not row["is_active"]:
            try:  # gasta ~o mesmo tempo de um verify real (timing)
                ph.verify(_dummy_hash(), password)
            except Exception:
                pass
            return None
        try:
            ph.verify(row["password_hash"], password)
        except VerifyMismatchError:
            return None
        if ph.check_needs_rehash(row["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (ph.hash(password), _now(), row["user_id"]),
            )
    return _row_to_user(row)


def set_password(user_id: int, password: str) -> None:
    if len(password or "") < _MIN_PASSWORD:
        raise WeakPassword(f"A senha precisa de pelo menos {_MIN_PASSWORD} caracteres.")
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
            (_hasher().hash(password), _now(), int(user_id)),
        )


def mark_verified(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET email_verified = 1, updated_at = ? WHERE user_id = ?", (_now(), int(user_id)))


def set_active(user_id: int, active: bool) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE user_id = ?",
            (1 if active else 0, _now(), int(user_id)),
        )
    return cur.rowcount > 0


# --------------------------------------------------------------- admin
def admin_emails() -> "set[str]":
    """E-mails com acesso ao painel admin — allowlist por env, normalizada."""
    raw = os.environ.get("RECOMENDAI_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: Optional[str]) -> bool:
    return bool(email) and email.strip().lower() in admin_emails()


def list_users() -> list[dict]:
    """Todas as contas (mais novas primeiro), sem hash de senha, com contagens
    de diário/listas/resenhas anexadas."""
    from core import user_data

    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    counts = user_data.counts_by_user()
    out = []
    for r in rows:
        u = _row_to_user(r)
        u["counts"] = counts.get(u["user_id"], {"ratings": 0, "reviews": 0, "lists": 0})
        out.append(u)
    return out


def count_users() -> dict:
    init_db()
    with _connect() as conn:
        total, verified, active = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(email_verified), 0), COALESCE(SUM(is_active), 0) FROM users"
        ).fetchone()
    return {"total": total, "verified": verified, "active": active}


def signups_by_day(days: int = 30) -> list[dict]:
    """Série densa [{date, count}] dos últimos `days` dias (zeros preenchidos),
    do mais antigo ao mais recente — para o sparkline do painel."""
    from datetime import date, timedelta

    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT substr(created_at, 1, 10) AS d, COUNT(*) FROM users GROUP BY d").fetchall()
    counts = {r[0]: r[1] for r in rows}
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        out.append({"date": d, "count": counts.get(d, 0)})
    return out


def delete_user(user_id: int) -> bool:
    """Apaga a conta e os dados pessoais dela (avaliações e listas). As `versions`
    são curadoria compartilhada — não pertencem a um usuário e ficam."""
    uid = int(user_id)
    with _connect() as conn:
        conn.execute("DELETE FROM ratings WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM list_items WHERE list_id IN (SELECT list_id FROM lists WHERE user_id = ?)", (uid,))
        conn.execute("DELETE FROM lists WHERE user_id = ?", (uid,))
        cur = conn.execute("DELETE FROM users WHERE user_id = ?", (uid,))
    return cur.rowcount > 0


# --------------------------------------------------------------- tokens
def _token_fingerprint(kind: str, row: sqlite3.Row) -> str:
    """Amarra o token a um estado da conta que muda quando o token é 'usado':
      - reset:  o hash da senha       → resetar (ou trocar) a senha mata o token
      - verify: a flag email_verified → confirmar o e-mail mata o link
    Assim o link vale **uma vez só**, mesmo dentro da janela de validade."""
    if kind == "reset":
        basis = f"{row['user_id']}:{row['password_hash']}"
    elif kind == "verify":
        basis = f"{row['user_id']}:{row['email']}:{int(row['email_verified'])}"
    else:
        basis = str(row["user_id"])
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _fetch_row(conn: sqlite3.Connection, user_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM users WHERE user_id = ?", (int(user_id),)).fetchone()


def make_token(kind: str, user_id: int) -> str:
    """kind: 'verify' (e-mail) | 'reset' (senha). O payload assinado leva o
    fingerprint do estado atual da conta (ver `_token_fingerprint`)."""
    init_db()
    with _connect() as conn:
        row = _fetch_row(conn, user_id)
    if row is None:
        raise ValueError(f"usuário {user_id} inexistente")
    return _serializer(kind).dumps({"uid": int(user_id), "fp": _token_fingerprint(kind, row)})


def read_token(kind: str, token: str, max_age: Optional[int] = None) -> Optional[int]:
    from itsdangerous import BadSignature, SignatureExpired

    try:
        data = _serializer(kind).loads(token, max_age=max_age or TOKEN_MAX_AGE)
        uid = int(data["uid"])
        fp = str(data.get("fp", ""))
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None
    with _connect() as conn:
        row = _fetch_row(conn, uid)
    if row is None or not row["is_active"]:
        return None
    if not fp or not secrets.compare_digest(fp, _token_fingerprint(kind, row)):
        return None  # token já usado, senha trocada, ou payload adulterado
    return uid


# --------------------------------------------------------------- Flask-Login
class AuthUser:
    """Adaptador para o Flask-Login."""

    def __init__(self, row: dict):
        self.id = row["user_id"]
        self.email = row["email"]
        self.email_verified = row["email_verified"]
        self._active = row["is_active"]

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return bool(self._active)

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)


def load_auth_user(user_id: str) -> Optional["AuthUser"]:
    u = get_user(int(user_id))
    return AuthUser(u) if u and u["is_active"] else None


# --------------------------------------------------------------- CLI
def _claim_orphans(email: str) -> None:
    """Reassocia dados com user_id=1 (backfill da migração) ao usuário `email`."""
    u = get_user_by_email(email)
    if not u:
        sys.exit(f"nenhuma conta com {email!r} — crie primeiro (`create`).")
    uid = u["user_id"]
    with _connect() as conn:
        n = 0
        for tbl in ("ratings", "lists"):
            n += conn.execute(f"UPDATE {tbl} SET user_id = ? WHERE user_id = 1", (uid,)).rowcount
    print(f"» {n} linhas reassociadas de user_id=1 para {email} (user_id={uid}).")


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__)
        return 1
    cmd, rest = args[0], args[1:]
    if cmd == "create" and len(rest) == 2:
        print(create_user(rest[0], rest[1]))
    elif cmd == "verify" and len(rest) == 1:
        u = get_user_by_email(rest[0]) or sys.exit("conta não encontrada")
        mark_verified(u["user_id"])
        print("verificado.")
    elif cmd == "passwd" and len(rest) == 2:
        u = get_user_by_email(rest[0]) or sys.exit("conta não encontrada")
        set_password(u["user_id"], rest[1])
        print("senha trocada.")
    elif cmd == "claim" and len(rest) == 1:
        _claim_orphans(rest[0])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
