"""Pôsteres — resolve a capa do filme via API do TMDB (com cache em disco).

`get_poster(tmdb_id)` busca o pôster no TMDB pelo id; se não houver credencial,
rede ou pôster, devolve um placeholder com o título. `attach(items)` faz o
prefetch concorrente das capas de uma página de resultados de uma vez só.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from core import tmdb

_PLACEHOLDER_BASE = "https://placehold.co/342x513/141414/e50914"


def get_poster(tmdb_id: int, title: Optional[str] = None) -> str:
    """URL do pôster do filme (ou placeholder com o título)."""
    if tmdb_id and int(tmdb_id) > 0:
        url = tmdb.poster_url(int(tmdb_id))
        if url:
            return url
    text = quote((title or "Sem pôster")[:40])
    return f"{_PLACEHOLDER_BASE}?text={text}"


def _movie_id(item: dict) -> int:
    tid = item.get("tmdb_id") or item.get("movie_id")
    try:
        return int(tid) if tid else 0
    except (TypeError, ValueError):
        return 0


def attach(items: list[dict]) -> list[dict]:
    """Devolve cópias de `items` com a chave `poster` preenchida.

    Faz o prefetch concorrente de todas as capas ausentes antes de montar a
    lista, então cada `get_poster` lê do cache (rápido).
    """
    tmdb.prefetch_posters(_movie_id(it) for it in items)
    out = []
    for it in items:
        it = dict(it)
        it["poster"] = get_poster(_movie_id(it), it.get("title"))
        out.append(it)
    return out
