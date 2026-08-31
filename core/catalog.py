"""Catálogo de filmes — fonte única para recuperação e recomendação.

Funciona em dois modos:
1. **SQLite** (data/raw/movies.db): modo completo com joins de gêneros, keywords, pessoas.
2. **JSON** (data/tmdb_movies_large.json): modo fallback simples (apenas dados do JSON).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from core import db

_MOVIE_COLUMNS = [
    "tmdb_id",
    "title",
    "release_date",
    "release_year",
    "runtime_minutes",
    "origin_countries",
    "original_language",
    "overview",
    "vote_average",
    "vote_count",
    "popularity",
]

_movies_df = None
_movies_dict: Optional[dict[int, dict[str, Any]]] = None


def _split_countries(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [c for c in value.split("|") if c]


def _json_to_movie(j: dict) -> dict:
    """Converte um filme do formato JSON para o formato do catálogo."""
    release_date = j.get("release_date", "") or ""
    release_year = None
    if release_date and len(release_date) >= 4:
        try:
            release_year = int(release_date[:4])
        except ValueError:
            pass

    return {
        "tmdb_id": int(j["id"]),
        "title": j.get("title", ""),
        "release_date": release_date,
        "release_year": release_year,
        "runtime_minutes": None,
        "origin_countries": [],
        "original_language": j.get("original_language", ""),
        "overview": j.get("overview", "") or "",
        "vote_average": j.get("vote_average", 0),
        "vote_count": j.get("vote_count", 0),
        "popularity": j.get("popularity", 0),
    }


def get_catalog_df():
    global _movies_df
    if _movies_df is None:
        if db.has_sqlite():
            import pandas as pd
            rows = db.query(f"SELECT {', '.join(_MOVIE_COLUMNS)} FROM movies")
            df = pd.DataFrame(rows, columns=_MOVIE_COLUMNS)
            df["overview"] = df["overview"].fillna("")
            df["origin_countries"] = df["origin_countries"].apply(_split_countries)
        else:
            import pandas as pd
            movies = db.load_json_movies()
            rows = [_json_to_movie(m) for m in movies]
            df = pd.DataFrame(rows)
            df["overview"] = df["overview"].fillna("")
        _movies_df = df
    return _movies_df


def get_catalog() -> dict[int, dict[str, Any]]:
    global _movies_dict
    if _movies_dict is None:
        if db.has_sqlite():
            rows = db.query(f"SELECT {', '.join(_MOVIE_COLUMNS)} FROM movies")
            catalog: dict[int, dict[str, Any]] = {}
            for row in rows:
                row["overview"] = row["overview"] or ""
                row["origin_countries"] = _split_countries(row["origin_countries"])
                catalog[row["tmdb_id"]] = row
        else:
            movies = db.load_json_movies()
            catalog = {}
            for m in movies:
                mv = _json_to_movie(m)
                catalog[mv["tmdb_id"]] = mv
        _movies_dict = catalog
    return _movies_dict


def get_movie(tmdb_id: int) -> Optional[dict[str, Any]]:
    return get_catalog().get(tmdb_id)


def reset_cache() -> None:
    global _movies_df, _movies_dict
    _movies_df = None
    _movies_dict = None
    get_genre_names.cache_clear()
    _genres_by_movie.cache_clear()
    _keywords_by_movie.cache_clear()


# --------------------------------------------------------------------------
# Joins sob demanda — disponíveis apenas com SQLite
# --------------------------------------------------------------------------


def _check_sqlite():
    if not db.has_sqlite():
        raise RuntimeError(
            "SQLite não disponível (falta data/raw/movies.db). "
            "Crie o banco rodando: python -c \"from retrieval.index_builder import build_index; build_index()\""
        )


@lru_cache(maxsize=1)
def get_genre_names() -> dict[int, str]:
    if not db.has_sqlite():
        # Fallback JSON: extrair gêneros únicos de todos os filmes
        movies = db.load_json_movies()
        names: dict[int, str] = {}
        gid = 1
        seen: set[str] = set()
        for m in movies:
            for g in m.get("genres", []):
                if g not in seen:
                    seen.add(g)
                    names[gid] = g
                    gid += 1
        return names
    return {r["genre_id"]: r["name"] for r in db.query("SELECT genre_id, name FROM genres")}


def get_movie_genres(tmdb_id: int) -> list[str]:
    if not db.has_sqlite():
        # Fallback JSON: extrair gêneros do JSON
        movies = db.load_json_movies()
        for m in movies:
            if int(m.get("id", 0)) == tmdb_id:
                return m.get("genres", [])
        return []
    _check_sqlite()
    rows = db.query(
        """
        SELECT g.name AS name
        FROM movie_genres mg JOIN genres g ON g.genre_id = mg.genre_id
        WHERE mg.tmdb_id = ?
        ORDER BY g.name
        """,
        (tmdb_id,),
    )
    return [r["name"] for r in rows]


def get_movie_keywords(tmdb_id: int) -> list[str]:
    _check_sqlite()
    rows = db.query(
        """
        SELECT k.name AS name
        FROM movie_keywords mk JOIN keywords k ON k.keyword_id = mk.keyword_id
        WHERE mk.tmdb_id = ?
        ORDER BY k.name
        """,
        (tmdb_id,),
    )
    return [r["name"] for r in rows]


def get_movie_people(tmdb_id: int, role: Optional[str] = None) -> list[dict[str, Any]]:
    _check_sqlite()
    sql = """
        SELECT p.name AS name, mp.role AS role, mp.character AS character,
               mp.credit_order AS credit_order
        FROM movie_people mp JOIN people p ON p.person_id = mp.person_id
        WHERE mp.tmdb_id = ?
    """
    params: list[Any] = [tmdb_id]
    if role is not None:
        sql += " AND mp.role = ?"
        params.append(role)
    sql += " ORDER BY mp.credit_order"
    return db.query(sql, params)


@lru_cache(maxsize=1)
def _genres_by_movie() -> dict[int, list[str]]:
    _check_sqlite()
    names = get_genre_names()
    out: dict[int, list[str]] = {}
    for r in db.query("SELECT tmdb_id, genre_id FROM movie_genres"):
        out.setdefault(r["tmdb_id"], []).append(names.get(r["genre_id"], ""))
    return out


@lru_cache(maxsize=1)
def _keywords_by_movie() -> dict[int, list[str]]:
    _check_sqlite()
    names = {r["keyword_id"]: r["name"] for r in db.query("SELECT keyword_id, name FROM keywords")}
    out: dict[int, list[str]] = {}
    for r in db.query("SELECT tmdb_id, keyword_id FROM movie_keywords"):
        out.setdefault(r["tmdb_id"], []).append(names.get(r["keyword_id"], ""))
    return out


def genres_by_movie() -> dict[int, list[str]]:
    return _genres_by_movie()


def keywords_by_movie() -> dict[int, list[str]]:
    return _keywords_by_movie()
