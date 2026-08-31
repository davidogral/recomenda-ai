# -*- coding: utf-8 -*-
"""Métricas Prometheus do RecomendAI.

Expõe, em `/metrics` (tanto na API Flask quanto no serviço de inferência):

  recomendaai_requests_total{endpoint,method,status}   contador de requisições
  recomendaai_errors_total{endpoint}                   erros não tratados
  recomendaai_request_seconds{endpoint}                histograma ponta-a-ponta
  recomendaai_stage_seconds{stage}                     histograma por etapa
                                                       (retrieval | rerank | encode | total)
  recomendaai_query_cache_events_total{result}         hit | miss do cache de embedding
  recomendaai_tmdb_calls_total{result}                 ok | error | capped
  recomendaai_process_rss_bytes                        memória residente do processo

Se `prometheus_client` não estiver instalado, tudo vira no-op — o app roda igual.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

try:  # prometheus é opcional
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _ENABLED = True
except Exception:  # pragma: no cover
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"


class _Noop:
    def labels(self, *a, **k):
        return self

    def inc(self, *a, **k):
        pass

    def observe(self, *a, **k):
        pass

    def set(self, *a, **k):
        pass

    def set_function(self, *a, **k):
        pass


if _ENABLED:
    _LAT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    REQUESTS = Counter("recomendaai_requests_total", "Requisições HTTP", ["endpoint", "method", "status"])
    ERRORS = Counter("recomendaai_errors_total", "Erros não tratados numa requisição", ["endpoint"])
    REQUEST_SECONDS = Histogram(
        "recomendaai_request_seconds", "Latência ponta-a-ponta", ["endpoint"], buckets=_LAT_BUCKETS
    )
    STAGE_SECONDS = Histogram(
        "recomendaai_stage_seconds", "Latência por etapa do pipeline", ["stage"], buckets=_LAT_BUCKETS
    )
    QUERY_CACHE = Counter(
        "recomendaai_query_cache_events_total", "Eventos do cache de embedding da consulta", ["result"]
    )
    TMDB_CALLS = Counter("recomendaai_tmdb_calls_total", "Chamadas à API da TMDB", ["result"])
    RSS_BYTES = Gauge("recomendaai_process_rss_bytes", "Memória residente do processo")
    try:
        import psutil

        _proc = psutil.Process()
        RSS_BYTES.set_function(lambda: float(_proc.memory_info().rss))
    except Exception:  # pragma: no cover
        pass
else:  # pragma: no cover
    REQUESTS = ERRORS = REQUEST_SECONDS = STAGE_SECONDS = QUERY_CACHE = TMDB_CALLS = RSS_BYTES = _Noop()


@contextmanager
def stage_timer(stage: str):
    """`with stage_timer("retrieval"): ...` → observa em recomendaai_stage_seconds."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        STAGE_SECONDS.labels(stage=stage).observe(time.perf_counter() - t0)


def observe_stage(stage: str, seconds: float) -> None:
    STAGE_SECONDS.labels(stage=stage).observe(seconds)


def query_cache_event(hit: bool) -> None:
    QUERY_CACHE.labels(result="hit" if hit else "miss").inc()


def tmdb_call(result: str) -> None:  # "ok" | "error" | "capped"
    TMDB_CALLS.labels(result=result).inc()


def render_latest() -> tuple[bytes, str]:
    """(corpo, content-type) para a rota /metrics."""
    if not _ENABLED:
        return b"# prometheus_client ausente\n", CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


# --------------------------------------------------------------------- Flask
def init_flask(app) -> None:
    """Instrumenta uma app Flask: conta requisições/erros/latência e serve /metrics."""
    from flask import g, request

    @app.before_request
    def _m_start():
        g._m_t0 = time.perf_counter()

    @app.after_request
    def _m_end(resp):
        ep = request.endpoint or request.path
        REQUESTS.labels(endpoint=ep, method=request.method, status=resp.status_code).inc()
        t0 = getattr(g, "_m_t0", None)
        if t0 is not None:
            REQUEST_SECONDS.labels(endpoint=ep).observe(time.perf_counter() - t0)
        return resp

    @app.teardown_request
    def _m_err(exc):
        if exc is not None:
            ERRORS.labels(endpoint=(request.endpoint or request.path)).inc()

    @app.route("/metrics")
    def _metrics():
        body, ctype = render_latest()
        return app.response_class(body, mimetype=ctype)
