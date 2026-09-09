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


# Achado 2026-09-08: existia um limiar de palavras aqui só pra evitar que
# `consulta_reescrita` (substituição) encolhesse a consulta e disparasse por
# engano "consulta curta = título" — mas a substituição já foi removida (só
# ACRESCENTA, ver abaixo), então esse risco não existe mais. Confirmado:
# "Mcquen"/"Brian oconner"/"Toreto"/"Roman pearce"/"Baba yaga"/"Bastardos"
# continuam classificando como "pessoa"/"generico" (nunca "objeto"), então
# nunca disparam o acréscimo — removido o limiar, a maioria das consultas
# curtas de OBJETO do log real ("Dogde charger preto", 3 palavras) passa a
# se beneficiar, que antes nem chegava a passar pelo Groq.

# Peso do canal léxico de enredo (`RECOMENDAI_PLOT_BM25_WEIGHT`, padrão 0.1)
# SÓ pra consulta que o Groq já classificou como "objeto" — o peso global
# fica baixo pra não arriscar as demais consultas, mas isolado numa consulta
# já sabida como objeto/veículo, um peso maior vale a pena. Medido no split
# `object` (só essas consultas): 0.3 é onde o ganho agregado pára de subir
# (0.1→0.325, 0.2→0.330, 0.3→0.330, 0.4→0.320 nDCG@10) — e no caso real
# "Dogde charger preto" tira o filme certo do rank 10 (fora do top 10) pro
# rank 1; "Arrancada com skyline azul e prata" de rank 36 pro rank 2.
OBJECT_PLOT_LEXICAL_WEIGHT = float(os.environ.get("RECOMENDAI_OBJECT_PLOT_LEXICAL_WEIGHT", "0.3"))


def _understand_and_rewrite(query: str) -> tuple[str, list, Optional[float], Optional[float]]:
    """Passa a consulta pelo entendimento via LLM (Groq) antes da busca.
    Devolve (consulta_pra_buscar, pistas_pessoa, plot_lexical_weight, entity_weight).

    NUNCA substitui o texto — só ACRESCENTA (ver nota acima sobre por que).
    `pistas_pessoa` (fatos biográficos, tipo="pessoa") vão pro canal
    `person_match` do search_engine — aqui só passam adiante, não alteram o
    texto da busca. `plot_lexical_weight`/`entity_weight` só vêm preenchidos
    (não-None) pra consulta tipo="objeto": sobe o léxico de enredo, desliga o
    de personagem — achado testando "Dogde charger preto": o termo
    acrescentado ("Dodge Charger") dava falso-positivo no canal de
    personagem (afinado pra consulta curta de NOME, não filtra "isso não é
    nome de gente").

    Qualquer falha (sem chave, rede, timeout, consulta vazia) devolve a
    consulta original sem alterar nada e os dois pesos em None."""
    q = (query or "").strip()
    if not q:
        return query, [], None, None
    from core import metrics, query_llm

    with metrics.stage_timer("query_llm"):
        plan = query_llm.understand(q)
    if not plan.ok:
        return query, [], None, None
    out = query
    plot_lexical_weight = entity_weight = None
    if plan.tipo == "objeto" and plan.pistas_objeto:
        ql = q.lower()
        extra = [t for t in plan.pistas_objeto if t.lower() not in ql]
        out = f"{q} {' '.join(extra)}".strip() if extra else query
        plot_lexical_weight = OBJECT_PLOT_LEXICAL_WEIGHT
        entity_weight = 0.0
    return out, (plan.pistas_pessoa if plan.tipo == "pessoa" else []), plot_lexical_weight, entity_weight


# Reranking via LLM (ver core.query_llm.rerank_confirm): lê a sinopse do topo
# da fusão e PROMOVE os candidatos que confirma com confiança, pode ser mais
# de um, na ordem de confiança devolvida; o resto mantém a ordem original da
# fusão entre si. Versão original (2026-09-08, 1 palpite só, gpt-oss-20b,
# pool=30): hard 0.473->0.506, sem mudança relevante nos outros 4 splits.
# 2026-09-09: trocado pra "lista de confirmados" (motivo e achados em
# core/query_llm.py junto do prompt), modelo de reranking trocado pra
# qwen/qwen3.8-27b (sem raciocínio oculto, cota diária própria) e pool
# reduzido pra 20 (RERANK_POOL). Remedido nos 3 splits mais sensíveis a
# ambiguidade: hard 0.473->0.654, object 0.325->0.468, entity 0.778->0.853.
RERANK_LLM_ENABLED = os.environ.get("RECOMENDAI_RERANK_LLM", "1").strip().lower() not in ("0", "false", "no")


def _llm_rerank(query: str, results: list[dict]) -> list[dict]:
    """Promove os candidatos que a LLM confirma com confiança (pode ser mais
    de um) na ordem de confiança devolvida — o resto mantém a ordem original
    da fusão entre si. Pior caso (sem chave, falha, nada confirmado): devolve
    `results` inalterado, a fusão já ordenou razoável.

    Usa a sinopse INTEIRA do catálogo, não a `overview` de `results` (essa já
    vem cortada em 240 chars pra exibição — cortar antes de mandar pra LLM
    corre o mesmo risco medido no protótipo: o fato relevante às vezes só
    aparece depois do corte)."""
    if not query or len(results) < 2:
        return results
    from core import catalog, metrics, query_llm

    candidates = [
        {"tmdb_id": r.get("tmdb_id"), "title": r.get("title"), "year": r.get("release_year"),
         "overview": (catalog.get_movie(r.get("tmdb_id")) or {}).get("overview") or r.get("overview")}
        for r in results
    ]
    with metrics.stage_timer("rerank_llm"):
        picks = query_llm.rerank_confirm(query, candidates)
    if not picks:
        return results
    by_id = {r.get("tmdb_id"): r for r in results}
    promoted = [by_id[tid] for tid in picks if tid in by_id]
    if not promoted:
        return results
    promoted_ids = {r.get("tmdb_id") for r in promoted}
    return promoted + [r for r in results if r.get("tmdb_id") not in promoted_ids]


# --------------------------------------------------------------- operações
def search_combined(
    query: str = "", director: str = "", actor: str = "", n: int = 12, filters: Optional[dict] = None
) -> list[dict]:
    query, pistas_pessoa, plot_lexical_weight, entity_weight = _understand_and_rewrite(query)
    from core import query_llm

    fetch_n = max(n, query_llm.RERANK_POOL) if RERANK_LLM_ENABLED else n
    if is_remote():
        out = _post(
            "/v1/search_combined",
            {"query": query, "director": director, "actor": actor, "n": fetch_n, "filters": filters or None},
        )
        results = out["results"]
    else:
        from retrieval.search_engine import get_engine

        results = get_engine().search_combined(
            query=query, director=director, actor=actor, n=fetch_n, filters=filters or None,
            pistas_pessoa=pistas_pessoa, plot_lexical_weight=plot_lexical_weight, entity_weight=entity_weight,
        )
    if RERANK_LLM_ENABLED and query:
        results = _llm_rerank(query, results)
    return results[:n]


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
