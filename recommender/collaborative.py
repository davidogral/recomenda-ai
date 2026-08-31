"""RecomendaÃ§Ã£o colaborativa: fold-in de usuÃ¡rio novo + fallbacks.

Sistema **independente** da recuperaÃ§Ã£o. Carrega os fatores treinados por
`recommender/train.py` e recomenda para um usuÃ¡rio que ainda nÃ£o estÃ¡ no modelo
(ex.: importado do Letterboxd), **sem retreinar**.

Cascata de estratÃ©gias, por nÂº de itens conhecidos (que existem no modelo):
  - overlap >= `min_overlap_foldin`  â†’ **fold-in**: resolve o vetor latente `p`
    por ridge sobre os itens avaliados; score = mu + bi + qiÂ·p.
  - 1 <= overlap < min_overlap_foldin â†’ **item-item**: agrega os top-k vizinhos
    dos itens que o usuÃ¡rio gostou (vizinhos prÃ©-computados, esparsos).
  - overlap == 0                      â†’ **popularidade** (do catÃ¡logo).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import numpy as np

from core import catalog
from recommender import train as tr


class CollaborativeRecommender:
    def __init__(self, weights_dir: str = tr.WEIGHTS_DIR,
                 ridge_reg: float = 0.1, min_overlap_foldin: int = 2,
                 bias_weight: float = 0.5):
        self.ridge_reg = ridge_reg
        self.min_overlap_foldin = min_overlap_foldin
        # Peso do viÃ©s de item `bi` no ranking top-N. A nota prevista Ã© sempre
        # mu + bi + qiÂ·p (bom para RMSE), mas rankear top-N pela nota prevista
        # crua super-recomenda os clÃ¡ssicos universalmente aclamados (bi alto)
        # para qualquer gosto. Reduzir `bias_weight` privilegia o casamento de
        # gosto (qiÂ·p) e melhora a personalizaÃ§Ã£o. 1.0 = nota prevista pura.
        self.bias_weight = bias_weight

        if not os.path.exists(tr.META_PATH):
            raise RuntimeError(
                "Pesos do recomendador ausentes. Rode recommender/train.py "
                "(ou research/train_recommender.ipynb) primeiro."
            )
        with open(tr.META_PATH, encoding="utf-8") as f:
            self.meta = json.load(f)
        self.mu: float = float(self.meta["mu"])
        self.qi: np.ndarray = np.load(tr.QI_PATH)
        self.bi: np.ndarray = np.load(tr.BI_PATH)
        self.item_ids: np.ndarray = np.load(tr.ITEM_IDS_PATH)
        self.neighbors: np.ndarray = np.load(tr.NEIGHBORS_PATH)
        self.neighbor_sims: np.ndarray = np.load(tr.NEIGHBOR_SIMS_PATH)
        # tmdb_id -> linha em qi/bi
        self.item_index: dict[int, int] = {int(t): i for i, t in enumerate(self.item_ids)}
        self.n_factors: int = self.qi.shape[1]

    # ------------------------------------------------------------------ fold-in
    def fold_in(self, known: list[tuple[int, float]]) -> np.ndarray:
        """Resolve o vetor latente `p` por ridge sobre os itens avaliados.

        `known`: lista de (linha_em_qi, rating). Minimiza
        ||y - Q p||Â² + regÂ·||p||Â², com y = rating - mu - bi.
        """
        rows = np.array([r for r, _ in known], dtype=np.int64)
        ratings = np.array([v for _, v in known], dtype=np.float64)
        Q = self.qi[rows].astype(np.float64)            # m Ã— k
        y = ratings - self.mu - self.bi[rows]           # resÃ­duo alvo
        A = Q.T @ Q + self.ridge_reg * np.eye(self.n_factors)
        p = np.linalg.solve(A, Q.T @ y)
        return p.astype(np.float32)

    # ------------------------------------------------------------------- modos
    def _predict_all(self, p: np.ndarray) -> np.ndarray:
        """Nota prevista para todos os itens: mu + bi + qiÂ·p."""
        return self.mu + self.bi + self.qi @ p

    def _rank_all(self, p: np.ndarray) -> np.ndarray:
        """Score de ranking top-N (viÃ©s de item down-weighted)."""
        return self.mu + self.bias_weight * self.bi + self.qi @ p

    def _item_item_scores(self, known: list[tuple[int, float]]) -> dict[int, float]:
        """Agrega vizinhos dos itens que o usuÃ¡rio gostou (rating > mu)."""
        agg: dict[int, float] = {}
        for row, rating in known:
            w = rating - self.mu
            if w <= 0:
                continue  # sÃ³ itens de que ele gostou puxam vizinhos
            for nbr, sim in zip(self.neighbors[row], self.neighbor_sims[row]):
                if sim <= 0:
                    continue
                agg[int(nbr)] = agg.get(int(nbr), 0.0) + float(w * sim)
        return agg

    def item_neighbors(self, tmdb_id: int, top: Optional[int] = None
                       ) -> list[tuple[int, float]]:
        """Vizinhos item-item pré-computados de um filme: [(tmdb_id, sim)] por sim
        decrescente. Lista vazia se o filme não está no modelo colaborativo
        (ex.: lançamento recente sem ratings) — o chamador degrada para conteúdo."""
        row = self.item_index.get(int(tmdb_id))
        if row is None:
            return []
        out = [
            (int(self.item_ids[int(nbr)]), float(sim))
            for nbr, sim in zip(self.neighbors[row], self.neighbor_sims[row])
            if sim > 0
        ]
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out[:top] if top else out

    def _popularity_rows(self) -> list[int]:
        """Linhas (em qi) ordenadas por popularidade do catÃ¡logo."""
        cat = catalog.get_catalog()
        scored = [
            (i, (cat.get(int(t), {}).get("popularity") or 0.0))
            for i, t in enumerate(self.item_ids)
        ]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return [i for i, _ in scored]

    # ---------------------------------------------------------------- recommend
    def recommend(self, user_ratings: list[tuple[int, float]], n: int = 20,
                  exclude_rated: bool = True) -> list[dict[str, Any]]:
        """Recomenda `n` filmes para `user_ratings` = [(tmdb_id, rating)]."""
        known = [
            (self.item_index[int(t)], float(r))
            for t, r in user_ratings
            if int(t) in self.item_index
        ]
        rated_rows = {row for row, _ in known}

        pred: Optional[np.ndarray] = None
        if len(known) >= self.min_overlap_foldin:
            method = "fold_in"
            p = self.fold_in(known)
            pred = self._predict_all(p)
            rank_score = self._rank_all(p)
            order = np.argsort(rank_score)[::-1]
            ranked = [(int(i), float(rank_score[i])) for i in order]
        elif len(known) >= 1:
            method = "item_item"
            agg = self._item_item_scores(known)
            ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
        else:
            method = "popularity"
            ranked = [(i, 0.0) for i in self._popularity_rows()]

        out: list[dict[str, Any]] = []
        for row, score in ranked:
            if exclude_rated and row in rated_rows:
                continue
            predicted = float(pred[row]) if pred is not None else None
            out.append(self._format(row, score, method, predicted))
            if len(out) >= n:
                break
        return out

    def _format(self, row: int, score: float, method: str,
                predicted: Optional[float] = None) -> dict[str, Any]:
        tmdb_id = int(self.item_ids[row])
        mv = catalog.get_movie(tmdb_id) or {}
        return {
            "tmdb_id": tmdb_id,
            "title": mv.get("title"),
            "release_year": mv.get("release_year"),
            "vote_average": mv.get("vote_average"),
            "predicted_rating": round(predicted, 3) if predicted is not None else None,
            "score": round(float(score), 4),
            "method": method,
        }


_recommender: Optional[CollaborativeRecommender] = None


def get_recommender() -> CollaborativeRecommender:
    global _recommender
    if _recommender is None:
        _recommender = CollaborativeRecommender()
    return _recommender
