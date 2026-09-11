# -*- coding: utf-8 -*-
"""Ablação parcial *leave-one-component-out* dos sinais adicionais da fusão
(personagem, enredo léxico, enredo MaxSim, prior de popularidade, teto de z-score).

Diferente de `eval.run` (que compara pipelines inteiros via `eval/pipelines.py`),
aqui cada linha é a MESMA fusão de produção com um componente zerado por
env var (`retrieval/search_engine.py`). Como esses pesos são lidos no import do
módulo, cada configuração roda num subprocesso próprio.

    .venv/bin/python -m eval.ablation_components                # split de teste, grava JSON
    .venv/bin/python -m eval.ablation_components --split dev
    .venv/bin/python -m eval.ablation_components --no-write

Usada nos resultados reportados na Seção "Ablação Parcial Leave-One-Component-Out"
do artigo (`artigo.tex`). Reporta nDCG@10 tanto no split completo quanto no
subconjunto `v2` (consultas não usadas na calibração original dos pesos).
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

# nome -> (env var, valor que zera o componente)
CONFIGS: list[tuple[str, dict[str, str]]] = [
    ("fusao_completa", {}),
    ("sem_personagem", {"RECOMENDAI_ENTITY_WEIGHT": "0"}),
    ("sem_enredo_lexico", {"RECOMENDAI_PLOT_BM25_WEIGHT": "0"}),
    ("sem_enredo_maxsim", {"RECOMENDAI_PLOT_CHUNK_WEIGHT": "0"}),
    ("sem_prior_popularidade", {"RECOMENDAI_POP_PRIOR": "0"}),
    ("sem_teto_zscore", {"RECOMENDAI_ZSCORE_CLIP": "0"}),
]

_WORKER = """
import json, sys
sys.path.insert(0, {root!r})
from eval.run import evaluate
from eval import metrics as M

payload = evaluate({split!r}, ["fusion"], None, True)
pq = payload["results"]["fusion"]["per_query"]

def agg(keep):
    ranks = [q["rank"] for q in pq if keep(q["source"])]
    a = M.aggregate(ranks)
    return {{"n": len(ranks), "ndcg10": a["ndcg@10"], "mrr": a["mrr"],
            "r10": a["recall@10"], "r50": a["recall@50"], "median": a["median_rank"]}}

print(json.dumps({{
    "all": agg(lambda s: True),
    "v2": agg(lambda s: s == "v2"),
    "engine": payload["run"]["engine"],
}}))
"""


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def run(split: str, quiet: bool) -> dict:
    python = sys.executable
    rows = {}
    for name, env_overrides in CONFIGS:
        env = dict(os.environ)
        env.update(env_overrides)
        # limpa overrides de configs anteriores que não fazem parte desta
        for _, other in CONFIGS:
            for k in other:
                if k not in env_overrides:
                    env.pop(k, None)
        if not quiet:
            print(f"  · {name} ({env_overrides or 'defaults'}) …", end=" ", flush=True)
        proc = subprocess.run(
            [python, "-c", _WORKER.format(root=_ROOT, split=split)],
            cwd=_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"config {name} falhou:\n{proc.stderr}")
        row = json.loads(proc.stdout.strip().splitlines()[-1])
        rows[name] = row
        if not quiet:
            print(f"nDCG@10(all)={row['all']['ndcg10']:.3f}  nDCG@10(v2)={row['v2']['ndcg10']:.3f}")
    return rows


def render(rows: dict) -> str:
    lines = [
        "| Configuração | nDCG@10 (todas) | nDCG@10 (v2) |",
        "|---|---|---|",
    ]
    for name, row in rows.items():
        lines.append(f"| {name} | {row['all']['ndcg10']:.3f} | {row['v2']['ndcg10']:.3f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    rows = run(args.split, args.quiet)
    print()
    print(f"### Ablação leave-one-component-out — split `{args.split}`\n")
    print(render(rows))

    payload = {
        "run": {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_commit": _git_commit(),
            "split": args.split,
            "configs": {name: overrides for name, overrides in CONFIGS},
        },
        "results": rows,
    }
    if not args.no_write:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = payload["run"]["timestamp_utc"].replace(":", "-")
        canonical = args.out or os.path.join(RESULTS_DIR, f"{stamp}__ablation-components-{args.split}.json")
        for path in (canonical, os.path.join(RESULTS_DIR, f"latest__ablation-components-{args.split}.json")):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"\n» JSON: {os.path.relpath(canonical, _ROOT)}")


if __name__ == "__main__":
    main()
