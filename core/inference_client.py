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


# --------------------------------------------------------------- operações
def search_combined(
    query: str = "", director: str = "", actor: str = "", n: int = 12, filters: Optional[dict] = None
) -> list[dict]:
    query, pistas_pessoa, plot_lexical_weight, entity_weight = _understand_and_rewrite(query)
    if is_remote():
        out = _post(
            "/v1/search_combined",
            {"query": query, "director": director, "actor": actor, "n": n, "filters": filters or None},
        )
        return out["results"]
    from retrieval.search_engine import get_engine

    return get_engine().search_combined(
        query=query, director=director, actor=actor, n=n, filters=filters or None,
        pistas_pessoa=pistas_pessoa, plot_lexical_weight=plot_lexical_weight, entity_weight=entity_weight,
    )


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
