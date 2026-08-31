"""Conexão com o dataset. Tenta SQLite (data/raw/movies.db); se não existir,
carrega do JSON (data/tmdb_movies_large.json) como fallback.

Assim o sistema funciona com os dados existentes e, quando o SQLite for
criado (via build_index), passa a usar o banco real.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get(
    "MOVIES_DB_PATH", os.path.join(_PROJECT_ROOT, "data", "raw", "movies.db")
)
JSON_PATH = os.environ.get(
    "MOVIES_JSON_PATH", os.path.join(_PROJECT_ROOT, "data", "tmdb_movies_large.json")
)

_local = threading.local()
Params = Union[Sequence[Any], Mapping[str, Any]]


def get_connection() -> sqlite3.Connection:
    """Retorna a conexão read-only desta thread, ou None se não existir."""
    conn: Optional[sqlite3.Connection] = getattr(_local, "conn", None)
    if conn is None:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(
                f"Banco SQLite não encontrado em {DB_PATH}. "
                "Usando fallback JSON. Para funcionalidade completa, "
                "crie o banco com: python -c \"from retrieval.index_builder import build_index; build_index()\""
            )
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def has_sqlite() -> bool:
    return os.path.exists(DB_PATH)


# -------------------------------------------------------------------------
# JSON fallback helpers (usados pelo catalog quando não há SQLite)
# -------------------------------------------------------------------------

_json_movies: Optional[list[dict]] = None


def load_json_movies() -> list[dict]:
    global _json_movies
    if _json_movies is None:
        if not os.path.exists(JSON_PATH):
            JSON_PATH2 = os.path.join(_PROJECT_ROOT, "data", "tmdb_movies.json")
            if os.path.exists(JSON_PATH2):
                path = JSON_PATH2
            else:
                raise FileNotFoundError(
                    f"Nenhum dado encontrado. Coloque tmdb_movies_large.json ou tmdb_movies.json em data/"
                )
        else:
            path = JSON_PATH
        with open(path, "r", encoding="utf-8") as f:
            _json_movies = json.load(f)
    return _json_movies


def query(sql: str, params: Params = ()) -> list[dict[str, Any]]:
    """Executa um SELECT parametrizado. Se SQLite não existir, levanta erro."""
    if not has_sqlite():
        raise RuntimeError("SQLite não disponível. Operação SQL não suportada sem o banco.")
    cur = get_connection().execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return [dict(row) for row in rows]


def query_one(sql: str, params: Params = ()) -> Optional[dict[str, Any]]:
    if not has_sqlite():
        raise RuntimeError("SQLite não disponível.")
    cur = get_connection().execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return dict(row) if row is not None else None


def query_scalar(sql: str, params: Params = ()) -> Any:
    if not has_sqlite():
        raise RuntimeError("SQLite não disponível.")
    cur = get_connection().execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row[0] if row is not None else None


def iter_query(sql: str, params: Params = ()) -> Iterable[dict[str, Any]]:
    if not has_sqlite():
        raise RuntimeError("SQLite não disponível.")
    cur = get_connection().execute(sql, params)
    try:
        for row in cur:
            yield dict(row)
    finally:
        cur.close()


def close_connection() -> None:
    conn: Optional[sqlite3.Connection] = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
