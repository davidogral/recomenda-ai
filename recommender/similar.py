"""Filmes parecidos com um filme-semente (recomendação item-to-item).

Responde à pergunta "gostei de X, me dá parecidos" (ex.: *Blade Runner* →
sci-fi noir-futurista). Funde **dois sinais de similaridade que o projeto já
tem**, sem treino novo:

  - **Conteúdo (e5)**: similaridade entre os embeddings de sinopse + temático do
    índice de busca — "tem a mesma cara/tema". Funciona para qualquer filme do
    catálogo, inclusive lançamentos recentes sem ratings.
  - **Colaborativo (item-item)**: vizinhos pré-computados do SVD
    (`recommender/weights/neighbors.npy`) — "quem gostou deste também gostou".
    Traz serendipidade de co-avaliação real.

A fusão é por **Reciprocal Rank Fusion (RRF)** — o mesmo esquema do perfil
(`recommender/profile.py`): combina pela *posição* em cada ranking, sem depender
de escalas comparáveis entre os sinais. Degrada com elegância: filme sem sinal
colaborativo usa só conteúdo; filme fora do índice de conteúdo usa só o
colaborativo.

Diferença para o perfil: aqui o sinal **temático pesa mais** (`KW_WEIGHT=1.5`),
porque o que define "mesmo tipo de filme" é o tema/gênero (replicante, dystopia,
neo-noir) e não o vocabulário de ação da sinopse — sem isso, blockbusters de
ação genéricos invadem a lista. Em vez da penalidade de genericidade do perfil,
usamos **cosseno centralizado**: subtraímos a direção média do catálogo antes de
comparar, o que remove o componente "hub" (hubness) de forma simétrica. O
cálculo é feito em forma fechada para não materializar uma cópia centralizada da
matriz de embeddings (economiza ~180 MB de RAM no servidor).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from core import catalog

# Peso do sinal temático (keywords) vs. sinopse. Maior que no perfil (0.5) de
# propósito: para "parecidos", o tema/gênero é o que importa, não a ação descrita.
KW_WEIGHT = 1.5

# Fusão por rank recíproco: conteúdo manda, colaborativo complementa.
CONTENT_RRF_W = 1.0
COLLAB_RRF_W = 0.6
RRF_K = 20

# Estatísticas de centralização por matriz, cacheadas por engine (singleton).
_center_cache: dict[tuple[int, str], dict[str, Any]] = {}


def _center_stats(M: np.ndarray) -> dict[str, Any]:
    """Pré-computa o necessário para o cosseno centralizado em forma fechada.

    Para vetores centralizados c_i = M_i − μ (com M_i já L2-normalizado, ||M_i||=1):
        ‖c_i‖ = √(1 − 2·(M_i·μ) + μ·μ).
    Guardamos μ, d_i = M·μ e μ·μ (tudo minúsculo), evitando a cópia N×D centralizada."""
    mu = M.mean(0).astype(np.float64)
    d = (M @ mu).astype(np.float64)               # M_i·μ para cada i
    mm = float(mu @ mu)
    denom = np.sqrt(np.maximum(1.0 - 2.0 * d + mm, 1e-12))
    return {"mu": mu, "d": d, "mm": mm, "denom": denom}


def _stats_for(engine, tag: str, M: np.ndarray) -> dict[str, Any]:
    key = (id(engine), tag)
    cached = _center_cache.get(key)
    if cached is None:
        cached = _center_stats(M)
        _center_cache[key] = cached
    return cached


def _centered_cos(M: np.ndarray, stats: dict[str, Any], row: int) -> np.ndarray:
    """Cosseno entre o filme `row` e todo o catálogo no espaço **centralizado**.

    ⟨c_a, c_i⟩ = a·M_i − a·μ − μ·M_i + μ·μ, dividido por ‖c_a‖·‖c_i‖."""
    mu, d, mm, denom = stats["mu"], stats["d"], stats["mm"], stats["denom"]
    a = M[row]
    a_mu = float(a @ mu)
    num = (M @ a) - a_mu - d + mm
    denom_a = float(np.sqrt(max(1.0 - 2.0 * a_mu + mm, 1e-12)))
    return num / (denom_a * denom)


def _content_neighbors(engine, tmdb_id: int, n: int) -> list[tuple[int, float]]:
    """Filmes mais próximos por conteúdo (sinopse + temático), espaço centralizado."""
    if engine._embeddings is None:
        return []
    row = engine._row_index.get(int(tmdb_id))
    if row is None:
        return []

    S = engine._embeddings
    score = _centered_cos(S, _stats_for(engine, "syn", S), row)
    if engine._kw_embeddings is not None:
        K = engine._kw_embeddings
        score = score + KW_WEIGHT * _centered_cos(K, _stats_for(engine, "kw", K), row)

    out: list[tuple[int, float]] = []
    for i in np.argsort(score)[::-1]:
        tid = int(engine._movie_ids[i])
        if tid == int(tmdb_id):
            continue  # o próprio filme-semente
        out.append((tid, float(score[i])))
        if len(out) >= n:
            break
    return out


def similar_to(tmdb_id: int, n: int = 15, region: Optional[str] = None,
               provider_ids: Optional[list[int]] = None) -> dict[str, Any]:
    """Recomenda `n` filmes parecidos com `tmdb_id`, fundindo conteúdo + colaborativo.

    Se `provider_ids` for dado, devolve os **N mais parecidos disponíveis** nesses
    serviços de streaming (filtra o ranking completo, não corta a lista exibida).

    Retorna {"seed": {...} | None, "recommendations": [...]}. `seed` é None quando
    o filme não existe no catálogo (o chamador devolve 404)."""
    from retrieval.search_engine import get_engine

    tmdb_id = int(tmdb_id)
    cat = catalog.get_catalog()
    seed = cat.get(tmdb_id)
    if seed is None:
        return {"seed": None, "recommendations": []}

    engine = get_engine()
    filtering = bool(provider_ids)
    # Filtrando por streaming, varremos um ranking bem maior para juntar N disponíveis.
    pool = max(n * 12, 200) if filtering else max(n * 4, 60)
    content = _content_neighbors(engine, tmdb_id, pool)

    collab: list[tuple[int, float]] = []
    try:
        from recommender.collaborative import get_recommender
        collab = get_recommender().item_neighbors(tmdb_id, top=pool)
    except Exception:
        collab = []  # best-effort: sem colaborativo, conteúdo basta

    score: dict[int, float] = defaultdict(float)
    found: dict[int, set] = defaultdict(set)
    for i, (tid, _s) in enumerate(content):
        score[tid] += CONTENT_RRF_W / (RRF_K + i)
        found[tid].add("conteúdo")
    for i, (tid, _s) in enumerate(collab):
        score[tid] += COLLAB_RRF_W / (RRF_K + i)
        found[tid].add("colaborativo")

    ranked_all = sorted((t for t in score if t != tmdb_id), key=lambda t: -score[t])
    if filtering:
        from core import tmdb
        ranked = tmdb.filter_available(ranked_all, provider_ids, region, limit=n)
    else:
        ranked = ranked_all[:n]

    seed_genres = set(catalog.get_movie_genres(tmdb_id))
    recs: list[dict[str, Any]] = []
    for tid in ranked:
        mv = cat.get(tid, {})
        shared = [g for g in catalog.get_movie_genres(tid) if g in seed_genres]
        recs.append({
            "tmdb_id": tid,
            "title": mv.get("title"),
            "release_year": mv.get("release_year"),
            "vote_average": mv.get("vote_average"),
            "overview": (mv.get("overview") or "")[:240],
            "shared_genres": shared[:3],
            "method": " + ".join(sorted(found[tid])),
            "score": round(score[tid], 4),
        })

    return {
        "seed": {
            "tmdb_id": tmdb_id,
            "title": seed.get("title"),
            "release_year": seed.get("release_year"),
        },
        "recommendations": recs,
    }
