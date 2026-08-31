# -*- coding: utf-8 -*-
"""Variantes do pipeline de recuperação por sinopse, para a **tabela de ablação**.

Cada variante recebe `(engine, ctx)` e devolve a **ordenação completa** de
`tmdb_id`s (melhor primeiro). O `ctx` carrega a consulta limpa e o embedding da
consulta, computados uma vez por consulta e reaproveitados entre as variantes.

| chave            | o que é                                                        |
|------------------|----------------------------------------------------------------|
| `bm25`           | só o sinal **lexical** (BM25 Okapi cru sobre as sinopses)      |
| `embedding`      | só o sinal **semântico** (cosseno com o embedding da sinopse)  |
| `thematic`       | só o sinal **temático** (cosseno com o embedding de keywords)  |
| `fusion`         | fusão z-score dos três sinais + prior de popularidade (**pipeline de produção**) |
| `fusion_rerank`  | a fusão acima com o **cross-encoder** re-rankeando o top-`POOL` — variante experimental (off em produção, ver `search_engine.RERANK_ENABLED`) |

Todas operam sobre o mesmo índice, então a comparação isola o efeito de cada
sinal e do re-ranker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from retrieval.search_engine import RERANK_POOL, clean_descriptive_query

POOL = RERANK_POOL


@dataclass
class QueryCtx:
    raw: str
    clean: str
    q_emb: Optional[np.ndarray]


def make_ctx(engine, query: str) -> QueryCtx:
    clean = clean_descriptive_query(query)
    q_emb = engine._encode(clean) if engine._embeddings is not None else None
    return QueryCtx(raw=query, clean=clean, q_emb=q_emb)


def _order_to_ids(engine, order: np.ndarray) -> list[int]:
    ids = engine._movie_ids
    return [int(ids[i]) for i in order]


def _bm25(engine, ctx: QueryCtx) -> list[int]:
    scores = engine._bm25.scores(ctx.clean)
    return _order_to_ids(engine, np.argsort(scores)[::-1])


def _embedding(engine, ctx: QueryCtx) -> list[int]:
    sims = engine._embeddings @ ctx.q_emb
    return _order_to_ids(engine, np.argsort(sims)[::-1])


def _thematic(engine, ctx: QueryCtx) -> list[int]:
    kw_q = engine._keyword_query_emb(ctx.clean, ctx.q_emb)
    sims = engine._kw_embeddings @ kw_q
    return _order_to_ids(engine, np.argsort(sims)[::-1])


def _fusion(engine, ctx: QueryCtx) -> list[int]:
    fused = engine._synopsis_scores(ctx.raw)
    return _order_to_ids(engine, np.argsort(fused)[::-1])


def _fusion_rerank(engine, ctx: QueryCtx) -> list[int]:
    return engine.synopsis_ranked_ids(ctx.raw, rerank=True, pool=POOL)


PIPELINES: dict[str, Callable] = {
    "bm25": _bm25,
    "embedding": _embedding,
    "thematic": _thematic,
    "fusion": _fusion,
    "fusion_rerank": _fusion_rerank,
}

# Rótulos legíveis para a tabela (ordem = ordem de exibição).
PIPELINE_LABELS: list[tuple[str, str]] = [
    ("bm25", "BM25 puro (lexical)"),
    ("embedding", "Só embedding (semântico)"),
    ("thematic", "Só temático (keywords)"),
    ("fusion", "Fusão (produção)"),
    ("fusion_rerank", f"Fusão + re-ranker (pool {POOL})"),
]

# Variantes que dependem do modelo de embeddings / cross-encoder (mais lentas).
NEEDS_EMBEDDINGS = {"embedding", "thematic", "fusion", "fusion_rerank"}
NEEDS_RERANKER = {"fusion_rerank"}


def rank_of(ranked_ids: list[int], target_id: int) -> Optional[int]:
    """Posição 1-based de `target_id` em `ranked_ids`, ou None se não estiver lá."""
    try:
        return ranked_ids.index(int(target_id)) + 1
    except ValueError:
        return None
