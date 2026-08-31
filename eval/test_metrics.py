# -*- coding: utf-8 -*-
"""Testes rápidos das métricas known-item. Rodar:

.venv/bin/python -m eval.test_metrics
"""

from __future__ import annotations

import math

from eval import metrics as M


def test_reciprocal_rank():
    assert M.reciprocal_rank(1) == 1.0
    assert M.reciprocal_rank(2) == 0.5
    assert M.reciprocal_rank(None) == 0.0


def test_recall_and_precision():
    assert M.recall_at_k(3, 5) == 1.0
    assert M.recall_at_k(6, 5) == 0.0
    assert M.recall_at_k(None, 50) == 0.0
    assert M.precision_at_k(1, 10) == 0.1  # teto: 1 relevante / k
    assert M.precision_at_k(11, 10) == 0.0


def test_ndcg():
    assert M.ndcg_at_k(1, 10) == 1.0  # 1/log2(2)
    assert abs(M.ndcg_at_k(3, 10) - 1.0 / math.log2(4)) < 1e-9
    assert M.ndcg_at_k(11, 10) == 0.0
    assert M.ndcg_at_k(None, 10) == 0.0


def test_aggregate():
    ranks = [1, 2, 5, None, 50]
    agg = M.aggregate(ranks)
    assert agg["n"] == 5 and agg["found"] == 4
    assert agg["hits@1"] == 1 and agg["hits@3"] == 2 and agg["hits@10"] == 3
    assert agg["recall@50"] == 0.8  # 4 de 5 dentro do top-50
    assert abs(agg["mrr"] - (1 + 0.5 + 0.2 + 0 + 0.02) / 5) < 1e-9
    assert agg["median_rank"] == 3  # mediana de [1,2,5,50]


def test_latency_stats():
    s = M.latency_stats([10.0, 20.0, 30.0, 40.0])
    assert s["max_ms"] == 40.0 and s["mean_ms"] == 25.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} testes passaram.")
