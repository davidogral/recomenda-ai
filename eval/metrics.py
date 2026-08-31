# -*- coding: utf-8 -*-
"""Métricas de recuperação **known-item**: cada consulta tem exatamente um
documento relevante (o filme que o usuário está tentando lembrar).

A entrada de cada função é a **posição 1-based** desse filme no ranking, ou
`None` se ele não apareceu dentro do corte considerado. Nesse regime de um único
relevante, várias métricas colapsam ou ganham forma fechada:

* **MRR** — média de `1/posição`. Sensível ao topo; é a métrica de "ele subiu?".
* **Recall@k** — fração de consultas com o filme certo dentro do top-k. Com um
  só relevante, `Recall@k == HitRate@k == Success@k`. `Recall@50` responde
  "o candidato certo chega ao re-ranker?" (o pool do cross-encoder é 300, mas 50
  já mostra se a 1ª etapa entregou).
* **nDCG@k** — `1/log2(pos+1)` se `pos<=k`, senão 0 (o IDCG é 1 com um relevante
  de ganho 1). Desconto de posição mais suave que o do MRR.
* **Precision@k** — `1/k` se o filme está no top-k, senão 0. Teto baixo por
  construção (só há 1 relevante); reportada por continuidade com o notebook
  antigo, não como métrica principal.

`aggregate` recebe a lista de posições (uma por consulta) e devolve o dicionário
de métricas médias + diagnósticos de rank (mediana, média, achados).
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable, Optional

Rank = Optional[int]


def reciprocal_rank(rank: Rank) -> float:
    return 1.0 / rank if rank else 0.0


def hit_at_k(rank: Rank, k: int) -> float:
    return 1.0 if (rank is not None and rank <= k) else 0.0


# Com um único documento relevante, recall e hit-rate são a mesma coisa.
recall_at_k = hit_at_k


def precision_at_k(rank: Rank, k: int) -> float:
    return (1.0 / k) if (rank is not None and rank <= k) else 0.0


def ndcg_at_k(rank: Rank, k: int) -> float:
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def aggregate(ranks: Iterable[Rank],
              recall_ks: tuple[int, ...] = (1, 3, 5, 10, 20, 50),
              precision_ks: tuple[int, ...] = (1, 5, 10),
              ndcg_ks: tuple[int, ...] = (10,)) -> dict:
    """Agrega posições (1-based, `None` = não recuperado) em métricas médias."""
    ranks = list(ranks)
    n = len(ranks)
    found = [r for r in ranks if r is not None]

    out: dict = {
        "n": n,
        "found": len(found),
        "mrr": round(_mean([reciprocal_rank(r) for r in ranks]), 4),
        "median_rank": int(statistics.median(found)) if found else None,
        "mean_rank": round(statistics.mean(found), 1) if found else None,
    }
    for k in recall_ks:
        out[f"recall@{k}"] = round(_mean([recall_at_k(r, k) for r in ranks]), 4)
    for k in precision_ks:
        out[f"precision@{k}"] = round(_mean([precision_at_k(r, k) for r in ranks]), 4)
    for k in ndcg_ks:
        out[f"ndcg@{k}"] = round(_mean([ndcg_at_k(r, k) for r in ranks]), 4)
    # hits absolutos ajudam a ler um conjunto pequeno ("45/52").
    for k in (1, 3, 10):
        out[f"hits@{k}"] = int(sum(hit_at_k(r, k) for r in ranks))
    return out


def latency_stats(samples_ms: list[float]) -> dict:
    if not samples_ms:
        return {"mean_ms": None, "p50_ms": None, "p90_ms": None, "max_ms": None}
    s = sorted(samples_ms)
    def pct(p: float) -> float:
        return s[min(len(s) - 1, int(math.ceil(p / 100.0 * len(s)) - 1))]
    return {
        "mean_ms": round(_mean(samples_ms), 1),
        "p50_ms": round(pct(50), 1),
        "p90_ms": round(pct(90), 1),
        "max_ms": round(max(samples_ms), 1),
    }
