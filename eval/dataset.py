# -*- coding: utf-8 -*-
"""Carrega o conjunto de avaliação (`eval/datasets/queries.jsonl`).

O `.jsonl` é gerado por `eval.datasets.build_queries` e já traz o `tmdb_id`
relevante congelado em cada linha (rótulo estável, sem depender de fuzzy no
momento da avaliação). Aqui só lemos, filtramos por split e validamos.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
QUERIES_PATH = os.path.join(_HERE, "datasets", "queries.jsonl")


@dataclass(frozen=True)
class EvalQuery:
    qid: str
    split: str  # "dev" | "test"
    query: str
    title_hint: str
    year: int
    relevant_id: int  # tmdb_id do único filme relevante
    relevant_title: str
    source: str  # "v1-core" | "v1-ext" | "v2"


def dataset_sha1(path: str = QUERIES_PATH) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def load_queries(split: str | None = None, path: str = QUERIES_PATH) -> list[EvalQuery]:
    """Consultas do conjunto. `split`: 'dev', 'test' ou None/'all' para todas."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} não existe. Rode: .venv/bin/python -m eval.datasets.build_queries")
    out: list[EvalQuery] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out.append(
                EvalQuery(
                    qid=r["qid"],
                    split=r["split"],
                    query=r["query"],
                    title_hint=r["title_hint"],
                    year=r["year"],
                    relevant_id=int(r["relevant_tmdb_id"]),
                    relevant_title=r.get("relevant_title", ""),
                    source=r["source"],
                )
            )
    if split and split != "all":
        if split not in ("dev", "test", "hard", "entity"):
            raise ValueError(f"split inválido: {split!r} (use dev|test|all)")
        out = [q for q in out if q.split == split]
    if not out:
        raise RuntimeError(f"nenhuma consulta para split={split!r}")
    return out


def split_counts(path: str = QUERIES_PATH) -> dict:
    from collections import Counter

    qs = load_queries("all", path)
    counts: dict = {"total": len(qs), "by_split": dict(Counter(q.split for q in qs)), "by_source": {}}
    for q in qs:
        counts["by_source"].setdefault(q.source, Counter())[q.split] += 1
    counts["by_source"] = {k: dict(v) for k, v in counts["by_source"].items()}
    return counts
