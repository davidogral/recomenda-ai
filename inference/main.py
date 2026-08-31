# -*- coding: utf-8 -*-
"""Serviço de **inferência** (FastAPI) — a camada de ML do RecomendAI isolada
atrás de um contrato HTTP tipado (Pydantic) + OpenAPI automático em `/docs`.

Rotas:
  POST /v1/search_combined        busca facetada por sinopse/nome/pessoa
  POST /v1/similar                filmes parecidos (conteúdo + colaborativo)
  POST /v1/recommend_from_profile recomendação a partir de um perfil de notas
  GET  /health                    prontidão + estado do cache
  GET  /metrics                   métricas Prometheus (mesmo registry da API)

A API (`app.py`, Flask) fala com este serviço quando `RECOMENDAI_INFERENCE_URL`
está setado (ver `core/inference_client.py`); é o ponto de corte para trocar
esta camada por outra implementação (ex.: Rust) medindo só a diferença.

Rodar:  uvicorn inference.main:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from core import metrics as _m

_engine = None


def engine():
    global _engine
    if _engine is None:
        from retrieval.search_engine import RERANK_ENABLED, SearchEngine

        _engine = SearchEngine(rerank=RERANK_ENABLED).warmup()
    return _engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("RECOMENDAI_NO_WARMUP", "0").lower() not in ("1", "true", "yes"):
        engine()  # pré-carrega modelos no boot, não na 1ª requisição
    yield


app = FastAPI(title="RecomendAI Inference", version="1", lifespan=lifespan)

try:  # /metrics compartilha o registry default com core.metrics
    from prometheus_client import make_asgi_app

    app.mount("/metrics", make_asgi_app())
except Exception:  # pragma: no cover
    pass


# ------------------------------------------------------------------- modelos
class Filters(BaseModel):
    year: Optional[int] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    genre: Optional[str] = None
    language: Optional[str] = None


class SearchCombinedRequest(BaseModel):
    query: str = ""
    director: str = ""
    actor: str = ""
    n: int = Field(12, ge=1, le=50)
    filters: Optional[Filters] = None


class SearchResponse(BaseModel):
    count: int
    results: list[dict[str, Any]]


class SimilarRequest(BaseModel):
    movie_id: int
    n: int = Field(12, ge=1, le=50)
    region: Optional[str] = None
    provider_ids: Optional[list[int]] = None


class RecommendProfileRequest(BaseModel):
    detail: list[dict[str, Any]]
    n: int = Field(20, ge=1, le=60)
    region: Optional[str] = None
    provider_ids: Optional[list[int]] = None


# -------------------------------------------------------------------- rotas
@app.post("/v1/search_combined", response_model=SearchResponse)
def search_combined(req: SearchCombinedRequest) -> SearchResponse:
    t0 = time.perf_counter()
    f = req.filters.model_dump(exclude_none=True) if req.filters else None
    res = engine().search_combined(query=req.query, director=req.director, actor=req.actor, n=req.n, filters=f or None)
    _m.observe_stage("total", time.perf_counter() - t0)
    return SearchResponse(count=len(res), results=res)


@app.post("/v1/similar")
def similar(req: SimilarRequest) -> dict[str, Any]:
    from recommender.similar import similar_to

    engine()  # garante índice carregado
    return similar_to(req.movie_id, n=req.n, region=req.region, provider_ids=req.provider_ids)


@app.post("/v1/recommend_from_profile")
def recommend_from_profile(req: RecommendProfileRequest) -> dict[str, Any]:
    from recommender.profile import recommend_from_profile as _r

    engine()
    return _r(req.detail, n=req.n, region=req.region, provider_ids=req.provider_ids)


@app.get("/health")
def health() -> dict[str, Any]:
    e = engine()
    return {"status": "ok", "has_synopsis_index": e.has_synopsis_index, "query_cache": e.query_cache_stats()}
