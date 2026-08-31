"""Perfil de gosto → recomendação por conteúdo (multi-interesse + relevance feedback).

Por que não bastava a média (versão anterior): resumir TODOS os filmes amados num
único vetor de gosto (centroide) funde um gosto multi-modal — quem gosta de
Kubrick *e* comédia romântica *e* terror vira um vetor no "meio" do espaço de
embeddings, justamente onde ficam os filmes genéricos/populares. Daí as sugestões
saíam genéricas.

Abordagem atual (o que a literatura indica como mais forte para perfis de conteúdo),
montada sobre os embeddings e5-large que o índice já tem:

  1. **Multi-interesse**: agrupamos os filmes amados em K interesses (k-means
     esférico). Cada candidato é pontuado pelo **melhor** interesse (max-pooling),
     não pela média — então "gosto de A e de B" não vira mush.
  2. **Relevance feedback (Rocchio)**: notas altas *puxam*, notas baixas *empurram*.
     O sinal negativo (filmes que a pessoa detestou / reviews ruins) subtrai score.
  3. **Reviews por filme**: o texto da resenha é embutido e misturado ao vetor
     DAQUELE filme (refina o que a pessoa articulou sobre ele), não numa média global.
  4. **De-viés de genericidade**: penalizamos proximidade ao "filme médio" do
     catálogo (direção comum dos embeddings) — derruba o blockbuster genérico.
  5. **MMR**: diversifica o topo (evita 8 sequências do mesmo filme).

Tudo é fundido com o colaborativo (fold-in sobre o SVD) por rank recíproco (RRF),
que acrescenta serendipidade de co-avaliação real.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from core import catalog

# --- Limiares de feedback (escala Letterboxd 0.5–5.0) ---------------------
POS_THRESHOLD = 3.0       # nota >= isto contribui como gosto (peso graduado)
NEG_THRESHOLD = 2.5       # nota <= isto contribui como antipatia
STRONG_LIKE = 3.5         # "amado" (só para o resumo exibido)

# --- Mistura de sinais -----------------------------------------------------
REVIEW_BLEND = 0.30       # quanto a resenha entra no vetor DAQUELE filme
KW_WEIGHT = 0.5           # peso do sinal temático (keywords) vs. sinopse
NEG_WEIGHT = 0.5          # quanto a antipatia subtrai do score
GENERIC_WEIGHT = 0.35     # penalidade por parecer com o "filme médio" (hubness)
MMR_LAMBDA = 0.72         # MMR: relevância vs. diversidade (1.0 = só relevância)
MAX_INTERESTS = 5         # teto de clusters de gosto

# --- Fusão com o colaborativo (RRF) ---------------------------------------
CONTENT_RRF_W = 1.0
COLLAB_RRF_W = 0.5
RRF_K = 20


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _norm_rows(m: np.ndarray) -> np.ndarray:
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


def _zscore(x: np.ndarray) -> np.ndarray:
    """Padroniza scores (média 0, desvio 1). Torna os pesos entre sinais
    comparáveis e neutraliza a compressão dos cossenos do e5."""
    std = float(x.std())
    if std <= 0:
        return np.zeros_like(x)
    return (x - float(x.mean())) / std


def _pos_weight(rating: float) -> float:
    """Peso de um gosto: 5★→2.5, 4★→1.5, 3★→0.5 (chamado só p/ rating >= 3.0)."""
    return float(rating) - 2.5


def _neg_weight(rating: float) -> float:
    """Peso de uma antipatia: 0.5★→2.5, 2.5★→0.5 (chamado só p/ rating <= 2.5)."""
    return 3.0 - float(rating)


def _encode_many(engine, texts: list[str]) -> np.ndarray:
    """Embute uma lista de textos (reviews) num lote — L2-normalizado."""
    prefix = engine._embed_query_prefix or ""
    model = engine._get_embed_model()
    arr = model.encode([f"{prefix}{t}" for t in texts],
                       normalize_embeddings=True, convert_to_numpy=True)
    return arr.astype(np.float64)


class TasteProfile:
    """Modelo de gosto multi-interesse a partir de [{tmdb_id, rating, review}]."""

    def __init__(self, engine, detail: list[dict]):
        self.engine = engine
        self.detail = detail
        self.seen = {int(d["tmdb_id"]) for d in detail}
        self.strong_liked = [d for d in detail if float(d["rating"]) >= STRONG_LIKE]
        self.positives = [d for d in detail if float(d["rating"]) >= POS_THRESHOLD]
        self.negatives = [d for d in detail if float(d["rating"]) <= NEG_THRESHOLD]

        self.interests: list[dict] = []     # [{syn, kw, weight, ids}]
        self.neg_syn: Optional[np.ndarray] = None
        self.neg_kw: Optional[np.ndarray] = None
        self._gen_syn: Optional[np.ndarray] = None
        self._best_cluster: Optional[np.ndarray] = None  # cluster vencedor por linha
        self._build()

    # ----------------------------------------------------- coleta de vetores
    def _gather(self, items: list[dict], weight_fn) -> Optional[dict]:
        """Vetores (sinopse + temático) e pesos dos itens que existem no índice.
        A resenha, quando há, é misturada ao vetor de sinopse DAQUELE item."""
        eng = self.engine
        rows, w, ids, reviews = [], [], [], []
        for d in items:
            r = eng._row_index.get(int(d["tmdb_id"]))
            if r is None:
                continue
            rows.append(r)
            w.append(max(0.25, float(weight_fn(d["rating"]))))
            ids.append(int(d["tmdb_id"]))
            reviews.append((d.get("review") or "").strip())
        if not rows:
            return None
        rows = np.asarray(rows)
        vsyn = _norm_rows(eng._embeddings[rows].astype(np.float64))

        if REVIEW_BLEND > 0 and any(reviews):
            idx = [i for i, t in enumerate(reviews) if t]
            rev = _encode_many(eng, [reviews[i][:400] for i in idx])
            for k, i in enumerate(idx):
                vsyn[i] = _norm((1 - REVIEW_BLEND) * vsyn[i] + REVIEW_BLEND * rev[k])

        vkw = None
        if eng._kw_embeddings is not None:
            vkw = _norm_rows(eng._kw_embeddings[rows].astype(np.float64))
        return {"rows": rows, "ids": np.asarray(ids), "w": np.asarray(w),
                "vsyn": vsyn, "vkw": vkw}

    # --------------------------------------------------------------- montagem
    def _build(self) -> None:
        eng = self.engine
        if eng._embeddings is None:
            return
        pos = self._gather(self.positives, _pos_weight)
        if pos is None:
            return
        self._cluster(pos)

        neg = self._gather(self.negatives, _neg_weight)
        if neg is not None:
            self.neg_syn = _norm((neg["vsyn"] * neg["w"][:, None]).sum(0))
            if neg["vkw"] is not None:
                self.neg_kw = _norm((neg["vkw"] * neg["w"][:, None]).sum(0))

        # Direção "média" do catálogo — proxy de genericidade/hubness.
        self._gen_syn = _norm(eng._embeddings.mean(0).astype(np.float64))

    def _cluster(self, pos: dict) -> None:
        """Particiona os gostos em K interesses (k-means esférico ponderado)."""
        n = len(pos["w"])
        k = int(np.clip(round(n / 4), 1, MAX_INTERESTS))
        k = min(k, n)
        if k <= 1:
            labels = np.zeros(n, dtype=int)
        else:
            try:
                from sklearn.cluster import KMeans
                labels = KMeans(n_clusters=k, n_init=4, random_state=0).fit_predict(
                    pos["vsyn"], sample_weight=pos["w"])
            except Exception:
                labels = np.zeros(n, dtype=int)

        interests = []
        for c in np.unique(labels):
            m = labels == c
            w = pos["w"][m][:, None]
            syn = _norm((pos["vsyn"][m] * w).sum(0))
            kw = _norm((pos["vkw"][m] * w).sum(0)) if pos["vkw"] is not None else None
            interests.append({"syn": syn, "kw": kw,
                              "weight": float(pos["w"][m].sum()),
                              "ids": [int(x) for x in pos["ids"][m]]})
        interests.sort(key=lambda it: -it["weight"])
        self.interests = interests

    @property
    def has_vector(self) -> bool:
        return bool(self.interests)

    # ------------------------------------------------------- score do catálogo
    def _interest_scores(self) -> tuple[np.ndarray, np.ndarray]:
        """Score de cada interesse para todo o catálogo (K×N), já com as penalidades
        de antipatia e genericidade. Devolve (scores_por_interesse, score_global).

        Cada interesse é padronizado (z-score) separadamente, então um gosto de
        nicho não é abafado por um gosto popular — eles competem em pé de igualdade,
        e a representação proporcional fica a cargo do round-robin ponderado."""
        eng = self.engine
        S = eng._embeddings
        Kw = eng._kw_embeddings

        penalty = np.zeros(S.shape[0], dtype=np.float64)
        if self.neg_syn is not None:
            neg = S @ self.neg_syn
            if Kw is not None and self.neg_kw is not None:
                neg = neg + KW_WEIGHT * (Kw @ self.neg_kw)
            penalty = penalty + NEG_WEIGHT * _zscore(neg)
        if self._gen_syn is not None:
            penalty = penalty + GENERIC_WEIGHT * _zscore(S @ self._gen_syn)

        scores_k = []
        for it in self.interests:
            sim = S @ it["syn"]
            if Kw is not None and it["kw"] is not None:
                sim = sim + KW_WEIGHT * (Kw @ it["kw"])
            scores_k.append(_zscore(sim) - penalty)
        stacked = np.stack(scores_k)                       # K×N
        self._best_cluster = np.argmax(stacked, axis=0)
        return stacked, stacked.max(axis=0)

    def recommend_content(self, n: int, exclude: Optional[set] = None
                          ) -> list[tuple[int, float]]:
        if not self.has_vector:
            return []
        eng = self.engine
        scores_k, gscore = self._interest_scores()
        skip = set(exclude or set()) | self.seen

        # Lista de candidatos ordenada POR interesse (cada um puxa os seus).
        ranked_k = []
        for k in range(len(self.interests)):
            order = np.argsort(scores_k[k])[::-1]
            ranked_k.append([int(i) for i in order
                             if int(eng._movie_ids[i]) not in skip])

        # Round-robin ponderado pelo peso do interesse → representação proporcional.
        weights = np.array([it["weight"] for it in self.interests], dtype=np.float64)
        target = max(n * 6, 90)
        ptr = [0] * len(ranked_k)
        taken = [0] * len(ranked_k)
        pool: list[int] = []
        seen_rows: set[int] = set()
        while len(pool) < target and any(ptr[k] < len(ranked_k[k]) for k in range(len(ranked_k))):
            avail = [k for k in range(len(ranked_k)) if ptr[k] < len(ranked_k[k])]
            k = min(avail, key=lambda k: (taken[k] + 1) / weights[k])
            row = ranked_k[k][ptr[k]]
            ptr[k] += 1
            if row in seen_rows:
                continue
            seen_rows.add(row)
            pool.append(row)
            taken[k] += 1

        pool_arr = np.asarray(pool)
        # Relevância p/ o MMR = posição no round-robin (preserva a proporção entre
        # interesses); o MMR só remove redundância sem desbalancear os gostos.
        rel = 1.0 - np.arange(len(pool_arr)) / max(len(pool_arr), 1)
        ranked = self._mmr(pool_arr, rel, n_out=min(len(pool_arr), max(n * 3, n)))
        return [(int(eng._movie_ids[i]), float(gscore[i])) for i in ranked]

    def _mmr(self, pool: np.ndarray, rel: np.ndarray, n_out: int) -> list[int]:
        """Maximal Marginal Relevance: equilibra relevância e novidade vs. o já escolhido.
        `rel` já vem alinhado a `pool`, em [0,1]."""
        if pool.size == 0:
            return []
        V = self.engine._embeddings[pool].astype(np.float64)   # P×D (normalizado)
        s = rel.astype(np.float64)
        cand = list(range(len(pool)))
        selected: list[int] = []
        sim_sel = np.full(len(pool), -1.0)
        while cand and len(selected) < n_out:
            if not selected:
                pick = cand[int(np.argmax(s[cand]))]
            else:
                mmr = MMR_LAMBDA * s[cand] - (1 - MMR_LAMBDA) * sim_sel[cand]
                pick = cand[int(np.argmax(mmr))]
            selected.append(pick)
            cand.remove(pick)
            sim_sel = np.maximum(sim_sel, V @ V[pick])
        return [int(pool[i]) for i in selected]

    # --------------------------------------------------------------- resumo
    def _interest_label(self, it: dict) -> str:
        g: dict[str, float] = defaultdict(float)
        for tid in it["ids"]:
            for name in catalog.get_movie_genres(tid):
                g[name] += 1
        top = [k for k, _ in sorted(g.items(), key=lambda kv: -kv[1])[:2]]
        return " / ".join(top) if top else "gosto variado"

    def summary(self, top: int = 6) -> dict[str, Any]:
        cat = catalog.get_catalog()
        g, d, a, k = (defaultdict(float) for _ in range(4))
        dec: dict[int, float] = defaultdict(float)
        for item in self.positives:
            tid = int(item["tmdb_id"])
            w = max(0.25, _pos_weight(item["rating"]))
            for name in catalog.get_movie_genres(tid):
                g[name] += w
            for name in catalog.get_movie_keywords(tid):
                k[name] += w
            for p in catalog.get_movie_people(tid, role="director"):
                d[p["name"]] += w
            for p in catalog.get_movie_people(tid, role="actor")[:5]:
                a[p["name"]] += w
            year = cat.get(tid, {}).get("release_year")
            if year:
                dec[(int(year) // 10) * 10] += w
        topn = lambda dd: [name for name, _ in sorted(dd.items(), key=lambda kv: -kv[1])[:top]]
        ratings = [float(x["rating"]) for x in self.detail]
        return {
            "n_rated": len(self.detail),
            "n_liked": len(self.strong_liked),
            "avg_rating": round(float(np.mean(ratings)), 2) if ratings else None,
            "genres": topn(g),
            "directors": topn(d),
            "actors": topn(a),
            "themes": topn(k),
            "decades": [f"{int(x)}s" for x in sorted(dec, key=lambda x: -dec[x])[:4]],
            "interests": [self._interest_label(it) for it in self.interests],
        }

    # ----------------------------------------------------- explicação por filme
    def _why(self, tmdb_id: int, summary: dict) -> list[str]:
        reasons: list[str] = []
        row = self.engine._row_index.get(int(tmdb_id))
        if self._best_cluster is not None and row is not None and self.interests:
            label = self._interest_label(self.interests[int(self._best_cluster[row])])
            if label:
                reasons.append(label)
        dirs = {p["name"] for p in catalog.get_movie_people(tmdb_id, role="director")}
        shared_dir = [x for x in summary["directors"] if x in dirs]
        if shared_dir:
            reasons.append(f"direção de {shared_dir[0]}")
        return reasons[:2]


def recommend_from_profile(detail: list[dict], n: int = 20, region: Optional[str] = None,
                           provider_ids: Optional[list[int]] = None) -> dict[str, Any]:
    """Recomenda combinando conteúdo multi-interesse + colaborativo, e devolve o
    perfil traçado. `detail`: [{tmdb_id, rating, name, year, review}].

    Se `provider_ids` for dado, devolve as **N melhores recomendações disponíveis**
    nesses serviços (filtra o ranking completo, sem cortar a lista exibida)."""
    from retrieval.search_engine import get_engine

    engine = get_engine()
    prof = TasteProfile(engine, detail)
    summary = prof.summary()

    # Filtrando por streaming, geramos um ranking maior para juntar N disponíveis.
    filtering = bool(provider_ids)
    mult = 8 if filtering else 3
    content = prof.recommend_content(n * mult)

    # Colaborativo (serendipidade) — best-effort.
    collab: list[dict] = []
    try:
        from recommender.collaborative import get_recommender
        rated = [(d["tmdb_id"], d["rating"]) for d in detail]
        collab = get_recommender().recommend(rated, n=n * mult)
    except Exception:
        collab = []

    # Fusão por rank recíproco (RRF): conteúdo manda, colaborativo complementa.
    score: dict[int, float] = defaultdict(float)
    for i, (tid, _s) in enumerate(content):
        score[tid] += CONTENT_RRF_W / (RRF_K + i)
    for i, r in enumerate(collab):
        score[int(r["tmdb_id"])] += COLLAB_RRF_W / (RRF_K + i)

    cat = catalog.get_catalog()
    ranked_all = sorted((t for t in score if t not in prof.seen), key=lambda t: -score[t])
    if filtering:
        from core import tmdb
        ranked = tmdb.filter_available(ranked_all, provider_ids, region, limit=n)
    else:
        ranked = ranked_all[:n]

    recs = []
    for tid in ranked:
        mv = cat.get(tid, {})
        recs.append({
            "tmdb_id": int(tid),
            "title": mv.get("title"),
            "release_year": mv.get("release_year"),
            "vote_average": mv.get("vote_average"),
            "overview": (mv.get("overview") or "")[:240],
            "why": prof._why(tid, summary),
            "method": "perfil multi-interesse (conteúdo + colaborativo)",
        })
    return {"profile": summary, "recommendations": recs}
