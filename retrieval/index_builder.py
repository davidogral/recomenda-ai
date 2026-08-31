"""ConstrÃ³i e serializa os Ã­ndices de busca por sinopse dos 22k filmes.

Gera, em `retrieval/index/`:
  - `movie_ids.npy`         ordem das linhas (tmdb_id) â€” compartilhada por todos os Ã­ndices
  - `bm25_vectorizer.pkl`   CountVectorizer (stopwords PT) ajustado nas sinopses
  - `bm25_counts.npz`       matriz de contagens esparsa (sinal lexical BM25)
  - `embeddings.npy`        embeddings multilÃ­ngues L2-normalizados (float32, NÃ—384)
  - `kw_embeddings.npy`     embeddings temÃ¡ticos (gÃªneros+keywords) L2-normalizados
  - `meta.json`             metadados (modelo, dim, contagens, parÃ¢metros)

A lÃ³gica fica aqui (testÃ¡vel via CLI/notebook); o notebook
`research/build_search_index.ipynb` apenas chama `build_index()` e reporta.
"""

from __future__ import annotations

import json
import os
import pickle
import time
from typing import Optional

import numpy as np

from core import catalog, db

INDEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index")


def index_paths(index_dir: str = INDEX_DIR) -> dict[str, str]:
    """Todos os caminhos de um diretório de índice (permite índices alternativos,
    ex.: `retrieval/index_e5small/` para comparar modelos de embedding)."""
    j = lambda name: os.path.join(index_dir, name)
    return {
        "dir": index_dir,
        "movie_ids": j("movie_ids.npy"),
        "bm25_vectorizer": j("bm25_vectorizer.pkl"),
        "bm25_counts": j("bm25_counts.npz"),
        "embeddings": j("embeddings.npy"),
        "kw_embeddings": j("kw_embeddings.npy"),
        "keyword_term_emb": j("keyword_term_embeddings.npy"),
        "keyword_terms": j("keyword_terms.json"),
        "meta": j("meta.json"),
    }


# Constantes do diretório padrão (retrocompatibilidade).
_P = index_paths(INDEX_DIR)
MOVIE_IDS_PATH = _P["movie_ids"]
BM25_VECTORIZER_PATH = _P["bm25_vectorizer"]
BM25_COUNTS_PATH = _P["bm25_counts"]
EMBEDDINGS_PATH = _P["embeddings"]
KW_EMBEDDINGS_PATH = _P["kw_embeddings"]
KEYWORD_TERM_EMB_PATH = _P["keyword_term_emb"]
KEYWORD_TERMS_PATH = _P["keyword_terms"]
META_PATH = _P["meta"]

# Artefatos do Ã­ndice TF-IDF antigo (removidos no rebuild â€” agora usamos BM25).
_LEGACY_PATHS = [
    os.path.join(INDEX_DIR, "tfidf_vectorizer.pkl"),
    os.path.join(INDEX_DIR, "tfidf_matrix.npz"),
]

DEFAULT_EMBED_MODEL = "intfloat/multilingual-e5-large"


def embed_prefixes(model_name: str) -> tuple[str, str]:
    """(prefixo_consulta, prefixo_passagem) exigidos pelo modelo. Família E5 usa
    'query: ' / 'passage: '; demais modelos não usam prefixo."""
    name = (model_name or "").lower()
    if "e5" in name:
        return "query: ", "passage: "
    return "", ""

# Fallback caso o corpus do NLTK nÃ£o esteja disponÃ­vel (mantÃ©m o sistema
# funcionando offline). Lista enxuta de stopwords PT.
_PT_STOPWORDS_FALLBACK = [
    "a", "Ã ", "ao", "aos", "as", "Ã s", "com", "como", "da", "das", "de", "do",
    "dos", "e", "Ã©", "em", "entre", "essa", "esse", "esta", "este", "eu", "foi",
    "isso", "mais", "mas", "me", "mesmo", "na", "nas", "no", "nos", "nÃ£o", "o",
    "os", "ou", "para", "pela", "pelo", "por", "que", "se", "sem", "ser", "seu",
    "sua", "sÃ£o", "tambÃ©m", "te", "tem", "um", "uma", "vocÃª", "Ã ",
]


def portuguese_stopwords() -> list[str]:
    """Stopwords PT do NLTK (como no notebook legado); fallback baked-in."""
    try:
        from nltk.corpus import stopwords

        try:
            return stopwords.words("portuguese")
        except LookupError:
            import nltk

            nltk.download("stopwords", quiet=True)
            return stopwords.words("portuguese")
    except Exception:
        return list(_PT_STOPWORDS_FALLBACK)


# Sinônimos PT para keywords de atributo da TMDB (que vêm em inglês). Quando o
# filme tem a keyword à esquerda, injetamos o texto PT à direita no documento
# temático — assim uma consulta PT ("preto e branco", "mudo") casa o atributo.
_ATTR_KEYWORD_PT = {
    "black and white": "preto e branco",
    "silent film": "filme mudo cinema mudo sem diálogo",
    "based on a true story": "baseado em fatos reais história real",
    "based on novel or book": "baseado em livro adaptação literária",
    "based on comic": "baseado em quadrinhos hq",
    "stop motion": "stop motion animação quadro a quadro",
    "anime": "anime animação japonesa",
    "found footage": "found footage filmagem encontrada",
    "mockumentary": "falso documentário",
    "cult film": "filme cult",
    "remake": "refilmagem remake",
    "sequel": "continuação sequência",
    "based on video game": "baseado em videogame jogo",
}


def _attribute_phrases(mv: dict) -> list[str]:
    """Atributos estruturados (em PT) que NÃO estão na sinopse mas o usuário usa
    para descrever o filme: época/década, era do mudo/preto-e-branco, qualidade
    (bom/ruim), duração. Derivados de release_year, vote_average e runtime."""
    out: list[str] = []
    year = mv.get("release_year")
    if year:
        decade = (int(year) // 10) * 10
        out += [f"década de {decade}", f"filme dos anos {decade}", f"de {year}"]
        if year <= 1929:
            out += ["filme mudo", "cinema mudo", "preto e branco", "filme muito antigo"]
        elif year <= 1935:
            out += ["preto e branco", "filme antigo clássico"]
        elif year <= 1969:
            out.append("filme antigo clássico")
        if year >= 2018:
            out.append("filme recente atual")
    va = mv.get("vote_average") or 0
    vc = mv.get("vote_count") or 0
    if va >= 7.8 and vc >= 300:
        out.append("filme aclamado muito bom premiado obra-prima")
    elif va >= 7.0:
        out.append("filme bem avaliado bom")
    elif va and va <= 4.5 and vc >= 80:
        out.append("filme ruim mal avaliado fraco")
    rt = mv.get("runtime_minutes")
    if rt and rt < 45:
        out.append("curta-metragem curta")
    elif rt and rt >= 150:
        out.append("filme longo épico")
    return out


def _build_keyword_documents(ids: np.ndarray) -> list[str]:
    """Documento temÃ¡tico por filme: gÃªneros + keywords da TMDB + atributos.

    As keywords ("time loop", "memory loss", "viagem no tempo") sÃ£o o gancho que
    casa com descriÃ§Ãµes de enredo. Vira um **embedding separado** (nÃ£o Ã© misturado
    Ã  sinopse, pra nÃ£o diluir o embedding principal nem injetar o ruÃ­do de keywords
    irrelevantes â€” "alarm clock", "telecaster" â€” no texto da sinopse)."""
    gmap = catalog.genres_by_movie()
    kmap = catalog.keywords_by_movie()
    cat = catalog.get_catalog()
    people = _people_documents(ids)
    docs: list[str] = []
    for t in ids.tolist():
        tid = int(t)
        kws = kmap.get(tid, [])
        parts = list(gmap.get(tid, [])) + list(kws)
        for kw in kws:  # sinônimo PT p/ keywords de atributo (vêm em inglês)
            pt = _ATTR_KEYWORD_PT.get(kw.lower())
            if pt:
                parts.append(pt)
        parts += _attribute_phrases(cat.get(tid, {}))  # década, p&b, mudo, qualidade...
        if people.get(tid):
            parts.append(people[tid])                    # diretor + elenco principal
        docs.append(" ".join(parts))
    return docs


def _people_documents(ids: np.ndarray, n_cast: int = 6) -> dict[int, str]:
    """Por filme: diretor(es) + elenco principal (por ordem de crédito). Permite
    que uma busca em texto livre citando pessoas ('filme do scorsese sobre máfia')
    case o filme certo, sem depender só dos campos de diretor/ator."""
    from collections import defaultdict

    directors: dict[int, list[str]] = defaultdict(list)
    cast: dict[int, list[tuple[int, str]]] = defaultdict(list)
    rows = db.query(
        "SELECT mp.tmdb_id AS tmdb_id, p.name AS name, mp.role AS role, "
        "mp.credit_order AS credit_order "
        "FROM movie_people mp JOIN people p ON p.person_id = mp.person_id "
        "WHERE mp.role IN ('actor', 'director')"
    )
    for r in rows:
        if r["role"] == "director":
            directors[r["tmdb_id"]].append(r["name"])
        else:
            order = r["credit_order"] if r["credit_order"] is not None else 999
            cast[r["tmdb_id"]].append((order, r["name"]))
    out: dict[int, str] = {}
    for t in ids.tolist():
        tid = int(t)
        names = [nm for _o, nm in sorted(cast.get(tid, []))[:n_cast]]
        parts = []
        if directors.get(tid):
            parts.append("dirigido por " + ", ".join(directors[tid][:2]))
        if names:
            parts.append("com " + ", ".join(names))
        out[tid] = " ".join(parts)
    return out


def build_index(
    embed_model_name: str = DEFAULT_EMBED_MODEL,
    with_embeddings: bool = True,
    limit: Optional[int] = None,
    batch_size: int = 64,
    show_progress: bool = True,
    index_dir: str = INDEX_DIR,
) -> dict:
    """ConstrÃ³i os Ã­ndices e salva em `index_dir` (padrÃ£o `retrieval/index/`).

    Gera, com `with_embeddings`, **dois** espaÃ§os de embedding: o da sinopse
    (`embeddings.npy`) e o temÃ¡tico de keywords/gÃªneros (`kw_embeddings.npy`),
    combinados na busca. O sinal lexical (BM25) Ã© sÃ³ da sinopse.
    `limit` restringe aos N primeiros filmes; `with_embeddings=False` gera sÃ³ BM25.
    `index_dir` != o padrÃ£o permite Ã­ndices alternativos (ex.: e5-small) sem
    sobrescrever o de produÃ§Ã£o.
    """
    from retrieval.bm25 import BM25Index

    P = index_paths(index_dir)
    os.makedirs(index_dir, exist_ok=True)

    df = catalog.get_catalog_df()
    if limit is not None:
        df = df.head(limit)

    ids = df["tmdb_id"].to_numpy(dtype=np.int64)
    docs = df["overview"].fillna("").tolist()  # sinopse pura: base do BM25 e do embedding principal
    n = len(docs)

    # --- BM25 lexical (CountVectorizer com stopwords PT, sem acento) ---
    t0 = time.time()
    import unicodedata
    def _noacc(s):
        return "".join(c for c in unicodedata.normalize("NFD", s)
                       if unicodedata.category(c) != "Mn")
    # O BM25 tira acento dos tokens; as stopwords precisam vir sem acento também.
    stop = sorted({_noacc(w) for w in portuguese_stopwords()})
    bm25 = BM25Index.build(docs, stop_words=stop, strip_accents="unicode")
    bm25_secs = round(time.time() - t0, 1)

    np.save(P["movie_ids"], ids)
    bm25.save(P["bm25_vectorizer"], P["bm25_counts"])
    for legacy in _LEGACY_PATHS:  # limpa o Ã­ndice TF-IDF antigo, se existir
        if os.path.exists(legacy):
            os.remove(legacy)

    meta = {
        "index_dir": os.path.relpath(index_dir, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "n_movies": int(n),
        "lexical": "bm25",
        "bm25_vocab_size": int(bm25.vocab_size),
        "bm25_k1": bm25.k1,
        "bm25_b": bm25.b,
        "bm25_build_secs": bm25_secs,
        "has_embeddings": False,
        "has_keyword_embeddings": False,
        "has_keyword_terms": False,
        "embed_model": None,
        "embed_dim": None,
        "built_at": int(time.time()),
    }

    # --- Embeddings multilÃ­ngues (L2-normalizados): sinopse + temÃ¡tico ---
    if with_embeddings:
        from sentence_transformers import SentenceTransformer

        from core.device import get_device

        t0 = time.time()
        model = SentenceTransformer(embed_model_name, device=get_device())
        q_pref, p_pref = embed_prefixes(embed_model_name)

        def _encode_passages(texts: list[str]) -> np.ndarray:
            payload = [f"{p_pref}{t}" for t in texts] if p_pref else texts
            return model.encode(
                payload, batch_size=batch_size, normalize_embeddings=True,
                show_progress_bar=show_progress, convert_to_numpy=True,
            ).astype(np.float32)

        emb = _encode_passages(docs)
        np.save(P["embeddings"], emb)

        kw_docs = _build_keyword_documents(ids)
        kw_emb = _encode_passages(kw_docs)
        np.save(P["kw_embeddings"], kw_emb)

        # Embedding por keyword distinta (nÃ£o por filme): permite, na explicaÃ§Ã£o,
        # dizer QUAIS keywords temÃ¡ticas casaram com a consulta â€” de forma
        # multilÃ­ngue (a consulta em PT casa "time loop"/"viagem no tempo").
        kw_rows = db.query("SELECT keyword_id, name FROM keywords ORDER BY keyword_id")
        kw_names = [r["name"] for r in kw_rows]
        kw_term_emb = _encode_passages(kw_names)
        np.save(P["keyword_term_emb"], kw_term_emb)
        with open(P["keyword_terms"], "w", encoding="utf-8") as f:
            json.dump(kw_names, f, ensure_ascii=False)

        meta.update(
            embed_query_prefix=q_pref,
            embed_passage_prefix=p_pref,
            has_embeddings=True,
            has_keyword_embeddings=True,
            has_keyword_terms=True,
            n_keyword_terms=len(kw_names),
            embed_model=embed_model_name,
            embed_dim=int(emb.shape[1]),
            embed_build_secs=round(time.time() - t0, 1),
        )
    else:
        # Remove embeddings antigos para nÃ£o dessincronizar com movie_ids.
        for path in (P["embeddings"], P["kw_embeddings"],
                     P["keyword_term_emb"], P["keyword_terms"]):
            if os.path.exists(path):
                os.remove(path)

    with open(P["meta"], "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="ConstrÃ³i o Ã­ndice de busca por sinopse.")
    p.add_argument("--no-embeddings", action="store_true", help="SÃ³ TF-IDF.")
    p.add_argument("--limit", type=int, default=None, help="Limitar a N filmes.")
    p.add_argument("--model", default=DEFAULT_EMBED_MODEL)
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--index-dir", default=INDEX_DIR)
    args = p.parse_args()

    info = build_index(
        embed_model_name=args.model,
        index_dir=args.index_dir,
        with_embeddings=not args.no_embeddings,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
