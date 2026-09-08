# -*- coding: utf-8 -*-
"""Entendimento de consulta via LLM (Groq, grátis) — roda em TODA busca de texto.

Groq (`openai/gpt-oss-20b`, gratuito) decompõe a consulta em: tipo (pessoa |
objeto | genérico), pistas buscáveis e uma reescrita mais direta (menos
"narrativa", mais termos concretos que a fusão de sinais já sabe casar).

Nunca bloqueia nem quebra a busca: qualquer falha (sem chave, rede, timeout,
JSON malformado) cai silenciosamente em `QueryPlan()` — o chamador trata isso
como "sem info extra", nunca como erro. Cacheado em disco por texto de
consulta (permanente — a mesma frase sempre decompõe igual) para não gastar
a cota grátis em consultas repetidas.

Medido 2026-09-08: reasoning_effort="low" é essencial — sem isso o modelo
(é um modelo de "raciocínio") pode gastar todo o `max_tokens` pensando e
devolver conteúdo vazio. ~0.3-0.6s por chamada não cacheada. Cota grátis da
chave: 1000 req/dia, 8000 tokens/min — folgada pro tráfego do site, mas o
cache local evita depender disso continuar assim.

O canal que usa `pistas_pessoa` (busca em bio de elenco/diretor) ainda não
existe — por enquanto essas pistas só são capturadas/logadas; a consulta
reescrita já ajuda um pouco mesmo em consultas de pessoa (é a mesma pergunta,
só mais limpa), mas o ganho de verdade pra trivia composta espera o índice de
bio (`core.enrich --wikipedia-people` + chunking, ver retrieval/index_builder).
"""

from __future__ import annotations

import atexit
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import requests

from core import db, tmdb

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("RECOMENDAI_GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_TIMEOUT = float(os.environ.get("RECOMENDAI_GROQ_TIMEOUT", "4.0"))
ENABLED = os.environ.get("RECOMENDAI_QUERY_LLM", "1").strip().lower() not in ("0", "false", "no")

_URL = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM = """Voce decompoe uma consulta de busca de filme em pistas atomicas e
buscaveis, e reescreve a consulta de forma mais direta e facil de casar por
busca textual/semantica (menos "narrativa", mais termos concretos).

IMPORTANTE: sua tarefa e SO DECOMPOR — nunca identificar quem e a pessoa ou
qual e o filme. Mesmo que voce reconheca de quem se trata, NAO escreva esse
nome em lugar nenhum da resposta — isso e proibido e so desperdica seu
orcamento de raciocinio tentando confirmar um palpite. So extraia os fatos
que a PRÓPRIA CONSULTA cita, cada um virando uma pista independente.

Responda SOMENTE em JSON, sem comentario, no formato:
{"tipo": "pessoa"|"objeto"|"generico",
 "consulta_reescrita": "...",
 "consulta_ingles": "...",
 "pistas_pessoa": ["fato buscavel 1", ...],
 "pistas_objeto": ["termo 1", ...]}
"pistas_pessoa": fatos sobre UMA pessoa (ator/diretor) citados na consulta -
premio, relacao pessoal, hobby, epoca - cada um buscavel sozinho numa
biografia. ESCREVA "pistas_pessoa" EM INGLES (ex.: "knighted by the Queen",
nao "condecorado pela rainha") - a biografia buscada e da Wikipedia em
ingles, e o termo literal em ingles ("knight", "Queen") bate no texto onde a
traducao para portugues nao bate nada. Se a consulta cita 2+ fatos sobre uma
pessoa, "tipo":"pessoa" e SEMPRE liste todos os fatos em pistas_pessoa -
nunca devolva a lista vazia so porque voce nao sabe (ou nao pode dizer) quem
e a pessoa. "pistas_objeto" fica no idioma da consulta (nome proprio/veiculo
sobrevive a traducao). "consulta_reescrita": a MESMA busca, so mais direta -
nunca invente fato novo. "consulta_ingles": traducao literal da consulta
inteira pro ingles (mesmo raciocinio de pistas_pessoa - o enredo detalhado
buscado tambem e da Wikipedia em ingles). Se a consulta ja e direta/
generica, sem pista especifica de pessoa ou objeto, "tipo":"generico" e as
duas listas vazias (consulta_reescrita/consulta_ingles ainda valem)."""

_TIPOS = ("pessoa", "objeto", "generico")
_cache: Optional[tmdb._JsonCache] = None


def _get_cache() -> tmdb._JsonCache:
    global _cache
    if _cache is None:
        _cache = tmdb._JsonCache(os.path.join(db._PROJECT_ROOT, "data", "tmdb_cache", "query_llm.json"))
    return _cache


@atexit.register
def _flush_cache() -> None:
    if _cache is not None:
        _cache.flush()


@dataclass
class QueryPlan:
    tipo: str = "generico"
    consulta_reescrita: str = ""
    consulta_ingles: str = ""
    pistas_pessoa: list[str] = field(default_factory=list)
    pistas_objeto: list[str] = field(default_factory=list)
    ok: bool = False  # True só se o Groq respondeu e foi parseado com sucesso


def is_configured() -> bool:
    return ENABLED and bool(GROQ_API_KEY)


def _plan_from_dict(data: dict) -> QueryPlan:
    tipo = data.get("tipo") if data.get("tipo") in _TIPOS else "generico"
    return QueryPlan(
        tipo=tipo,
        consulta_reescrita=str(data.get("consulta_reescrita") or "").strip()[:300],
        consulta_ingles=str(data.get("consulta_ingles") or "").strip()[:300],
        pistas_pessoa=[str(x).strip() for x in (data.get("pistas_pessoa") or []) if str(x).strip()][:6],
        pistas_objeto=[str(x).strip() for x in (data.get("pistas_objeto") or []) if str(x).strip()][:6],
        ok=True,
    )


def understand(query: str) -> QueryPlan:
    """Decompõe/reescreve `query`. Nunca levanta — falha vira QueryPlan()
    (tipo="generico", ok=False), tratado pelo chamador como "sem info extra"."""
    q = (query or "").strip()
    if not q or not is_configured():
        return QueryPlan()

    key = q.lower()
    cache = _get_cache()
    if key in cache:
        cached = cache.get(key)
        return _plan_from_dict(cached) if cached else QueryPlan()

    plan_dict: Optional[dict] = None
    try:
        r = requests.post(
            _URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": q}],
                "temperature": 0,
                "max_tokens": 800,
                "reasoning_effort": "low",
            },
            timeout=GROQ_TIMEOUT,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            plan_dict = json.loads(content)
    except Exception:
        plan_dict = None

    if plan_dict is None:
        # Falha de rede/timeout/parse é TRANSIENTE — não cacheia, senão uma
        # instabilidade passageira do Groq vira "sem plano" permanente pra
        # essa consulta. Só o sucesso (mesmo com tipo=generico) é cacheado.
        return QueryPlan()
    cache.set(key, plan_dict)
    return _plan_from_dict(plan_dict)


# ---------------------------------------------------------------------------
# Reranking: verifica se o TOPO da fusão (similaridade de vetor/termo) bate
# de verdade com a descrição, lendo a sinopse de cada candidato — ataca o
# tipo de erro que nenhum sinal de similaridade resolve (fato composto,
# objeto específico citado uma vez). Medido 2026-09-08: acerta "Sonho dentro
# do sonho"->A Origem e "chove hambúrguer"->Tá Chovendo Hambúrguer lendo só a
# sinopse da TMDB — e importante, RECUSA (não inventa) quando a sinopse
# genuinamente não tem o fato citado, em vez de forçar um palpite.
#
# Só promove com confiança >= "media" — nunca troca o topo baseado num
# palpite de baixa confiança, então o pior caso de uma falha/recusa é não
# mudar nada (a fusão já ordenou razoavelmente).
_RERANK_SYSTEM = """Voce recebe uma descricao de busca de filme e uma lista
numerada de filmes candidatos (titulo, ano, sinopse). Escolha qual candidato
REALMENTE corresponde ao que a descricao pede, lendo a sinopse de cada um -
nao pelo tema geral, pelo FATO especifico citado na descricao.
Responda SOMENTE em JSON, sem comentario:
{"escolha": N, "confianca": "alta"|"media"|"baixa"}
N e o numero do candidato (1-indexado). Se NENHUM candidato bate de verdade
com o fato especifico da descricao, {"escolha": null, "confianca": null} -
nao force um palpite so pra responder algo."""

RERANK_POOL = int(os.environ.get("RECOMENDAI_RERANK_LLM_POOL", "30"))
_rerank_cache: Optional[tmdb._JsonCache] = None


def _get_rerank_cache() -> tmdb._JsonCache:
    global _rerank_cache
    if _rerank_cache is None:
        _rerank_cache = tmdb._JsonCache(os.path.join(db._PROJECT_ROOT, "data", "tmdb_cache", "rerank_llm.json"))
    return _rerank_cache


@atexit.register
def _flush_rerank_cache() -> None:
    if _rerank_cache is not None:
        _rerank_cache.flush()


def _rerank_cache_key(query: str, candidates: list[dict]) -> str:
    import hashlib

    ids_part = ",".join(str(c.get("tmdb_id")) for c in candidates)
    raw = f"{query.strip().lower()}|{ids_part}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def rerank_pick(query: str, candidates: list[dict]) -> Optional[int]:
    """`candidates`: [{"tmdb_id", "title", "year", "overview"}, ...] já na
    ordem da fusão. Devolve o tmdb_id escolhido pela LLM (só com confiança
    "alta"/"media"), ou None — sem candidato, sem chave, falha de rede/
    timeout/parse, palpite de baixa confiança, ou índice fora da lista.
    Nunca levanta.

    Achado 2026-09-08: SEM cache, a mesma consulta com os MESMOS candidatos
    dava resposta diferente a cada chamada (8 chamadas idênticas -> 3
    respostas distintas) — mesmo com temperature=0, a Groq não é
    perfeitamente determinística (efeito de lote conhecido em inferência
    compartilhada). Cacheado por (consulta, IDs dos candidatos NA ORDEM) —
    a mesma busca sempre devolve a mesma resposta a partir da 1ª chamada
    real, e evita gastar a cota grátis roletando de novo. Só o SUCESSO
    (mesmo "nenhum candidato bate") é cacheado — falha de rede/timeout/parse
    é transiente, nunca cacheada."""
    if not candidates or not is_configured():
        return None
    cache = _get_rerank_cache()
    key = _rerank_cache_key(query, candidates)
    if key in cache:
        return cache.get(key)

    lines = [
        f"{i}. {c.get('title') or '?'} ({c.get('year') or '?'}): {(c.get('overview') or '').strip()}"
        for i, c in enumerate(candidates, 1)
    ]
    user = "Descrição: " + query + "\n\nCandidatos:\n" + "\n".join(lines)
    try:
        r = requests.post(
            _URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "system", "content": _RERANK_SYSTEM}, {"role": "user", "content": user}],
                "temperature": 0,
                "max_tokens": 600,
                "reasoning_effort": "low",
            },
            timeout=GROQ_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception:
        return None

    pick_id: Optional[int] = None
    if data.get("confianca") in ("alta", "media"):
        try:
            idx = int(data.get("escolha")) - 1
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(candidates):
            pick_id = candidates[idx].get("tmdb_id")
    cache.set(key, pick_id)
    return pick_id
