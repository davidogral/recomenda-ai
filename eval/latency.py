# -*- coding: utf-8 -*-
"""Perfil de latência do pipeline de busca por sinopse, **etapa a etapa**.

    .venv/bin/python -m eval.latency                    # split de teste, 3 repetições
    .venv/bin/python -m eval.latency --repeats 5
    .venv/bin/python -m eval.latency --rerank-pool 50   # inclui a etapa do cross-encoder

Reporta p50/p95/p99 (ms) de cada etapa e do pipeline inteiro, mais o **RSS** do
processo depois de carregar o modelo. As etapas medidas (mesma ordem do
`_synopsis_ranked`):

  clean          limpeza da consulta (muletas iniciais)
  encode_cold    embedding da consulta — 1ª vez (forward do transformer)
  encode_warm    embedding da consulta — 2ª vez (acerto no cache LRU)
  bm25           BM25 sobre 22k sinopses
  emb_matmul     produto (22k×d) · (d) — sinal semântico
  kw_matmul      produto (22k×d) · (d) — sinal temático
  fuse_argsort   z-score + ReLU + soma + prior + argsort
  rerank         cross-encoder no top-pool (só com --rerank-pool > 0)
  search_total   engine.search(q, mode="synopsis") ponta a ponta (cache quente)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import datetime, timezone

os.environ.setdefault("RECOMENDAI_TMDB_NAMES", "0")

import numpy as np

from eval.dataset import dataset_sha1, load_queries
from eval.run import _engine_meta, _git_commit

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
RESULTS_DIR = os.path.join(_HERE, "results")


def _rss_mb() -> float:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1e6 if peak > 1e7 else 1e3), 1)  # macOS: bytes; linux: kB


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def _stats(xs: list[float]) -> dict:
    return {
        "p50": round(_pct(xs, 50), 2),
        "p95": round(_pct(xs, 95), 2),
        "p99": round(_pct(xs, 99), 2),
        "mean": round(statistics.mean(xs), 2) if xs else 0.0,
        "n": len(xs),
    }


def profile(split: str, repeats: int, rerank_pool: int, quiet: bool) -> dict:
    from retrieval.search_engine import RERANK_BLEND, SearchEngine, clean_descriptive_query

    rss_before = _rss_mb()
    engine = SearchEngine(rerank=rerank_pool > 0).warmup(reranker=rerank_pool > 0)
    rss_after = _rss_mb()
    queries = [q.query for q in load_queries(split)]

    stages: dict[str, list[float]] = {
        k: []
        for k in (
            "clean",
            "encode_cold",
            "encode_warm",
            "bm25",
            "emb_matmul",
            "kw_matmul",
            "fuse_argsort",
            "search_total",
        )
    }
    if rerank_pool > 0:
        stages["rerank"] = []

    reranker = engine._get_reranker() if rerank_pool > 0 else None

    def _t(fn):
        t0 = time.perf_counter()
        out = fn()
        return (time.perf_counter() - t0) * 1000.0, out

    for rep in range(repeats):
        if not quiet:
            print(f"  · passada {rep + 1}/{repeats}", end="\r", flush=True)
        for q in queries:
            dt, cq = _t(lambda: clean_descriptive_query(q))
            stages["clean"].append(dt)

            engine._query_emb_cache.pop(cq, None)  # garante MISS
            dt, q_emb = _t(lambda: engine._encode(cq))
            stages["encode_cold"].append(dt)
            dt, _ = _t(lambda: engine._encode(cq))  # agora HIT no LRU
            stages["encode_warm"].append(dt)

            dt, _ = _t(lambda: engine._bm25.scores(cq))  # type: ignore
            stages["bm25"].append(dt)
            dt, _ = _t(lambda: engine._embeddings @ q_emb)
            stages["emb_matmul"].append(dt)
            kw_q = engine._keyword_query_emb(cq, q_emb)
            dt, _ = _t(lambda: engine._kw_embeddings @ kw_q)
            stages["kw_matmul"].append(dt)

            def _fuse_argsort():
                fused = engine._synopsis_scores(q)
                return np.argsort(fused)[::-1]

            dt, order = _t(_fuse_argsort)
            stages["fuse_argsort"].append(dt)

            if reranker is not None:
                ids = [int(engine._movie_ids[i]) for i in order[:rerank_pool]]
                retr = {t: 0.0 for t in ids}
                dt, _ = _t(lambda: reranker.rerank(q, ids, engine._text_for, retr_scores=retr, blend=RERANK_BLEND))
                stages["rerank"].append(dt)

            dt, _ = _t(lambda: engine.search(q, mode="synopsis", n=10, explain=False))
            stages["search_total"].append(dt)

    if not quiet:
        print(" " * 40, end="\r")

    return {
        "run": {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_commit": _git_commit(),
            "mode": "latency",
            "split": split,
            "n_queries": len(queries),
            "repeats": repeats,
            "rerank_pool": rerank_pool,
            "dataset_sha1": dataset_sha1(),
            "engine": _engine_meta(),
            "device": __import__("core.device", fromlist=["get_device"]).get_device(),
            "rss_mb": {
                "before_load": rss_before,
                "after_load": rss_after,
                "model_delta": round(rss_after - rss_before, 1),
            },
            "query_cache": engine.query_cache_stats(),
        },
        "stages": {k: _stats(v) for k, v in stages.items()},
    }


_STAGE_ORDER = [
    "clean",
    "encode_cold",
    "encode_warm",
    "bm25",
    "emb_matmul",
    "kw_matmul",
    "fuse_argsort",
    "rerank",
    "search_total",
]


def render(payload: dict) -> str:
    st = payload["stages"]
    lines = ["| etapa | p50 (ms) | p95 (ms) | p99 (ms) | média |", "|---|---|---|---|---|"]
    for k in _STAGE_ORDER:
        if k not in st:
            continue
        s = st[k]
        bold = "**" if k == "search_total" else ""
        lines.append(f"| {bold}{k}{bold} | {s['p50']} | {s['p95']} | {s['p99']} | {s['mean']} |")
    r = payload["run"]
    lines.append("")
    lines.append(
        f"RSS após carregar o modelo: **{r['rss_mb']['after_load']} MB** "
        f"(+{r['rss_mb']['model_delta']} MB do modelo) · device `{r['device']}` · "
        f"cache LRU {r['query_cache']['hit_rate']:.0%} hit"
    )
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Perfil de latência por etapa do SRI.")
    ap.add_argument("--split", default="test", choices=["test", "dev", "all"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument(
        "--rerank-pool", type=int, default=0, help="inclui a etapa do cross-encoder nesse tamanho de pool (0 = pula)."
    )
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.quiet:
        print("» carregando + aquecendo o motor…")
    payload = profile(args.split, args.repeats, args.rerank_pool, args.quiet)

    print(f"\n### Latência por etapa — split `{args.split}` ({payload['run']['n_queries']}×{args.repeats} amostras)\n")
    print(render(payload))
    print()

    if not args.no_write:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = payload["run"]["timestamp_utc"].replace(":", "-")
        tag = f"latency-p{args.rerank_pool}" if args.rerank_pool else "latency"
        canonical = args.out or os.path.join(RESULTS_DIR, f"{stamp}__{tag}-{args.split}.json")
        for path in (canonical, os.path.join(RESULTS_DIR, f"latest__{tag}-{args.split}.json")):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"» JSON: {os.path.relpath(canonical, _ROOT)}")


if __name__ == "__main__":
    main()
