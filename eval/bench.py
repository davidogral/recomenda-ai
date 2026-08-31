# -*- coding: utf-8 -*-
"""Benchmark de **configuração do encoder** — qualidade × latência × RSS.

Responde: qual modelo/precisão de embedding usar em produção?

    .venv/bin/python -m eval.bench embed          # matriz de embeddings (a entrega)
    .venv/bin/python -m eval.bench rerank         # matriz do cross-encoder
    .venv/bin/python -m eval.bench embed --worker "e5-large INT8 (ONNX)"   # 1 config (interno)

Cada configuração roda num **subprocesso isolado** (o RSS é por processo — não dá
para medir e5-large e e5-small no mesmo processo). O worker carrega o motor,
mede o RSS depois de carregar, roda a ablação `fusion` no split de teste
(nDCG@10 / MRR / Recall@50) e o perfil de latência por etapa; imprime um JSON.
O pai junta tudo numa tabela Markdown + grava `eval/results/latest__bench-*.json`.

Padrão: `RECOMENDAI_DEVICE=cpu` — servidor de produção não tem MPS; é o número
que importa para a decisão.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
RESULTS_DIR = os.path.join(_HERE, "results")

# ------------------------------------------------------------------ matrizes
EMBED_CONFIGS = [
    {"name": "e5-large fp32 (ST)",
     "env": {"RECOMENDAI_EMBED_BACKEND": "st"}, "index_dir": "retrieval/index"},
    {"name": "e5-large INT8 (ONNX)",
     "env": {"RECOMENDAI_EMBED_BACKEND": "onnx-int8"}, "index_dir": "retrieval/index"},
    {"name": "e5-small fp32 (ST)",
     "env": {"RECOMENDAI_EMBED_BACKEND": "st"}, "index_dir": "retrieval/index_e5small"},
]

RERANK_MODELS = [
    ("mMiniLMv2-L12 (atual)", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
    ("mMiniLM-L6 (mmarco)", "unicamp-dl/mMiniLM-L6-v2-mmarco-v2"),
]
RERANK_POOLS = [50, 100, 300]

SPLIT = "test"
LAT_REPEATS = 5


# ------------------------------------------------------------------- helpers
def _rss_mb() -> float:
    import psutil
    return round(psutil.Process().memory_info().rss / 1e6, 1)


def _metrics_for(engine, split: str) -> dict:
    from eval import metrics as M
    from eval.pipelines import PIPELINES, make_ctx, rank_of
    from eval.dataset import load_queries

    ranks = []
    for q in load_queries(split):
        ids = PIPELINES["fusion"](engine, make_ctx(engine, q.query))
        ranks.append(rank_of(ids, q.relevant_id))
    return M.aggregate(ranks)


def _latency_for(engine, split: str, repeats: int, rerank_pool: int = 0) -> dict:
    import time
    import numpy as np
    from eval import metrics as M
    from eval.dataset import load_queries
    from retrieval.search_engine import RERANK_BLEND, clean_descriptive_query

    qs = [q.query for q in load_queries(split)]
    stages = {k: [] for k in ("encode_cold", "encode_warm", "bm25", "emb_matmul",
                              "kw_matmul", "fuse_argsort", "search_total")}
    reranker = engine._get_reranker() if rerank_pool > 0 else None
    if reranker is not None:
        stages["rerank"] = []

    def _t(fn):
        t0 = time.perf_counter()
        r = fn()
        return (time.perf_counter() - t0) * 1000.0, r

    for _ in range(repeats):
        for q in qs:
            cq = clean_descriptive_query(q)
            engine._query_emb_cache.pop(cq, None)
            dt, q_emb = _t(lambda: engine._encode(cq)); stages["encode_cold"].append(dt)
            dt, _ = _t(lambda: engine._encode(cq)); stages["encode_warm"].append(dt)
            dt, _ = _t(lambda: engine._bm25.scores(cq)); stages["bm25"].append(dt)
            dt, _ = _t(lambda: engine._embeddings @ q_emb); stages["emb_matmul"].append(dt)
            kw_q = engine._keyword_query_emb(cq, q_emb)
            dt, _ = _t(lambda: engine._kw_embeddings @ kw_q); stages["kw_matmul"].append(dt)
            dt, order = _t(lambda: np.argsort(engine._synopsis_scores(q))[::-1])
            stages["fuse_argsort"].append(dt)
            if reranker is not None:
                ids = [int(engine._movie_ids[i]) for i in order[:rerank_pool]]
                dt, _ = _t(lambda: reranker.rerank(q, ids, engine._text_for,
                                                   retr_scores={t: 0.0 for t in ids},
                                                   blend=RERANK_BLEND))
                stages["rerank"].append(dt)
            dt, _ = _t(lambda: engine.search(q, mode="synopsis", n=10, explain=False))
            stages["search_total"].append(dt)

    def _p(xs, p):
        s = sorted(xs); k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
        return round(s[k], 1)
    return {k: {"p50": _p(v, 50), "p95": _p(v, 95), "p99": _p(v, 99)} for k, v in stages.items()}


# -------------------------------------------------------------------- worker
def run_worker(kind: str, name: str, device: str) -> None:
    os.environ.setdefault("RECOMENDAI_TMDB_NAMES", "0")
    os.environ["RECOMENDAI_DEVICE"] = device

    rss0 = _rss_mb()
    if kind == "embed":
        cfg = next(c for c in EMBED_CONFIGS if c["name"] == name)
        for k, v in cfg["env"].items():
            os.environ[k] = v
        index_dir = os.path.join(_ROOT, cfg["index_dir"])
        from retrieval.search_engine import SearchEngine
        eng = SearchEngine(rerank=False, index_dir=index_dir).warmup(reranker=False)
        rss1 = _rss_mb()
        out = {
            "name": name, "kind": "embed", "device": device,
            "embed_dim": int(eng._embeddings.shape[1]),
            "rss_mb": {"after_load": rss1, "model_delta": round(rss1 - rss0, 1)},
            "metrics": _metrics_for(eng, SPLIT),
            "latency": _latency_for(eng, SPLIT, LAT_REPEATS),
            "onnx_path": getattr(eng._get_embed_model(), "onnx_path", None),
        }
    else:  # rerank
        model_name = dict((n, m) for n, m in RERANK_MODELS)[name]
        os.environ["RECOMENDAI_RERANKER_MODEL"] = model_name
        os.environ["RECOMENDAI_RERANK"] = "1"
        from retrieval.search_engine import SearchEngine
        eng = SearchEngine(rerank=True).warmup(reranker=True)
        rr = eng._get_reranker()
        if rr is None or eng._reranker_failed:
            raise RuntimeError(f"cross-encoder '{model_name}' não carregou — abortando config")
        _ = rr.rerank("consulta de teste do benchmark", [eng._movie_ids[0].item()],
                      eng._text_for, retr_scores={}, blend=0.5)  # falha alto se .predict quebrar
        rss1 = _rss_mb()
        by_pool = {}
        for pool in RERANK_POOLS:
            m = _rerank_metrics(eng, SPLIT, pool)
            lat = _latency_for(eng, SPLIT, max(2, LAT_REPEATS // 2), rerank_pool=pool)
            by_pool[str(pool)] = {"metrics": m, "latency": lat}
        out = {"name": name, "kind": "rerank", "model": model_name, "device": device,
               "rss_mb": {"after_load": rss1, "model_delta": round(rss1 - rss0, 1)},
               "by_pool": by_pool}
    print("###JSON###" + json.dumps(out, ensure_ascii=False))


def _rerank_metrics(engine, split: str, pool: int) -> dict:
    from eval import metrics as M
    from eval.dataset import load_queries
    from eval.pipelines import rank_of

    ranks = []
    for q in load_queries(split):
        ids = engine.synopsis_ranked_ids(q.query, rerank=True, pool=pool)
        ranks.append(rank_of(ids, q.relevant_id))
    return M.aggregate(ranks)


# -------------------------------------------------------------------- parent
def _spawn(kind: str, name: str, device: str) -> dict:
    print(f"  · {name} …", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "eval.bench", kind, "--worker", name, "--device", device],
        cwd=_ROOT, capture_output=True, text=True, env={**os.environ},
    )
    for line in proc.stdout.splitlines():
        if line.startswith("###JSON###"):
            return json.loads(line[len("###JSON###"):])
    sys.stderr.write(proc.stdout[-3000:] + "\n" + proc.stderr[-3000:] + "\n")
    raise RuntimeError(f"worker '{name}' não retornou JSON (exit {proc.returncode})")


def bench_embed(device: str, quiet: bool) -> dict:
    rows = [_spawn("embed", c["name"], device) for c in EMBED_CONFIGS]
    payload = _wrap(rows, "embed", device)
    print("\n### Matriz de embeddings — split de teste, device `%s`\n" % device)
    print(_render_embed(rows))
    _write(payload, "bench-embed")
    return payload


def bench_rerank(device: str, quiet: bool) -> dict:
    rows = [_spawn("rerank", n, device) for n, _ in RERANK_MODELS]
    payload = _wrap(rows, "rerank", device)
    print("\n### Matriz do cross-encoder — split de teste, device `%s`\n" % device)
    print(_render_rerank(rows))
    _write(payload, "bench-rerank")
    return payload


def _wrap(rows: list, kind: str, device: str) -> dict:
    from eval.dataset import dataset_sha1
    from eval.run import _engine_meta, _git_commit
    return {"run": {"timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "git_commit": _git_commit(), "mode": f"bench_{kind}", "split": SPLIT,
                    "device": device, "dataset_sha1": dataset_sha1(),
                    "lat_repeats": LAT_REPEATS, "engine": _engine_meta()},
            "rows": rows}


def _render_embed(rows: list) -> str:
    h = ("| config | dim | nDCG@10 | MRR | R@50 | encode p99 | search p99 | RSS |\n"
         "|---|---|---|---|---|---|---|---|")
    out = [h]
    for r in rows:
        m, lat = r["metrics"], r["latency"]
        out.append(f"| {r['name']} | {r['embed_dim']} | {m['ndcg@10']:.3f} | {m['mrr']:.3f} "
                   f"| {m['recall@50']:.3f} | {lat['encode_cold']['p99']:.0f} ms "
                   f"| {lat['search_total']['p99']:.0f} ms | {r['rss_mb']['after_load']:.0f} MB |")
    return "\n".join(out)


def _render_rerank(rows: list) -> str:
    h = ("| modelo | pool | nDCG@10 | MRR | R@50 | rerank p99 | RSS |\n"
         "|---|---|---|---|---|---|---|")
    out = [h]
    for r in rows:
        for pool, d in r["by_pool"].items():
            m, lat = d["metrics"], d["latency"]
            rr = lat.get("rerank", {}).get("p99", 0)
            out.append(f"| {r['name']} | {pool} | {m['ndcg@10']:.3f} | {m['mrr']:.3f} "
                       f"| {m['recall@50']:.3f} | {rr:.0f} ms | {r['rss_mb']['after_load']:.0f} MB |")
    return "\n".join(out)


def _write(payload: dict, tag: str) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = payload["run"]["timestamp_utc"].replace(":", "-")
    for path in (os.path.join(RESULTS_DIR, f"{stamp}__{tag}.json"),
                 os.path.join(RESULTS_DIR, f"latest__{tag}.json")):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n» JSON: eval/results/latest__{tag}.json")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Benchmark de encoder (qualidade × latência × RSS).")
    ap.add_argument("kind", choices=["embed", "rerank"])
    ap.add_argument("--worker", default=None, help="(interno) roda 1 config neste processo.")
    ap.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.worker:
        run_worker(a.kind, a.worker, a.device)
    elif a.kind == "embed":
        bench_embed(a.device, a.quiet)
    else:
        bench_rerank(a.device, a.quiet)


if __name__ == "__main__":
    main()
