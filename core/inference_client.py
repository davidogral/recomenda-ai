# -*- coding: utf-8 -*-
"""Costura API ↔ serviço de inferência.

Se `RECOMENDAI_INFERENCE_URL` está setado, as operações pesadas de ML (busca por
sinopse, "parecidos", recomendação de perfil) vão por **HTTP** para o serviço
`inference/`; senão, rodam **no mesmo processo** (comportamento padrão, dev).

É o ponto de corte que permite reimplementar `inference/` em outra linguagem
(ex.: Rust) sem tocar na API — bastando manter o mesmo contrato JSON.
"""

from __future__ import annotations

import os
from typing import Any, Optional

INFERENCE_URL = os.environ.get("RECOMENDAI_INFERENCE_URL", "").rstrip("/")
_TIMEOUT = float(os.environ.get("RECOMENDAI_INFERENCE_TIMEOUT", "15"))

_client = None


def is_remote() -> bool:
    return bool(INFERENCE_URL)


def _post(path: str, payload: dict) -> Any:
    global _client
    import httpx

    if _client is None:
        _client = httpx.Client(base_url=INFERENCE_URL, timeout=_TIMEOUT)
    try:
        r = _client.post(path, json=payload)
    except httpx.HTTPError as e:  # rede: sobe como RuntimeError -> a rota devolve 503
        raise RuntimeError(f"serviço de inferência indisponível: {e}") from e
    if r.status_code >= 500:
        raise RuntimeError(f"serviço de inferência falhou ({r.status_code}): {r.text[:200]}")
    if r.status_code >= 400:
        raise ValueError(r.json().get("detail") or r.text)
    return r.json()


# Consulta com até tantas palavras nem passa pelo Groq: é o mesmo limiar do
# canal `entity` (ver _ENTITY_MAX_QUERY_TOKENS em search_engine.py) — nomes/
# personagens curtos já são bem servidos pelo fuzzy-match tolerante a erro de
# grafia, e "consertar" a grafia (ex.: "Mcquen"->"McQueen") pode ATRAPALHAR:
# medido que isso faz "Mcquen" (Pixar) perder pra "Alexandre McQueen"
# (documentário real) no canal de nome, porque a grafia corrigida casa melhor
# com o título de verdade que o typo original não confundia.
_REWRITE_MIN_WORDS = 7


def _rewrite_query(query: str) -> str:
    """Passa a consulta pelo entendimento via LLM (Groq) antes da busca.

    NUNCA substitui o texto — só ACRESCENTA. Medido: substituir por uma
    versão mais curta (`pistas_objeto` sozinho, ou `consulta_reescrita`)
    encolhe a consulta o bastante pra disparar sem querer as heurísticas de
    "consulta curta = provável título" (`_adaptive_name_weight`,
    `_best_title_match`) — uma consulta de 16 palavras sobre um musical virou
    só "bar" e passou a casar com qualquer título que contém essa substring
    ("O Bar", "Barfuß"...). Mantendo a consulta original e só emendando
    termo novo, o tamanho/contexto que já funciona bem não muda.

    Qualquer falha (sem chave, rede, timeout, consulta curta) devolve a
    consulta original sem alterar nada."""
    q = (query or "").strip()
    if not q or len(q.split()) <= _REWRITE_MIN_WORDS:
        return query
    from core import metrics, query_llm

    with metrics.stage_timer("query_llm"):
        plan = query_llm.understand(q)
    if not plan.ok or plan.tipo != "objeto" or not plan.pistas_objeto:
        return query
    ql = q.lower()
    extra = [t for t in plan.pistas_objeto if t.lower() not in ql]
    return f"{q} {' '.join(extra)}".strip() if extra else query


# --------------------------------------------------------------- operações
def search_combined(
    query: str = "", director: str = "", actor: str = "", n: int = 12, filters: Optional[dict] = None
) -> list[dict]:
    query = _rewrite_query(query)
    if is_remote():
        out = _post(
            "/v1/search_combined",
            {"query": query, "director": director, "actor": actor, "n": n, "filters": filters or None},
        )
        return out["results"]
    from retrieval.search_engine import get_engine

    return get_engine().search_combined(query=query, director=director, actor=actor, n=n, filters=filters or None)


def similar(movie_id: int, n: int = 12, region: Optional[str] = None, provider_ids: Optional[list[int]] = None) -> dict:
    if is_remote():
        return _post("/v1/similar", {"movie_id": movie_id, "n": n, "region": region, "provider_ids": provider_ids})
    from recommender.similar import similar_to

    return similar_to(movie_id, n=n, region=region, provider_ids=provider_ids)


def recommend_from_profile(
    detail: list[dict], n: int = 20, region: Optional[str] = None, provider_ids: Optional[list[int]] = None
) -> dict:
    if is_remote():
        return _post(
            "/v1/recommend_from_profile", {"detail": detail, "n": n, "region": region, "provider_ids": provider_ids}
        )
    from recommender.profile import recommend_from_profile as _local

    return _local(detail, n=n, region=region, provider_ids=provider_ids)
