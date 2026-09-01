"""Dados pessoais do usuário — avaliações e listas (agora **multi-usuário**).

Banco separado do catálogo (`data/user.db`), para sobreviver a rebuilds do
índice/catálogo. Famílias de dados:

1. **Avaliações** (estilo Letterboxd), **por usuário**: cada filme tem no máximo
   uma avaliação por conta — nota em meias-estrelas (0.5–5.0), curtida (❤),
   resenha e data de visualização, todos opcionais.
2. **Listas ordenadas** (ordem de assistir), **por usuário**: listas nomeadas de
   filmes com posição explícita, reordenáveis.
3. **Versões dos filmes** (cortes / qual é o melhor): dado **curado e
   compartilhado** — não é por usuário. Semeado uma vez.

Toda função de (1) e (2) recebe `user_id` como primeiro argumento. `contas` de
usuário: `core/users.py`.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("USER_DB_PATH", os.path.join(_PROJECT_ROOT, "data", "user.db"))

LEGACY_USER_ID = 1  # dados do modo single-user antigo vão para cá na migração

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ratings (
    user_id      INTEGER NOT NULL,
    tmdb_id      INTEGER NOT NULL,
    rating       REAL,                      -- 0.5–5.0 em passos de 0.5; NULL = sem nota
    liked        INTEGER NOT NULL DEFAULT 0,
    review       TEXT NOT NULL DEFAULT '',
    watched_date TEXT,                      -- ISO yyyy-mm-dd; NULL = não informada
    title        TEXT NOT NULL DEFAULT '',
    release_year INTEGER,
    poster       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, tmdb_id)
);

CREATE TABLE IF NOT EXISTS lists (
    list_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    list_id      INTEGER NOT NULL,
    tmdb_id      INTEGER NOT NULL,
    position     INTEGER NOT NULL,          -- 1..N = ordem de assistir
    note         TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    release_year INTEGER,
    poster       TEXT,
    added_at     TEXT NOT NULL,
    PRIMARY KEY (list_id, tmdb_id)
);

CREATE TABLE IF NOT EXISTS versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    runtime    INTEGER,
    notes      TEXT NOT NULL DEFAULT '',
    is_best    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Índices criados DEPOIS da migração (dependem de colunas que a migração adiciona).
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_lists_user ON lists(user_id);
CREATE INDEX IF NOT EXISTS idx_versions_movie ON versions(tmdb_id);
"""

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _migrate(conn: sqlite3.Connection) -> None:
    """Traz um `user.db` do modo single-user para o multi-usuário (idempotente).
    Dados antigos vão para `user_id = LEGACY_USER_ID` (reassocie com
    `python -m core.users claim <email>`)."""
    if conn.execute("SELECT 1 FROM meta WHERE key = 'schema_multiuser'").fetchone():
        return

    def cols(table: str) -> set:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    if "ratings" in {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} and "user_id" not in cols("ratings"):
        conn.execute("ALTER TABLE ratings RENAME TO _ratings_old")
        conn.executescript(_SCHEMA)  # recria com o PK novo
        conn.execute(
            "INSERT INTO ratings (user_id, tmdb_id, rating, liked, review, watched_date, "
            "title, release_year, poster, created_at, updated_at) "
            f"SELECT {LEGACY_USER_ID}, tmdb_id, rating, liked, review, watched_date, "
            "title, release_year, poster, created_at, updated_at FROM _ratings_old")
        conn.execute("DROP TABLE _ratings_old")

    if "user_id" not in cols("lists"):
        conn.execute(f"ALTER TABLE lists ADD COLUMN user_id INTEGER NOT NULL DEFAULT {LEGACY_USER_ID}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lists_user ON lists(user_id)")

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_multiuser', '1')")


def _connect() -> sqlite3.Connection:
    """Conexão por operação (volume por-requisição minúsculo — simplicidade > pooling)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)   # tabelas (com user_id em bancos novos)
    _migrate(conn)                # ALTER em bancos vindos do modo single-user
    conn.executescript(_INDEXES)  # índices — agora as colunas existem
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(user_id: Any) -> int:
    n = int(user_id)
    if n <= 0:
        raise ValueError("user_id inválido")
    return n


def normalize_rating(value: Any) -> Optional[float]:
    """Valida a nota: None, ou 0.5–5.0 em passos de 0.5. ValueError se inválida."""
    if value in (None, "", 0, 0.0):
        return None
    r = float(value)
    if not (0.5 <= r <= 5.0) or (r * 2) != int(r * 2):
        raise ValueError("Nota deve ser de 0.5 a 5.0, em passos de 0.5.")
    return r


def normalize_date(value: Any) -> Optional[str]:
    """Valida a data (yyyy-mm-dd) — ValueError se malformada."""
    if not value:
        return None
    s = str(value).strip()
    if not _DATE_RE.match(s):
        raise ValueError("Data deve estar no formato yyyy-mm-dd.")
    return s


# --------------------------------------------------------------------------
# Avaliações (por usuário)
# --------------------------------------------------------------------------

def upsert_rating(user_id: int, tmdb_id: int, *, rating: Optional[float] = None, liked: bool = False,
                  review: str = "", watched_date: Optional[str] = None,
                  title: str = "", release_year: Optional[int] = None,
                  poster: Optional[str] = None) -> dict:
    """Grava (ou substitui) a avaliação de um filme para o usuário."""
    uid, now = _uid(user_id), _now()
    with _connect() as conn:
        row = conn.execute("SELECT created_at FROM ratings WHERE user_id = ? AND tmdb_id = ?",
                           (uid, int(tmdb_id))).fetchone()
        created = row["created_at"] if row else now
        conn.execute(
            """INSERT OR REPLACE INTO ratings
               (user_id, tmdb_id, rating, liked, review, watched_date, title, release_year,
                poster, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, int(tmdb_id), rating, 1 if liked else 0, review or "", watched_date,
             title or "", release_year, poster, created, now),
        )
    return get_rating(uid, tmdb_id)  # type: ignore[return-value]


def get_rating(user_id: int, tmdb_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ratings WHERE user_id = ? AND tmdb_id = ?",
                           (_uid(user_id), int(tmdb_id))).fetchone()
    return _shape(row) if row else None


def list_ratings(user_id: int) -> list[dict]:
    """Avaliações do usuário, mais recentes primeiro (data assistida > atualização)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM ratings WHERE user_id = ?
               ORDER BY COALESCE(watched_date, substr(updated_at, 1, 10)) DESC,
                        updated_at DESC""", (_uid(user_id),)
        ).fetchall()
    return [_shape(r) for r in rows]


def delete_rating(user_id: int, tmdb_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM ratings WHERE user_id = ? AND tmdb_id = ?",
                           (_uid(user_id), int(tmdb_id)))
    return cur.rowcount > 0


def _shape(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["liked"] = bool(d.get("liked"))
    return d


# --------------------------------------------------------------------------
# Listas ordenadas (por usuário)
# --------------------------------------------------------------------------

def _owns_list(conn: sqlite3.Connection, user_id: int, list_id: int) -> bool:
    return conn.execute("SELECT 1 FROM lists WHERE list_id = ? AND user_id = ?",
                        (int(list_id), user_id)).fetchone() is not None


def create_list(user_id: int, name: str, description: str = "") -> dict:
    uid, now = _uid(user_id), _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO lists (user_id, name, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)", (uid, name.strip(), description.strip(), now, now),
        )
        lid = cur.lastrowid
    return get_list(uid, lid)  # type: ignore[return-value]


def get_lists(user_id: int) -> list[dict]:
    """Listas do usuário (recém-mexidas primeiro), com contagem e até 4 pôsteres."""
    with _connect() as conn:
        lists = [dict(r) for r in conn.execute(
            "SELECT * FROM lists WHERE user_id = ? ORDER BY updated_at DESC",
            (_uid(user_id),)).fetchall()]
        for lst in lists:
            lid = lst["list_id"]
            lst["n_items"] = conn.execute(
                "SELECT COUNT(*) FROM list_items WHERE list_id = ?", (lid,)).fetchone()[0]
            lst["posters"] = [r[0] for r in conn.execute(
                """SELECT poster FROM list_items
                   WHERE list_id = ? AND poster IS NOT NULL
                   ORDER BY position LIMIT 4""", (lid,)).fetchall()]
    return lists


def get_list(user_id: int, list_id: int) -> Optional[dict]:
    """Uma lista do usuário com os itens em ordem de assistir, ou None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM lists WHERE list_id = ? AND user_id = ?",
                           (int(list_id), _uid(user_id))).fetchone()
        if row is None:
            return None
        lst = dict(row)
        lst["items"] = [dict(r) for r in conn.execute(
            "SELECT * FROM list_items WHERE list_id = ? ORDER BY position",
            (int(list_id),)).fetchall()]
    return lst


def delete_list(user_id: int, list_id: int) -> bool:
    with _connect() as conn:
        if not _owns_list(conn, _uid(user_id), list_id):
            return False
        conn.execute("DELETE FROM list_items WHERE list_id = ?", (int(list_id),))
        cur = conn.execute("DELETE FROM lists WHERE list_id = ?", (int(list_id),))
    return cur.rowcount > 0


def add_list_item(user_id: int, list_id: int, tmdb_id: int, *, title: str = "",
                  release_year: Optional[int] = None,
                  poster: Optional[str] = None) -> Optional[bool]:
    """None = lista não é do usuário / não existe; True = adicionado; False = já estava."""
    uid, now = _uid(user_id), _now()
    with _connect() as conn:
        if not _owns_list(conn, uid, list_id):
            return None
        if conn.execute("SELECT 1 FROM list_items WHERE list_id = ? AND tmdb_id = ?",
                        (int(list_id), int(tmdb_id))).fetchone():
            return False
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM list_items WHERE list_id = ?",
                           (int(list_id),)).fetchone()[0]
        conn.execute(
            """INSERT INTO list_items
               (list_id, tmdb_id, position, title, release_year, poster, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(list_id), int(tmdb_id), pos, title or "", release_year, poster, now),
        )
        conn.execute("UPDATE lists SET updated_at = ? WHERE list_id = ?", (now, int(list_id)))
    return True


def remove_list_item(user_id: int, list_id: int, tmdb_id: int) -> bool:
    with _connect() as conn:
        if not _owns_list(conn, _uid(user_id), list_id):
            return False
        cur = conn.execute("DELETE FROM list_items WHERE list_id = ? AND tmdb_id = ?",
                           (int(list_id), int(tmdb_id)))
        if cur.rowcount == 0:
            return False
        remaining = [r[0] for r in conn.execute(
            "SELECT tmdb_id FROM list_items WHERE list_id = ? ORDER BY position",
            (int(list_id),)).fetchall()]
        for i, tid in enumerate(remaining, start=1):
            conn.execute("UPDATE list_items SET position = ? WHERE list_id = ? AND tmdb_id = ?",
                         (i, int(list_id), tid))
        conn.execute("UPDATE lists SET updated_at = ? WHERE list_id = ?", (_now(), int(list_id)))
    return True


def reorder_list(user_id: int, list_id: int, tmdb_ids: list) -> bool:
    with _connect() as conn:
        if not _owns_list(conn, _uid(user_id), list_id):
            return False
        current = [r[0] for r in conn.execute(
            "SELECT tmdb_id FROM list_items WHERE list_id = ? ORDER BY position",
            (int(list_id),)).fetchall()]
        cset = set(current)
        given, seen = [], set()
        for t in tmdb_ids:
            try:
                tid = int(t)
            except (TypeError, ValueError):
                continue
            if tid in cset and tid not in seen:
                seen.add(tid)
                given.append(tid)
        ordered = given + [t for t in current if t not in seen]
        for i, tid in enumerate(ordered, start=1):
            conn.execute("UPDATE list_items SET position = ? WHERE list_id = ? AND tmdb_id = ?",
                         (i, int(list_id), tid))
        conn.execute("UPDATE lists SET updated_at = ? WHERE list_id = ?", (_now(), int(list_id)))
    return True


# --------------------------------------------------------------------------
# Versões dos filmes — dado CURADO e COMPARTILHADO (não é por usuário)
# --------------------------------------------------------------------------

_seed_checked = False


def ensure_versions_seed() -> None:
    """Carga única do pacote inicial de versões famosas (Blade Runner etc.)."""
    global _seed_checked
    if _seed_checked:
        return
    _seed_checked = True
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM meta WHERE key = 'versions_seeded'").fetchone():
            return
        from core.versions_seed import SEED

        now = _now()
        for tmdb_id, versions in SEED:
            for name, runtime, is_best, notes in versions:
                conn.execute(
                    """INSERT INTO versions
                       (tmdb_id, name, runtime, notes, is_best, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tmdb_id, name, runtime, notes, 1 if is_best else 0, now, now),
                )
        conn.execute("INSERT INTO meta (key, value) VALUES ('versions_seeded', '1')")


def list_versions(tmdb_id: int) -> list[dict]:
    ensure_versions_seed()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM versions WHERE tmdb_id = ? ORDER BY version_id",
                            (int(tmdb_id),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_best"] = bool(d["is_best"])
        out.append(d)
    return out


def save_version(tmdb_id: int, name: str, *, runtime: Optional[int] = None,
                 notes: str = "", is_best: bool = False,
                 version_id: Optional[int] = None) -> Optional[dict]:
    ensure_versions_seed()
    now = _now()
    with _connect() as conn:
        if is_best:
            conn.execute("UPDATE versions SET is_best = 0 WHERE tmdb_id = ?", (int(tmdb_id),))
        if version_id is None:
            cur = conn.execute(
                """INSERT INTO versions
                   (tmdb_id, name, runtime, notes, is_best, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(tmdb_id), name, runtime, notes, 1 if is_best else 0, now, now),
            )
            version_id = cur.lastrowid
        else:
            cur = conn.execute(
                """UPDATE versions SET name = ?, runtime = ?, notes = ?, is_best = ?,
                   updated_at = ? WHERE version_id = ? AND tmdb_id = ?""",
                (name, runtime, notes, 1 if is_best else 0, now,
                 int(version_id), int(tmdb_id)),
            )
            if cur.rowcount == 0:
                return None
        row = conn.execute("SELECT * FROM versions WHERE version_id = ?",
                           (int(version_id),)).fetchone()
    d = dict(row)
    d["is_best"] = bool(d["is_best"])
    return d


def delete_version(version_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM versions WHERE version_id = ?", (int(version_id),))
    return cur.rowcount > 0
