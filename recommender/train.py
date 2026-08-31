"""Treina o modelo colaborativo (SVD) nos ratings reais e serializa os fatores.

Gera, em `recommender/weights/`:
  - `qi.npy`            fatores latentes dos itens (n_items Ã— n_factors, float32)
  - `bi.npy`            vieses dos itens (n_items, float32)
  - `item_ids.npy`      tmdb_id de cada linha de qi/bi (mapeamento Ã­ndiceâ†’item)
  - `neighbors.npy`     top-k vizinhos por item (Ã­ndices em qi) â€” fallback item-item
  - `neighbor_sims.npy` similaridades cosseno dos vizinhos (float32)
  - `meta.json`         mu (mÃ©dia global), n_factors, RMSE/MAE em holdout, contagens

A lÃ³gica fica aqui (rodÃ¡vel por CLI/notebook). `research/train_recommender.ipynb`
apenas chama `train()` e reporta as mÃ©tricas.

NÃ£o usa ratings sintÃ©ticos â€” sÃ³ os ratings reais (escala 0.5â€“5.0).
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

import numpy as np

from core import db

WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")

QI_PATH = os.path.join(WEIGHTS_DIR, "qi.npy")
BI_PATH = os.path.join(WEIGHTS_DIR, "bi.npy")
ITEM_IDS_PATH = os.path.join(WEIGHTS_DIR, "item_ids.npy")
NEIGHBORS_PATH = os.path.join(WEIGHTS_DIR, "neighbors.npy")
NEIGHBOR_SIMS_PATH = os.path.join(WEIGHTS_DIR, "neighbor_sims.npy")
META_PATH = os.path.join(WEIGHTS_DIR, "meta.json")

RATING_SCALE = (0.5, 5.0)


def _load_ratings_df(sample: Optional[int], seed: int):
    """Carrega os ratings como DataFrame (user_id, tmdb_id, rating)."""
    import pandas as pd

    df = pd.read_sql_query(
        "SELECT user_id, tmdb_id, rating FROM ratings", db.get_connection()
    )
    if sample is not None and sample < len(df):
        df = df.sample(n=sample, random_state=seed).reset_index(drop=True)
    return df


def _compute_neighbors(qi: np.ndarray, k: int):
    """Top-k vizinhos por item via cosseno (brute, sem matriz densa 22kÂ²)."""
    from sklearn.neighbors import NearestNeighbors

    k_eff = min(k + 1, qi.shape[0])  # +1 porque o prÃ³prio item aparece
    nn = NearestNeighbors(n_neighbors=k_eff, metric="cosine", algorithm="brute")
    nn.fit(qi)
    dist, idx = nn.kneighbors(qi)
    # Remove a auto-correspondÃªncia (primeira coluna costuma ser o prÃ³prio item).
    neighbors = np.empty((qi.shape[0], k_eff - 1), dtype=np.int32)
    sims = np.empty((qi.shape[0], k_eff - 1), dtype=np.float32)
    for i in range(qi.shape[0]):
        row_idx = idx[i]
        row_sim = 1.0 - dist[i]
        mask = row_idx != i
        ri = row_idx[mask][: k_eff - 1]
        rs = row_sim[mask][: k_eff - 1]
        neighbors[i, : len(ri)] = ri
        sims[i, : len(rs)] = rs
    return neighbors, sims


def train(
    n_factors: int = 50,
    n_epochs: int = 20,
    lr_all: float = 0.005,
    reg_all: float = 0.02,
    test_size: float = 0.1,
    sample: Optional[int] = None,
    neighbors_k: int = 50,
    seed: int = 42,
    save: bool = True,
    verbose: bool = True,
) -> dict:
    """Treina o SVD, reporta RMSE/MAE em holdout e salva os fatores.

    Treina em `1-test_size` para avaliar, depois **retreina no conjunto
    completo** para os pesos salvos (mais dados = melhor para servir).
    """
    from surprise import SVD, Dataset, Reader, accuracy
    from surprise.model_selection import train_test_split

    os.makedirs(WEIGHTS_DIR, exist_ok=True)

    df = _load_ratings_df(sample, seed)
    n_ratings = len(df)
    n_users = int(df["user_id"].nunique())
    n_items = int(df["tmdb_id"].nunique())

    reader = Reader(rating_scale=RATING_SCALE)
    data = Dataset.load_from_df(df[["user_id", "tmdb_id", "rating"]], reader)

    # --- AvaliaÃ§Ã£o em holdout ---
    t0 = time.time()
    trainset, testset = train_test_split(data, test_size=test_size, random_state=seed)
    algo = SVD(n_factors=n_factors, n_epochs=n_epochs, lr_all=lr_all,
               reg_all=reg_all, random_state=seed)
    algo.fit(trainset)
    preds = algo.test(testset)
    rmse = float(accuracy.rmse(preds, verbose=False))
    mae = float(accuracy.mae(preds, verbose=False))
    eval_secs = round(time.time() - t0, 1)
    if verbose:
        print(f"[holdout] RMSE={rmse:.4f}  MAE={mae:.4f}  ({eval_secs}s)")

    # --- Modelo final no conjunto completo ---
    t0 = time.time()
    full_trainset = data.build_full_trainset()
    algo = SVD(n_factors=n_factors, n_epochs=n_epochs, lr_all=lr_all,
               reg_all=reg_all, random_state=seed)
    algo.fit(full_trainset)
    fit_secs = round(time.time() - t0, 1)

    # Fatores/vieses dos itens + mapeamento Ã­ndiceâ†’tmdb_id.
    qi = np.asarray(algo.qi, dtype=np.float32)
    bi = np.asarray(algo.bi, dtype=np.float32)
    mu = float(full_trainset.global_mean)
    item_ids = np.array(
        [int(full_trainset.to_raw_iid(inner)) for inner in range(full_trainset.n_items)],
        dtype=np.int64,
    )

    # Vizinhos top-k (fallback item-item).
    neighbors, sims = _compute_neighbors(qi, neighbors_k)

    meta = {
        "n_ratings": int(n_ratings),
        "n_users": n_users,
        "n_items": n_items,
        "n_factors": int(n_factors),
        "n_epochs": int(n_epochs),
        "mu": mu,
        "rmse_holdout": rmse,
        "mae_holdout": mae,
        "rating_scale": list(RATING_SCALE),
        "neighbors_k": int(neighbors.shape[1]),
        "eval_secs": eval_secs,
        "fit_secs": fit_secs,
        "built_at": int(time.time()),
    }

    if save:
        np.save(QI_PATH, qi)
        np.save(BI_PATH, bi)
        np.save(ITEM_IDS_PATH, item_ids)
        np.save(NEIGHBORS_PATH, neighbors)
        np.save(NEIGHBOR_SIMS_PATH, sims)
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"[salvo] {WEIGHTS_DIR}  qi={qi.shape}  fit={fit_secs}s")

    return meta


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Treina o SVD colaborativo.")
    p.add_argument("--n-factors", type=int, default=50)
    p.add_argument("--n-epochs", type=int, default=20)
    p.add_argument("--sample", type=int, default=None, help="Amostra N ratings (teste rÃ¡pido).")
    p.add_argument("--neighbors-k", type=int, default=50)
    args = p.parse_args()

    info = train(n_factors=args.n_factors, n_epochs=args.n_epochs,
                 sample=args.sample, neighbors_k=args.neighbors_k)
    print(json.dumps(info, ensure_ascii=False, indent=2))
