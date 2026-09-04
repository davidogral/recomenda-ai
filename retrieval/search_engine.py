"""Recuperação de filmes — achar um filme que o usuário conhece mas não lembra.

Sistema **independente** da recomendação (não usa ratings). Modos combináveis:
  - nome fuzzy (rapidfuzz sobre títulos)
  - sinopse híbrida (TF-IDF PT + embeddings multilíngues, scores fundidos)
  - pessoa (ator/diretor via movie_people)
  - keyword/tema (via movie_keywords)
Com filtros opcionais de ano, gênero e idioma.

O índice de sinopse é gerado por `retrieval/index_builder.py`. Se ele não
existir, a busca por sinopse degrada graciosamente (TF-IDF only, ou indisponível),
mas nome/pessoa/keyword continuam funcionando.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import OrderedDict
from typing import Any, Optional

import numpy as np

from core import catalog, db
from core import metrics as _metrics
from retrieval import index_builder as ib

# Artigos PT iniciais: ruído no casamento de nome ("O pescotapa" casava todo
# "O ..."). Removidos antes do fuzzy, dos dois lados (consulta e título).
_PT_ARTICLES = {"o", "a", "os", "as", "um", "uma", "uns", "umas"}

# Stopwords PT (com acento removido) — usadas para (1) limpar muletas no começo
# de consultas descritivas e (2) medir cobertura de palavras de conteúdo no
# casamento de nome, para que compartilhar só "uma/homem/com" não vire 90%.
_PT_STOP = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "e", "em", "no", "na", "nos", "nas", "com", "sem", "sob", "sobre",
    "por", "para", "pra", "que", "qual", "quais", "quem", "onde", "como",
    "quando", "ao", "aos", "se", "seu", "sua", "seus", "suas", "ele", "ela",
    "eles", "elas", "este", "esta", "esse", "essa", "aquele", "aquela",
    "aqueles", "aquelas", "isto", "isso", "aquilo", "meu", "minha", "filme",
    "filmes", "longa", "longas", "desenho", "animacao", "serie", "cinema",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _content_tokens(s: str) -> list[str]:
    """Tokens de conteúdo (sem acento, >2 chars, fora das stopwords)."""
    return [t for t in _strip_accents((s or "").lower()).split()
            if len(t) > 2 and t not in _PT_STOP]


def clean_descriptive_query(q: str) -> str:
    """Remove muletas iniciais de consultas descritivas ('Filme da ...',
    'aquele filme que ...', 'qual o filme ...') — só a sequência inicial de
    stopwords, parando na 1ª palavra de conteúdo. Mantém ao menos um token.

    Medido: 'Filme da mulher de cabelo branco...' ranqueava o Frozen em #562;
    sem o 'Filme da', #63 (o embedding é sensível ao lixo no começo)."""
    toks = (q or "").strip().split()
    i = 0
    while i < len(toks) - 1 and _strip_accents(toks[i].lower().strip("?¿!.,;:")) in _PT_STOP:
        i += 1
    cleaned = " ".join(toks[i:]).strip(" ?¿!.,;:")
    return cleaned or (q or "").strip()


def _name_key(s: str) -> str:
    """Normaliza um título/consulta p/ casamento de nome: minúsculas + remove o
    artigo inicial ('O Psicopata Americano' ~ 'Psicopata Americano')."""
    toks = (s or "").lower().split()
    if toks and toks[0] in _PT_ARTICLES:
        toks = toks[1:]
    return " ".join(toks)


def _alnum_key(s: str) -> str:
    """Chave de comparação de título: sem acento, sem pontuação, minúscula, sem o
    artigo inicial. Faz 'spider man' == 'Spider-Man' (o hífen não penaliza mais)."""
    s = re.sub(r"[^a-z0-9]+", " ", _strip_accents((s or "").lower())).strip()
    toks = s.split()
    if toks and toks[0] in _PT_ARTICLES:
        toks = toks[1:]
    return " ".join(toks)


def _name_score(query_key: str, title: str) -> float:
    """Score de nome (0–1+): WRatio (tolera typo/partial) com duas penalizações.

    1. **Cobertura de caracteres**: derruba títulos muito mais curtos que a
       consulta (ruído de fragmento — "Amer" p/ "amerecano").
    2. **Cobertura de conteúdo**: a fração das palavras de conteúdo da consulta
       (sem stopwords) que aparecem no título. Sem isso o WRatio dá ~0.85 a
       qualquer título que compartilhe só "uma/homem/com" com a consulta — o que
       inflava a confiança dos candidatos errados. Compartilhar nenhuma palavra
       de conteúdo zera quase tudo; compartilhar todas não penaliza.

    Bônus de match exato/prefixo (que já implicam cobertura total) preservados."""
    from rapidfuzz import fuzz

    tk = _name_key(title)
    base = fuzz.WRatio(query_key, tk) / 100.0
    if tk == query_key:
        return base + 0.5
    if tk and tk.startswith(query_key):
        return base + 0.15

    char_cov = min(1.0, len(tk) / max(len(query_key), 1))
    q_content = _content_tokens(query_key)
    if q_content:
        title_content = set(_content_tokens(title))
        shared = sum(1 for t in q_content if t in title_content)
        word_cov = shared / len(q_content)
    else:
        word_cov = 1.0  # consulta só de stopwords: nada a cobrir
    return base * (0.4 + 0.6 * char_cov) * (0.2 + 0.8 * word_cov)


# Rótulos dos sinais para a explicação exibida ao usuário.
SIGNAL_LABELS = {
    "lexical": "termos da sinopse",
    "synopsis": "sentido da sinopse",
    "keyword": "tema",
    "entity": "personagem",
    "plot": "enredo detalhado",
    "plot_lexical": "enredo (texto)",
    "plot_maxsim": "enredo (trecho)",
    "name": "nome",
}

# Pesos default da fusão na busca por sinopse: TF-IDF + embedding da sinopse +
# embedding temático (keywords/gêneros). O embedding da sinopse é o sinal mais
# confiável no caso geral; o lexical (TF-IDF) resgata enredos com termos próprios
# ("sete pecados capitais", "revivendo o mesmo dia") e o temático resgata casos
# de conceito ("time loop", "memory loss") que a sinopse sozinha não pega.
# Pesos da fusão calibrados no harness de 52 casos com o modelo e5-base.
# O tema/keyword carrega também os ATRIBUTOS (preto-e-branco, mudo, década,
# qualidade). A fusão usa ReLU (ver _synopsis_components): cada sinal só SOMA
# evidência quando está acima da média — nunca pune um filme por estar "na média"
# num sinal (era o que derrubava Forrest Gump de #12 para #136).
DEFAULT_LEXICAL_WEIGHT = 0.25   # consultas curtas (ver _adaptive_lexical_weight)
DEFAULT_EMBED_WEIGHT = 0.6
DEFAULT_KEYWORD_WEIGHT = 0.5

# Canal de ENTIDADE: casa (fuzzy, Jaro-Winkler) uma consulta CURTA com nomes de
# personagem do elenco de topo ("Toretto", "Roman Pearce", "Jonh wick"). É assim
# que o usuário digita nome de personagem; paráfrase de enredo é longa, não cita
# personagem e não paga o custo do rapidfuzz. Medido: split `entity` do eval sobe
# nDCG@10 ~0.49 -> ~0.74; test/dev não mudam (a consulta longa não dispara). 0 desliga.
DEFAULT_ENTITY_WEIGHT = float(os.environ.get("RECOMENDAI_ENTITY_WEIGHT", "0.45"))
_ENTITY_MAX_QUERY_TOKENS = int(os.environ.get("RECOMENDAI_ENTITY_MAX_TOKENS", "6"))

# Canal do ENREDO da Wikipédia — embedding do texto de "Plot/Enredo" num 4º
# espaço vetorial. Ablação de 2026-09-04 (split `object`): neutro-a-NEGATIVO em
# toda a faixa de peso (0.15→0.4 dá 0.107→0.098 de nDCG@10 vs 0.129 sem ele) — o
# e5 trunca em 512 tokens e só "vê" o 1º ato; objeto/cena icônicos ficam no 3º.
# OFF por padrão. O texto do enredo entra pelo sinal LEXICAL abaixo.
DEFAULT_PLOT_WEIGHT = float(os.environ.get("RECOMENDAI_PLOT_WEIGHT", "0.0"))

# Canal do ENREDO (lexical): BM25 sobre o texto do "Plot" da Wikipédia. O enredo
# nomeia objeto/carro/lugar ("Nissan Skyline GT-R", "DeLorean", "green light") —
# nome próprio sobrevive à tradução, então "skyline azul" casa pelo termo
# distintivo mesmo com o texto em inglês. Sem truncamento de tokens. Só existe se
# o índice tem `bm25_plot_*` (index_builder --plot-bm25-only). 0 desliga.
DEFAULT_PLOT_BM25_WEIGHT = float(os.environ.get("RECOMENDAI_PLOT_BM25_WEIGHT", "0.0"))

# Canal do ENREDO (trecho/MaxSim): o enredo é fatiado em janelas de ~380 palavras
# e cada uma é um embedding; o score do filme é o MÁXIMO sobre os trechos. Assim
# um objeto/cena do 3º ato ainda casa — o embedding do plot inteiro perde isso
# (e5 trunca em 512 tokens). Só existe com `plot_chunk_*` no índice
# (index_builder --plot-chunks-only). 0 desliga.
DEFAULT_PLOT_CHUNK_WEIGHT = float(os.environ.get("RECOMENDAI_PLOT_CHUNK_WEIGHT", "0.0"))

# Prior de popularidade/aclamação (z-score de log(vote_count)). Desempata a favor
# do filme famoso quando muitos casam parecido com uma descrição genérica (ex.:
# "ascensão e queda de um gângster" → dezenas de filmes de máfia). Configurável:
# 0 desliga; ~0.3 moderado; ~0.5 agressivo (enterra obscuros). Ver harness.
DEFAULT_POP_PRIOR = float(os.environ.get("RECOMENDAI_POP_PRIOR", "0.35"))

# Re-ranker cross-encoder (2º estágio): reordena o top-K da 1ª etapa misturando
# o score do cross-encoder com o da recuperação (blend) como prior estabilizador.
#
# DESLIGADO POR PADRÃO (produção). A varredura de `eval/` (split de teste e de
# calibração, ver eval/results/latest__sweep-rerank-*.json) mostra que o
# cross-encoder **não melhora de forma confiável**: no teste o pool 50 sobe o
# nDCG@10 de 0.733 → 0.754; no dev ele CAI de 0.823 → 0.814 — variação dentro do
# ruído para n=47–95. E o pool 300 (o default antigo) piora nos dois splits e
# ainda derruba o Recall@50, a ~1.8–2.2 s por busca (≈250× a latência da fusão
# sozinha, ~8 ms). Para produção, a fusão sem re-ranker já entrega mediana da
# posição #1.
#
# Para ligar num experimento: `RECOMENDAI_RERANK=1`. Se ligar, o pool 50 é a
# única configuração que não regride (300 é estritamente pior em todas as rodadas).
RERANK_POOL = int(os.environ.get("RECOMENDAI_RERANK_POOL", "50"))
RERANK_BLEND = float(os.environ.get("RECOMENDAI_RERANK_BLEND", "0.5"))
RERANK_ENABLED = os.environ.get("RECOMENDAI_RERANK", "0").lower() not in ("0", "false", "no")

# Cache LRU do embedding da consulta (por string já com prefixo). Consultas
# repetem MUITO entre usuários ("filme do homem que perde a memória"); reusar o
# vetor economiza o forward do transformer (~15–40 ms). Limite evita o dict
# crescer sem fim num processo de vida longa. 0 desliga.
QUERY_EMB_CACHE_SIZE = int(os.environ.get("RECOMENDAI_QUERY_CACHE", "4096"))

# Fallback de título via TMDB: resolve títulos em qualquer idioma (ex.: inglês) e
# tolera digitação aproximada, mapeando o resultado para o catálogo. Só dispara
# quando o casamento LOCAL (títulos PT) é fraco — economiza chamadas — e a consulta
# é curta (parece um título, não uma descrição de enredo). A confiança vem da
# similaridade da consulta ao title/original_title que a própria TMDB devolve.
TMDB_NAME_FALLBACK = os.environ.get("RECOMENDAI_TMDB_NAMES", "1").lower() not in ("0", "false", "no")
TMDB_LOCAL_GOOD = 0.93      # casamento local já ótimo → nem chama a TMDB
TMDB_TITLE_CUTOFF = 0.55    # similaridade mínima do título TMDB à consulta
TMDB_MAX_TITLE_WORDS = 8    # acima disso é descrição de enredo, não título


def _zscore(x: np.ndarray) -> np.ndarray:
    """Padroniza um vetor de scores (média 0, desvio 1).

    Fundir por z-score em vez de min-max+soma tem duas vantagens medidas no
    harness: (1) é robusto a outliers — um único filme com score altíssimo não
    achata todos os outros perto de zero; (2) preserva o quanto um sinal
    *separa* um filme da média, então um enredo com termo próprio muito forte
    (lexical) ou um tema muito específico (keywords) é resgatado mesmo quando o
    embedding da sinopse é fraco para aquela consulta."""
    if x.size == 0:
        return x
    std = float(x.std())
    if std <= 0:
        return np.zeros_like(x)
    return (x - float(x.mean())) / std


class SearchEngine:
    """Motor de recuperação. Carrega catálogo + índice de sinopse (se houver)."""

    def __init__(self, load_embeddings: bool = True, rerank: bool = RERANK_ENABLED,
                 index_dir: Optional[str] = None):
        # `index_dir` != o padrão permite carregar um índice alternativo (ex.:
        # e5-small) sem tocar no de produção — usado pelo benchmark eval/bench.py.
        # Em produção, `RECOMENDAI_INDEX_DIR` troca o índice sem mexer no código.
        self._index_dir = index_dir or os.environ.get("RECOMENDAI_INDEX_DIR") or ib.INDEX_DIR
        if not os.path.isabs(self._index_dir):
            self._index_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self._index_dir)
        self.catalog = catalog.get_catalog()  # {tmdb_id: filme}
        # Títulos para fuzzy (lista paralela id<->título).
        self._title_ids: list[int] = []
        self._titles: list[str] = []
        for tid, mv in self.catalog.items():
            self._title_ids.append(tid)
            self._titles.append(mv["title"] or "")

        # Índice de sinopse (carregado sob demanda / no init se existir).
        self._movie_ids: Optional[np.ndarray] = None      # ordem das linhas
        self._bm25 = None                                  # BM25Index (sinal lexical)
        self._embeddings: Optional[np.ndarray] = None      # N×D sinopse (L2-norm)
        self._kw_embeddings: Optional[np.ndarray] = None   # N×D temático (L2-norm)
        self._plot_embeddings: Optional[np.ndarray] = None  # N×D enredo Wikipédia (L2-norm)
        self._bm25_plot = None                             # BM25Index sobre o texto do enredo
        self._plot_chunk_emb: Optional[np.ndarray] = None   # M×D trechos de enredo (L2-norm)
        self._plot_chunk_rows: Optional[np.ndarray] = None  # M -> linha do filme em _movie_ids
        self._kw_term_emb: Optional[np.ndarray] = None     # Nkw×D por keyword (L2-norm)
        self._kw_term_row: dict[str, int] = {}             # nome(lower) -> linha em _kw_term_emb
        self._embed_model = None
        self._embed_model_name: Optional[str] = None
        self._embed_query_prefix: str = ""  # prefixo do lado-consulta (ex.: E5 "query: ")
        self._query_emb_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()  # LRU
        self._query_emb_hits = 0
        self._query_emb_misses = 0
        self._load_embeddings = load_embeddings
        # Re-ranker cross-encoder (2º estágio), carregado sob demanda.
        self.rerank_enabled = rerank
        self._reranker = None
        self._reranker_failed = False
        # Query-SLM opcional (tradução PT→EN p/ o sinal de keyword), off por padrão.
        from retrieval.query_expander import QUERY_SLM_ENABLED
        self.query_slm_enabled = QUERY_SLM_ENABLED
        self._query_expander = None
        self._query_expander_failed = False
        self._load_index()

        # tmdb_id -> linha em self._movie_ids (para ler scores de sinopse).
        self._row_index_cache: Optional[dict[int, int]] = None
        self._pop_prior_cache: Optional[np.ndarray] = None  # z-score de log(vote_count)
        # Mapas reversos para pessoa/keyword (construídos sob demanda).
        self._person_movies: Optional[dict[int, list[int]]] = None
        self._keyword_movies: Optional[dict[int, list[int]]] = None
        self._people_names: Optional[list[tuple[int, str]]] = None
        self._keyword_names: Optional[list[tuple[int, str]]] = None

    # ------------------------------------------------------------------ índice
    def _load_index(self) -> None:
        P = ib.index_paths(self._index_dir)
        if not os.path.exists(P["meta"]):
            return
        import json

        with open(P["meta"], encoding="utf-8") as f:
            meta = json.load(f)
        self._embed_query_prefix = meta.get("embed_query_prefix", "") or ""
        self._movie_ids = np.load(P["movie_ids"])
        from retrieval.bm25 import BM25Index

        self._bm25 = BM25Index.load(
            P["bm25_vectorizer"], P["bm25_counts"],
            k1=meta.get("bm25_k1", 1.5), b=meta.get("bm25_b", 0.75),
        )
        if meta.get("has_plot_bm25") and os.path.exists(P["bm25_plot_vectorizer"]):
            self._bm25_plot = BM25Index.load(
                P["bm25_plot_vectorizer"], P["bm25_plot_counts"],
                k1=meta.get("bm25_k1", 1.5), b=meta.get("bm25_b", 0.75),
            )
        if self._load_embeddings and meta.get("has_embeddings") and os.path.exists(P["embeddings"]):
            self._embeddings = np.load(P["embeddings"])
            self._embed_model_name = meta.get("embed_model")
            if meta.get("has_keyword_embeddings") and os.path.exists(P["kw_embeddings"]):
                self._kw_embeddings = np.load(P["kw_embeddings"])
            if meta.get("has_plot_embeddings") and os.path.exists(P["plot_embeddings"]):
                self._plot_embeddings = np.load(P["plot_embeddings"])
            if meta.get("has_plot_chunks") and os.path.exists(P["plot_chunk_emb"]):
                self._plot_chunk_emb = np.load(P["plot_chunk_emb"])
                self._plot_chunk_rows = np.load(P["plot_chunk_rows"])
            if meta.get("has_keyword_terms") and os.path.exists(P["keyword_term_emb"]):
                self._kw_term_emb = np.load(P["keyword_term_emb"])
                with open(P["keyword_terms"], encoding="utf-8") as f:
                    names = json.load(f)
                self._kw_term_row = {nm.lower(): i for i, nm in enumerate(names)}

    @property
    def has_synopsis_index(self) -> bool:
        return self._bm25 is not None

    def _get_embed_model(self):
        """Encoder da consulta. `RECOMENDAI_EMBED_BACKEND`:
          - `st` (padrão) → sentence-transformers (fp32, usa MPS/CUDA se houver);
          - `onnx-int8` / `onnx-fp32` → ONNX Runtime (CPU), pesos INT8 ou fp32.
        O modelo é o mesmo que gerou o índice (`meta.embed_model`)."""
        if self._embed_model is None:
            name = self._embed_model_name or ib.DEFAULT_EMBED_MODEL
            backend = os.environ.get("RECOMENDAI_EMBED_BACKEND", "st").lower()
            if backend.startswith("onnx"):
                from retrieval.onnx_embed import OnnxEncoder

                self._embed_model = OnnxEncoder.from_env(name)
            else:
                from sentence_transformers import SentenceTransformer

                from core.device import get_device

                self._embed_model = SentenceTransformer(name, device=get_device())
        return self._embed_model

    def _get_reranker(self):
        """Carrega o cross-encoder sob demanda; desativa em silêncio se falhar."""
        if not self.rerank_enabled or self._reranker_failed:
            return None
        if self._reranker is None:
            try:
                from retrieval.reranker import CrossEncoderReranker

                self._reranker = CrossEncoderReranker()
            except Exception:
                self._reranker_failed = True
                return None
        return self._reranker

    def _get_query_expander(self):
        """Query-SLM (tradução) sob demanda; desativa em silêncio se falhar."""
        if not self.query_slm_enabled or self._query_expander_failed:
            return None
        if self._query_expander is None:
            try:
                from retrieval.query_expander import QueryExpander

                self._query_expander = QueryExpander()
            except Exception:
                self._query_expander_failed = True
                return None
        return self._query_expander

    def _keyword_query_emb(self, query: str, q_emb: np.ndarray) -> np.ndarray:
        """Embedding da consulta para o sinal de keyword. Com o query-SLM ligado,
        usa a tradução EN (keywords da TMDB são em inglês); senão, o embedding PT."""
        qx = self._get_query_expander()
        if qx is None:
            return q_emb
        try:
            return self._encode(qx.translate(query))
        except Exception:
            self._query_expander_failed = True
            return q_emb

    def _text_for(self, tmdb_id: int) -> str:
        """Passagem para o cross-encoder: a sinopse; se vazia, título + keywords."""
        mv = self.catalog.get(tmdb_id, {})
        overview = (mv.get("overview") or "").strip()
        if overview:
            return overview
        kws = " ".join(catalog.get_movie_keywords(tmdb_id)[:10])
        return f"{mv.get('title', '')} {kws}".strip()

    def _maybe_rerank(self, query: str, scored: list[tuple[int, float]], ctx: dict,
                      min_words: int = 4, pool: int = RERANK_POOL
                      ) -> list[tuple[int, float]]:
        """Reordena (com cross-encoder) o top-`pool` de `scored`, mantendo a cauda.
        Só age em consultas descritivas (>= `min_words` palavras) — para títulos
        curtos a 1ª etapa já é melhor."""
        rr = self._get_reranker()
        if rr is None or len(scored) < 2 or len(query.split()) < min_words:
            return scored
        head = scored[:pool]
        retr = {tid: s for tid, s in head}
        try:
            reordered = rr.rerank(query, [t for t, _ in head], self._text_for,
                                  retr_scores=retr, blend=RERANK_BLEND)
        except Exception:
            self._reranker_failed = True
            return scored
        ctx["reranked"] = True
        return reordered + scored[pool:]

    def _encode(self, query: str) -> np.ndarray:
        """Embedding L2-normalizado da consulta (cache **LRU** por string de consulta).

        Aplica o prefixo do lado-consulta exigido por alguns modelos (E5 usa
        'query: '); o índice foi gerado com o prefixo de passagem correspondente."""
        c = self._query_emb_cache
        cached = c.get(query)
        if cached is not None:
            c.move_to_end(query)
            self._query_emb_hits += 1
            _metrics.query_cache_event(hit=True)
            return cached
        self._query_emb_misses += 1
        _metrics.query_cache_event(hit=False)
        text = f"{self._embed_query_prefix}{query}" if self._embed_query_prefix else query
        vec = self._get_embed_model().encode(
            [text], normalize_embeddings=True, convert_to_numpy=True
        ).astype(np.float32)[0]
        if QUERY_EMB_CACHE_SIZE > 0:
            c[query] = vec
            if len(c) > QUERY_EMB_CACHE_SIZE:
                c.popitem(last=False)
        return vec

    def query_cache_stats(self) -> dict:
        """Diagnóstico do cache LRU do embedding da consulta."""
        tot = self._query_emb_hits + self._query_emb_misses
        return {
            "size": len(self._query_emb_cache),
            "capacity": QUERY_EMB_CACHE_SIZE,
            "hits": self._query_emb_hits,
            "misses": self._query_emb_misses,
            "hit_rate": round(self._query_emb_hits / tot, 3) if tot else 0.0,
        }

    def warmup(self, reranker: Optional[bool] = None) -> "SearchEngine":
        """Carrega os modelos pesados **agora** (no startup), não na 1ª requisição.

        Baixa/instancia o modelo de embeddings e roda um forward de aquecimento;
        se `reranker` (default = `self.rerank_enabled`), faz o mesmo com o
        cross-encoder. Idempotente."""
        if self.has_synopsis_index and self._embeddings is not None:
            try:
                self._get_embed_model()
                self._get_embed_model().encode(
                    [f"{self._embed_query_prefix}aquecimento"],
                    normalize_embeddings=True, convert_to_numpy=True)
            except Exception as e:  # pragma: no cover
                print(f"[warmup] embeddings falhou: {e}")
        want_rr = self.rerank_enabled if reranker is None else reranker
        if want_rr:
            rr = self._get_reranker()
            if rr is not None:
                try:
                    rr._get_model()
                except Exception as e:  # pragma: no cover
                    print(f"[warmup] cross-encoder falhou: {e}")
        return self

    # -------------------------------------------------------------- formatação
    def _format(self, tmdb_id: int, score: float) -> dict[str, Any]:
        mv = self.catalog.get(tmdb_id, {})
        overview = mv.get("overview", "") or ""
        return {
            "tmdb_id": int(tmdb_id),
            "title": mv.get("title"),
            "release_year": mv.get("release_year"),
            "original_language": mv.get("original_language"),
            "vote_average": mv.get("vote_average"),
            "overview": overview[:240],
            "score": round(float(score), 4),
        }

    # ------------------------------------------------------------------- filtros
    def _passes_filters(self, tmdb_id: int, filters: Optional[dict]) -> bool:
        if not filters:
            return True
        mv = self.catalog.get(tmdb_id)
        if mv is None:
            return False
        year = mv.get("release_year")
        if filters.get("year") is not None and year != filters["year"]:
            return False
        if filters.get("year_min") is not None and (year is None or year < filters["year_min"]):
            return False
        if filters.get("year_max") is not None and (year is None or year > filters["year_max"]):
            return False
        lang = filters.get("language")
        if lang is not None and (mv.get("original_language") or "").lower() != lang.lower():
            return False
        genre = filters.get("genre")
        if genre is not None:
            gset = {g.lower() for g in catalog.get_movie_genres(tmdb_id)}
            if genre.lower() not in gset:
                return False
        return True

    # =================================================================== modos
    def _local_name_scored(self, query: str, n: int, score_cutoff: float = 50.0
                           ) -> list[tuple[int, float]]:
        """Nome fuzzy via rapidfuzz sobre os títulos PT do catálogo (erro de
        digitação / nome parcial).

        `processor=_name_key` torna o casamento case-insensitive e ignora o artigo
        inicial (sem isso o `WRatio` dava "matrix"→"Animatrix" 90 > "Matrix" exato
        83). Um match **exato** ou de **prefixo** recebe bônus, para que o título
        buscado vença substrings mais longas que o contêm."""
        from rapidfuzz import fuzz, process

        results = process.extract(
            query, self._titles, scorer=fuzz.WRatio, processor=_name_key,
            limit=n * 8, score_cutoff=score_cutoff,
        )
        qk = _name_key(query)
        scored = [(self._title_ids[idx], _name_score(qk, self._titles[idx]))
                  for _title, _score, idx in results]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored

    def _tmdb_title_scores(self, query: str, n: int) -> tuple[dict[int, float], float]:
        """Filmes do catálogo achados pela TMDB para `query` (título em qualquer
        idioma, fuzzy), com a similaridade da consulta ao title/original_title
        devolvido pela TMDB como score. Devolve ({tmdb_id: score}, confiança)."""
        if not TMDB_NAME_FALLBACK or len(query.split()) > TMDB_MAX_TITLE_WORDS:
            return {}, 0.0
        from core import tmdb
        if not tmdb.is_configured():
            return {}, 0.0
        try:
            hits = tmdb.search_movies(query, limit=n * 2)
        except Exception:
            return {}, 0.0

        from rapidfuzz import fuzz
        qk = _alnum_key(query)
        out: dict[int, float] = {}
        # `hits` já vem ordenado por relevância/popularidade da TMDB; uma penalidade
        # leve por posição preserva essa ordem dentro dos títulos que casam (ex.: o
        # "Spider-Man" original vem antes dos derivados, sem afetar o filtro).
        for rank, h in enumerate(hits):
            tid = int(h.get("id") or 0)
            if tid not in self.catalog:
                continue  # só o que o sistema conhece (tem sinopse/pôster/dados)
            sim = max(fuzz.WRatio(qk, _alnum_key(h.get("title") or "")),
                      fuzz.WRatio(qk, _alnum_key(h.get("original_title") or ""))) / 100.0
            if sim >= TMDB_TITLE_CUTOFF:
                out[tid] = max(out.get(tid, 0.0), sim * (1.0 - 0.015 * rank))
        return out, (max(out.values()) if out else 0.0)

    def _name_scores(self, query: str, n: int, score_cutoff: float = 50.0
                     ) -> tuple[dict[int, float], float]:
        """Scores de nome (catálogo local + fallback TMDB) e a confiança do melhor
        casamento de título TMDB (0 se não houve). O TMDB só é consultado quando o
        casamento local é fraco — um título PT exato dispensa a rede."""
        scored = self._local_name_scored(query, n, score_cutoff)
        merged = dict(scored)
        conf = 0.0
        if (scored[0][1] if scored else 0.0) < TMDB_LOCAL_GOOD:
            tmdb_scores, conf = self._tmdb_title_scores(query, n)
            for tid, s in tmdb_scores.items():
                merged[tid] = max(merged.get(tid, 0.0), s)
        return merged, conf

    def search_by_name(self, query: str, n: int = 10, score_cutoff: float = 50.0
                       ) -> list[tuple[int, float]]:
        """Busca por nome: catálogo (PT) + fallback TMDB (qualquer idioma, fuzzy)."""
        merged, _ = self._name_scores(query, n, score_cutoff)
        return sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[: n * 4]

    def _adaptive_lexical_weight(self, query: str) -> float:
        """Peso do sinal lexical (BM25) conforme o tamanho da consulta. Consulta
        curta (poucas palavras de conteúdo) costuma ter termos próprios fortes →
        lexical ajuda; descrição longa é paráfrase do enredo (termos diferentes
        da sinopse) → BM25 demove o match semântico certo, então pesa pouco."""
        n = len(_content_tokens(query))
        if n <= 3:
            return 0.30
        if n <= 6:
            return 0.25
        return 0.20

    def _synopsis_components(self, query: str,
                            q_emb: Optional[np.ndarray] = None,
                            lexical_weight: Optional[float] = None,
                            embed_weight: float = DEFAULT_EMBED_WEIGHT,
                            keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
                            ) -> dict[str, np.ndarray]:
        """Contribuições **já ponderadas** de cada sinal de sinopse (alinhadas a
        `self._movie_ids`): BM25 (lexical), embedding da sinopse e embedding
        temático de keywords. Cada sinal é padronizado por z-score e multiplicado
        pelo seu peso, de modo que a soma das três é o score fundido.

        A consulta é limpa de muletas iniciais antes de tudo. `lexical_weight=None`
        usa o peso lexical adaptativo; um valor explícito (harness) o sobrepõe.

        Devolver os componentes (em vez de só a soma) é o que permite explicar
        ao usuário quanto cada sinal pesou em cada resultado (Objetivo 1)."""
        if self._bm25 is None:
            raise RuntimeError(
                "Índice de sinopse ausente. Rode retrieval/index_builder.py "
                "(ou research/build_search_index.ipynb) primeiro."
            )
        query = clean_descriptive_query(query)
        if lexical_weight is None:
            lexical_weight = self._adaptive_lexical_weight(query)
        n = self._bm25.n_docs
        lexical_scores = self._bm25.scores(query)
        # ReLU(z): cada sinal só SOMA evidência quando está acima da média; nunca
        # pune um filme por estar "na média" num sinal (com o e5 os cossenos são
        # comprimidos e altos, então o z-score cru dava negativo a bons matches e
        # os derrubava). Guardamos o z BRUTO (sufixo _z_) p/ a confiança absoluta.
        relu = lambda z: np.maximum(0.0, z)
        z_lex = _zscore(lexical_scores)
        zero = np.zeros(n, dtype=np.float64)
        comps = {
            "lexical": lexical_weight * relu(z_lex),
            "synopsis": zero.copy(),
            "keyword": zero.copy(),
            "entity": zero.copy(),
            "plot": zero.copy(),
            "plot_lexical": zero.copy(),
            "plot_maxsim": zero.copy(),
            # Prior de popularidade (sempre presente; pode ser negativo p/ obscuros).
            "prior": DEFAULT_POP_PRIOR * self._pop_prior_vec,
            "_z_synopsis": zero.copy(),
            "_z_keyword": zero.copy(),
        }
        if DEFAULT_PLOT_BM25_WEIGHT > 0 and self._bm25_plot is not None:
            comps["plot_lexical"] = DEFAULT_PLOT_BM25_WEIGHT * relu(_zscore(self._bm25_plot.scores(query)))
        ent = self._entity_scores(query)
        if ent is not None:
            comps["entity"] = DEFAULT_ENTITY_WEIGHT * relu(_zscore(ent))
        if self._embeddings is not None and (embed_weight > 0 or keyword_weight > 0):
            if q_emb is None:
                q_emb = self._encode(query)
            if embed_weight > 0:
                z_syn = _zscore(self._embeddings @ q_emb)
                comps["synopsis"] = embed_weight * relu(z_syn)
                comps["_z_synopsis"] = z_syn
            if keyword_weight > 0 and self._kw_embeddings is not None:
                kw_q = self._keyword_query_emb(query, q_emb)
                z_kw = _zscore(self._kw_embeddings @ kw_q)
                comps["keyword"] = keyword_weight * relu(z_kw)
                comps["_z_keyword"] = z_kw
            if DEFAULT_PLOT_WEIGHT > 0 and self._plot_embeddings is not None:
                comps["plot"] = DEFAULT_PLOT_WEIGHT * relu(_zscore(self._plot_embeddings @ q_emb))
            if DEFAULT_PLOT_CHUNK_WEIGHT > 0 and self._plot_chunk_emb is not None:
                cs = self._plot_chunk_emb @ q_emb  # (M,) sim por trecho
                ms = np.full(n, np.nan)
                np.fmax.at(ms, self._plot_chunk_rows, cs)  # max por filme; sem trecho -> nan
                have = ~np.isnan(ms)
                z = zero.copy()
                if have.any():
                    z[have] = _zscore(ms[have])
                comps["plot_maxsim"] = DEFAULT_PLOT_CHUNK_WEIGHT * relu(z)
        return comps

    def _synopsis_scores(self, query: str, **weights) -> np.ndarray:
        """Vetor de scores de sinopse fundido (ReLU dos sinais + prior)."""
        comps = self._synopsis_components(query, **weights)
        return comps["lexical"] + comps["synopsis"] + comps["keyword"] + comps["entity"] + comps["plot"] + comps["plot_lexical"] + comps["plot_maxsim"] + comps["prior"]

    def search_by_synopsis(self, query: str, n: int = 10, **weights) -> list[tuple[int, float]]:
        """Sinopse híbrida sobre todo o catálogo (top n*4 candidatos)."""
        fused = self._synopsis_scores(query, **weights)
        top = np.argsort(fused)[::-1][: n * 4]
        return [(int(self._movie_ids[i]), float(fused[i])) for i in top if fused[i] > 0]

    def synopsis_ranked_ids(self, query: str, rerank: bool = True,
                            pool: int = RERANK_POOL) -> list[int]:
        """Ordem completa (todos os tmdb_ids) da busca por sinopse, opcionalmente
        com o re-ranker aplicado ao top-`pool`. Usado pelo harness para medir a
        posição de qualquer alvo (mesmo fora do pool reordenado)."""
        fused = self._synopsis_scores(query)
        order = np.argsort(fused)[::-1]
        ids = [int(self._movie_ids[i]) for i in order]
        rr = self._get_reranker() if rerank else None
        if rr is not None and len(ids) > 1:
            retr = {ids[i]: float(fused[order[i]]) for i in range(min(pool, len(ids)))}
            try:
                reordered = rr.rerank(query, ids[:pool], self._text_for,
                                      retr_scores=retr, blend=RERANK_BLEND)
                ids = [t for t, _ in reordered] + ids[pool:]
            except Exception:
                self._reranker_failed = True
        return ids

    def _build_person_maps(self) -> None:
        person_movies: dict[int, list[int]] = {}
        for r in db.iter_query("SELECT person_id, tmdb_id FROM movie_people"):
            person_movies.setdefault(r["person_id"], []).append(r["tmdb_id"])
        self._person_movies = person_movies
        self._people_names = [
            (r["person_id"], r["name"]) for r in db.query("SELECT person_id, name FROM people")
        ]

    def _build_character_index(self) -> None:
        """Índice invertido `token de personagem -> {linha do filme}` do elenco de
        topo. Comparar os poucos tokens da consulta contra os ~dezenas de milhares
        de tokens distintos de personagem é barato; comparar contra cada nome
        inteiro não é."""
        tok_films: dict[str, set[int]] = {}
        pos = {int(t): i for i, t in enumerate(self._movie_ids)}
        for r in db.iter_query(
            "SELECT tmdb_id, character FROM movie_people "
            "WHERE role = 'actor' AND character IS NOT NULL AND character <> '' "
            "AND (credit_order IS NULL OR credit_order < 10)"
        ):
            row = pos.get(int(r["tmdb_id"]))
            if row is None:
                continue
            raw = re.sub(r"\s*\([^)]*\)\s*$", "", r["character"] or "")
            for tok in _strip_accents(raw.lower()).replace("/", " ").split():
                tok = tok.strip(".,'-’\"")
                if len(tok) >= 3 and tok not in _PT_STOP:
                    tok_films.setdefault(tok, set()).add(row)
        self._char_tok_films = tok_films
        self._char_toks = list(tok_films)

    def _entity_scores(self, query: str) -> Optional[np.ndarray]:
        """Vetor [0,1] alinhado a `self._movie_ids`: casa (fuzzy) os tokens de uma
        consulta **curta** com tokens de nome de personagem do elenco.

        - 1 token de conteúdo ("Toreto"): exige match quase-exato (typo de nome).
        - 2+ tokens ("Roman pearce"): pontua pela fração deles que o mesmo filme
          cobre, exigindo ao menos 2 — assim "Roman Pearce" só ganha de "Roman
          <qualquer>" se o filme tiver os dois.
        None para consulta longa (paráfrase de enredo) ou sem nenhum match."""
        if DEFAULT_ENTITY_WEIGHT <= 0 or self._bm25 is None:
            return None
        toks = [
            t for t in _strip_accents(query.lower()).split()
            if len(t) >= 3 and t not in _PT_STOP
        ]
        if not toks or len(query.split()) > _ENTITY_MAX_QUERY_TOKENS:
            return None
        if getattr(self, "_char_toks", None) is None:
            self._build_character_index()
        if not self._char_toks:
            return None
        # Jaro-Winkler tolera transposição/prefixo — o padrão dos typos reais
        # ("Jonh" wick, "Dogde", "Toreto"). Score já normalizado em [0, 1].
        from rapidfuzz import process
        from rapidfuzz.distance import JaroWinkler

        n = len(self._movie_ids)
        hits = np.zeros(n, dtype=np.float64)
        best = np.zeros(n, dtype=np.float64)
        for qt in toks[:6]:
            rows_hit: set[int] = set()
            for tok, s, _idx in process.extract(
                qt, self._char_toks, scorer=JaroWinkler.normalized_similarity,
                limit=10, score_cutoff=0.93,
            ):
                for row in self._char_tok_films[tok]:
                    rows_hit.add(row)
                    if s > best[row]:
                        best[row] = s
            for row in rows_hit:
                hits[row] += 1.0
        if len(toks) >= 2:
            vec = np.where(hits >= 2, hits / len(toks), 0.0)
        else:
            vec = np.where(best >= 0.94, best, 0.0)  # 1 token: só typo de nome
        return vec if vec.any() else None

    def search_by_person(self, query: str, n: int = 10, role: Optional[str] = None,
                         score_cutoff: float = 75.0) -> list[tuple[int, float]]:
        """Ator/diretor: casa o nome (fuzzy) e agrega os filmes da pessoa."""
        from rapidfuzz import fuzz, process

        if self._person_movies is None:
            self._build_person_maps()

        names = [name for _pid, name in self._people_names]
        matches = process.extract(
            query, names, scorer=fuzz.WRatio, limit=15, score_cutoff=score_cutoff
        )
        if not matches:
            return []

        # Pontua cada filme pela melhor correspondência de nome da pessoa.
        movie_score: dict[int, float] = {}
        for _name, score, idx in matches:
            pid = self._people_names[idx][0]
            for tmdb_id in self._person_movies.get(pid, []):
                if role is not None and not self._has_role(tmdb_id, pid, role):
                    continue
                s = score / 100.0
                if s > movie_score.get(tmdb_id, 0.0):
                    movie_score[tmdb_id] = s
        # Desempate por popularidade.
        ranked = sorted(
            movie_score.items(),
            key=lambda kv: (kv[1], self.catalog.get(kv[0], {}).get("popularity") or 0.0),
            reverse=True,
        )
        return [(tid, s) for tid, s in ranked[: n * 4]]

    def _has_role(self, tmdb_id: int, person_id: int, role: str) -> bool:
        return db.query_scalar(
            "SELECT 1 FROM movie_people WHERE tmdb_id=? AND person_id=? AND role=? LIMIT 1",
            (tmdb_id, person_id, role),
        ) is not None

    def _build_keyword_maps(self) -> None:
        keyword_movies: dict[int, list[int]] = {}
        for r in db.iter_query("SELECT keyword_id, tmdb_id FROM movie_keywords"):
            keyword_movies.setdefault(r["keyword_id"], []).append(r["tmdb_id"])
        self._keyword_movies = keyword_movies
        self._keyword_names = [
            (r["keyword_id"], r["name"]) for r in db.query("SELECT keyword_id, name FROM keywords")
        ]

    def search_by_keyword(self, query: str, n: int = 10, score_cutoff: float = 80.0
                         ) -> list[tuple[int, float]]:
        """Tema/keyword: casa a keyword (fuzzy) e agrega os filmes."""
        from rapidfuzz import fuzz, process

        if self._keyword_movies is None:
            self._build_keyword_maps()

        names = [name for _kid, name in self._keyword_names]
        matches = process.extract(
            query, names, scorer=fuzz.WRatio, limit=15, score_cutoff=score_cutoff
        )
        if not matches:
            return []

        movie_score: dict[int, float] = {}
        for _name, score, idx in matches:
            kid = self._keyword_names[idx][0]
            for tmdb_id in self._keyword_movies.get(kid, []):
                s = score / 100.0
                movie_score[tmdb_id] = movie_score.get(tmdb_id, 0.0) + s
        ranked = sorted(
            movie_score.items(),
            key=lambda kv: (kv[1], self.catalog.get(kv[0], {}).get("popularity") or 0.0),
            reverse=True,
        )
        return [(tid, s) for tid, s in ranked[: n * 4]]

    # --------------------------------------------------------------- explicação
    def _relevance(self, score: float, lo: float, hi: float) -> int:
        """Relevância apresentável (0–100), min-max dentro do conjunto retornado.
        É relativa à busca (não comparável entre buscas) — por isso a **posição**
        também é exibida."""
        if hi <= lo:
            return 100
        return int(round(100.0 * (score - lo) / (hi - lo)))

    def _confidence(self, tmdb_id: int, ctx: dict) -> int:
        """Confiança ABSOLUTA do match (0–100), independente do conjunto retornado.

        Diferente da `relevance` (min-max relativa à busca), aqui um 2º colocado
        fraco lê baixo de verdade. Para intenção de nome usa o score de nome (já
        ciente de stopwords); para descrição, mapeia o z-score do melhor sinal
        semântico (sinopse/tema) por uma logística — o z independe da escala de
        cosseno do modelo, então vale para MiniLM e E5 sem recalibrar."""
        import math

        conf = 0.0
        name_w = ctx.get("name_w", 0.0)
        name_scores = ctx.get("name_scores") or {}
        if name_w >= 0.3 and tmdb_id in name_scores:
            conf = max(conf, min(1.0, float(name_scores[tmdb_id])))

        comps = ctx.get("comps")
        row = self._row_index.get(int(tmdb_id)) if comps is not None else None
        if comps is not None and row is not None:
            z = max(float(comps["_z_synopsis"][row]), float(comps["_z_keyword"][row]))
            conf = max(conf, 1.0 / (1.0 + math.exp(-0.85 * (z - 1.4))))
        return int(round(100.0 * conf))

    def _signal_contributions(self, tmdb_id: int, ctx: dict) -> dict[str, float]:
        """Contribuição (já ponderada) de cada sinal para o score deste filme.

        Quando o ranking mistura nome + sinopse (auto/combinada), a sinopse entra
        normalizada a [0,1] (`syn_norm`) para ser comparável ao nome; aqui a
        contribuição total da sinopse (`syn_w * syn_norm`) é repartida entre os
        três sub-sinais pela proporção positiva dos seus z-scores — assim as
        contribuições somam ao score real e ainda mostram o detalhamento."""
        raw: dict[str, float] = {}
        comps = ctx.get("comps")
        syn_w = ctx.get("syn_w", 1.0)
        syn_norm = ctx.get("syn_norm")  # dict tid->[0,1] quando há blend com nome
        row = self._row_index.get(int(tmdb_id)) if comps is not None else None
        if comps is not None and row is not None:
            sub = {"lexical": float(comps["lexical"][row]),
                   "synopsis": float(comps["synopsis"][row]),
                   "keyword": float(comps["keyword"][row]),
                   "entity": float(comps["entity"][row]),
                   "plot": float(comps["plot"][row]),
                   "plot_lexical": float(comps["plot_lexical"][row]),
                   "plot_maxsim": float(comps["plot_maxsim"][row])}
            if syn_norm is not None:
                total = syn_w * float(syn_norm.get(tmdb_id, 0.0))
                pos = {k: max(0.0, v) for k, v in sub.items()}
                denom = sum(pos.values()) or 1.0
                for k, v in pos.items():
                    raw[k] = total * v / denom
            else:
                for k, v in sub.items():
                    raw[k] = syn_w * v
        name_w = ctx.get("name_w", 0.0)
        name_scores = ctx.get("name_scores") or {}
        if name_w > 0 and tmdb_id in name_scores:
            raw["name"] = name_w * float(name_scores[tmdb_id])
        return raw

    def _normalize_contributions(self, raw: dict[str, float]) -> list[dict[str, Any]]:
        """Frações (da parte positiva) que somam ~1, da maior para a menor — para
        o usuário ler 'tema 0.6 · sinopse 0.3 · nome 0.1'."""
        positive = {k: max(0.0, v) for k, v in raw.items()}
        total = sum(positive.values())
        items = [
            {"signal": k, "label": SIGNAL_LABELS.get(k, k),
             "share": round(positive[k] / total, 3) if total > 0 else 0.0}
            for k in raw
        ]
        items.sort(key=lambda d: d["share"], reverse=True)
        return items

    def _matched_keywords(self, tmdb_id: int, q_emb: Optional[np.ndarray],
                          topk: int = 4, min_sim: float = 0.30) -> list[str]:
        """Keywords temáticas do filme mais próximas da consulta (multilíngue):
        compara o embedding da consulta com o de cada keyword do filme. É assim
        que 'revivendo o mesmo dia' (PT) acende o chip 'time loop' (EN)."""
        if q_emb is None or self._kw_term_emb is None:
            return []
        names = catalog.get_movie_keywords(tmdb_id)
        pairs = [(nm, self._kw_term_row[nm.lower()]) for nm in names
                 if nm.lower() in self._kw_term_row]
        if not pairs:
            return []
        rows = np.array([r for _nm, r in pairs])
        sims = self._kw_term_emb[rows] @ q_emb
        order = np.argsort(sims)[::-1]
        return [pairs[int(i)][0] for i in order[:topk] if float(sims[i]) >= min_sim]

    def _matched_title_terms(self, query: str, title: Optional[str]) -> list[str]:
        """Tokens (>2 chars) presentes tanto na consulta quanto no título."""
        if not title:
            return []
        qset = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
        out: list[str] = []
        for w in re.findall(r"\w+", title.lower()):
            if len(w) > 2 and w in qset and w not in out:
                out.append(w)
        return out

    def _build_explanation(self, query: str, tmdb_id: int, score: float, ctx: dict,
                           position: int, pool_lo: float, pool_hi: float,
                           constraints: Optional[dict] = None) -> dict[str, Any]:
        """Explicação estruturada de por que o filme ficou nesta posição."""
        exp: dict[str, Any] = {
            "relevance": self._relevance(score, pool_lo, pool_hi),
            "confidence": self._confidence(tmdb_id, ctx),
            "position": position,
            "signals": self._normalize_contributions(self._signal_contributions(tmdb_id, ctx)),
            "matched_keywords": self._matched_keywords(tmdb_id, ctx.get("q_emb")),
            "matched_title_terms": self._matched_title_terms(
                query, self.catalog.get(tmdb_id, {}).get("title")),
        }
        cons = {k: v for k, v in (constraints or {}).items() if v}
        if cons:
            exp["constraints"] = cons
        return exp

    # ================================================================ dispatch
    def search(self, query: str, mode: str = "auto", n: int = 10,
               filters: Optional[dict] = None, role: Optional[str] = None,
               explain: bool = True) -> list[dict[str, Any]]:
        """Busca unificada.

        `mode`: 'name' | 'synopsis' | 'person' | 'keyword' | 'auto'.
        Em 'auto', funde nome (curto) + sinopse (se houver índice).
        `filters`: {year, year_min, year_max, genre, language}.
        `role`: no modo 'person', restringe a 'actor' ou 'director'.
        `explain`: anexa um objeto `explanation` por resultado.
        """
        query = (query or "").strip()
        if not query:
            return []

        ctx: dict[str, Any] = {}
        with _metrics.stage_timer("retrieval"):
            if mode == "name":
                scored = self.search_by_name(query, n)
                ctx = {"name_scores": dict(scored), "name_w": 1.0, "syn_w": 0.0,
                       "q_emb": self._encode(query) if self._embeddings is not None else None}
            elif mode == "synopsis":
                scored, ctx = self._synopsis_ranked(query, n, blend_name=False)
            elif mode == "person":
                scored = self.search_by_person(query, n, role=role)
            elif mode == "keyword":
                scored = self.search_by_keyword(query, n)
                ctx = {"q_emb": self._encode(query) if self._embeddings is not None else None}
            elif mode == "auto":
                if self.has_synopsis_index:
                    scored, ctx = self._synopsis_ranked(query, n, blend_name=True)
                else:
                    scored = self.search_by_name(query, n)
                    ctx = {"name_scores": dict(scored), "name_w": 1.0, "syn_w": 0.0}
            else:
                raise ValueError(f"modo desconhecido: {mode!r}")

        # Re-rank por sinopse só faz sentido p/ descrição; numa busca de nome
        # (título quase-exato) reordenar pela sinopse atrapalha.
        if mode in ("synopsis", "auto") and self.has_synopsis_index and ctx.get("intent") != "name":
            with _metrics.stage_timer("rerank"):
                scored = self._maybe_rerank(query, scored, ctx)

        # Relevância: quando reordenado, escala só pela cabeça reordenada (mesma
        # escala blended); senão, por todo o conjunto.
        rel_scored = scored[:RERANK_POOL] if ctx.get("reranked") else scored
        pool = [s for _tid, s in rel_scored]
        pool_lo, pool_hi = (min(pool), max(pool)) if pool else (0.0, 1.0)

        out: list[dict[str, Any]] = []
        for tmdb_id, score in scored:
            if not self._passes_filters(tmdb_id, filters):
                continue
            item = self._format(tmdb_id, score)
            if explain:
                item["explanation"] = self._build_explanation(
                    query, tmdb_id, score, ctx, len(out) + 1, pool_lo, pool_hi)
            out.append(item)
            if len(out) >= n:
                break
        return out

    def _adaptive_name_weight(self, query: str) -> float:
        """Peso-base do nome conforme o tamanho da consulta: curta parece título
        (nome pesa mais); longa é descrição (a sinopse domina)."""
        n_words = len(query.split())
        return 0.6 if n_words <= 3 else 0.3 if n_words <= 5 else 0.05

    def _best_title_match(self, query: str, titles: list[str]) -> float:
        """Maior similaridade (0–1) da consulta a algum título — para classificar
        INTENÇÃO. Usa `fuzz.ratio` (string inteira), não `WRatio`: o ratio penaliza
        diferença de tamanho, então uma descrição longa NÃO casa com um título
        curto (evita falso 'nome'), mas um título digitado (mesmo com typo) casa."""
        from rapidfuzz import fuzz, process

        if not titles:
            return 0.0
        m = process.extractOne(query, titles, scorer=fuzz.ratio, processor=_name_key)
        return (m[1] / 100.0) if m else 0.0

    def _intent_weight(self, best_title: float, base: float) -> tuple[float, str]:
        """Classifica a INTENÇÃO da consulta pela força do melhor match de título
        e devolve (peso_do_nome, intent). Título (quase) exato ⇒ a consulta É um
        nome → nome domina; senão mantém a base descritiva. Mais confiável que uma
        SLM aqui, porque usa o próprio catálogo (a SLM não conhece os títulos)."""
        if best_title >= 0.92:
            return 0.9, "name"
        if best_title >= 0.85:
            return max(base, 0.6), "name"
        return base, "description"

    def _synopsis_ranked(self, query: str, n: int, blend_name: bool
                         ) -> tuple[list[tuple[int, float]], dict]:
        """Ranqueia por sinopse; em 'auto' (blend_name) funde também o nome.
        Devolve (scored, ctx) — ctx carrega os componentes p/ a explicação."""
        # Embedding do lado-consulta usa a query LIMPA (mesmo texto do BM25 dentro
        # de _synopsis_components); o nome usa a query original (casa títulos).
        cq = clean_descriptive_query(query)
        q_emb = self._encode(cq) if self._embeddings is not None else None
        comps = self._synopsis_components(cq, q_emb=q_emb)
        fused = comps["lexical"] + comps["synopsis"] + comps["keyword"] + comps["entity"] + comps["plot"] + comps["plot_lexical"] + comps["plot_maxsim"] + comps["prior"]
        order = np.argsort(fused)[::-1]

        if not blend_name:
            top = order[: n * 4]
            scored = [(int(self._movie_ids[i]), float(fused[i])) for i in top]
            ctx = {"comps": comps, "q_emb": q_emb, "name_scores": {},
                   "name_w": 0.0, "syn_w": 1.0}
            return scored, ctx

        # auto: o sinal de sinopse (z-score, ilimitado) precisa virar [0,1] para
        # ser comparável ao nome (rapidfuzz [0,1]) antes da soma ponderada. O peso
        # do nome é ditado pela INTENÇÃO (título quase-exato ⇒ nome domina). O
        # fallback TMDB entra aqui: um título em inglês casa pouco com os títulos PT
        # locais, então a confiança do TMDB é quem eleva a intenção para "nome".
        name_scores, tmdb_conf = self._name_scores(query, n)
        best_title = max(
            self._best_title_match(
                query, [self.catalog.get(t, {}).get("title") or "" for t in name_scores]),
            tmdb_conf)
        name_w, intent = self._intent_weight(best_title, self._adaptive_name_weight(query))
        syn_w = 1.0 - name_w
        row = self._row_index
        # Candidatos: top do fundido ∪ top de CADA sinal (sinopse/keyword) ∪ nome.
        # O pool por sinal resgata um match forte num único sinal (ex.: Frozen no
        # sinal temático) que a fusão sozinha deixaria fora da janela de re-rank.
        per_signal_k = max(n * 4, 80)
        cand_ids = {int(self._movie_ids[i]) for i in order[: n * 4]}
        for sig in ("synopsis", "keyword", "entity", "plot", "plot_lexical", "plot_maxsim"):
            sig_order = np.argsort(comps[sig])[::-1][:per_signal_k]
            cand_ids |= {int(self._movie_ids[i]) for i in sig_order}
        cand_ids |= set(name_scores)
        syn_raw = {tid: float(fused[row[tid]]) if tid in row else float(fused.min())
                   for tid in cand_ids}
        lo, hi = min(syn_raw.values()), max(syn_raw.values())
        syn_norm = {tid: ((v - lo) / (hi - lo) if hi > lo else 0.0)
                    for tid, v in syn_raw.items()}
        combined = {tid: syn_w * syn_norm[tid] + name_w * name_scores.get(tid, 0.0)
                    for tid in cand_ids}
        scored = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
        ctx = {"comps": comps, "q_emb": q_emb, "name_scores": name_scores,
               "name_w": name_w, "syn_w": syn_w, "syn_norm": syn_norm, "intent": intent}
        return scored, ctx

    # ============================================================== combinada
    @property
    def _row_index(self) -> dict[int, int]:
        if self._row_index_cache is None:
            self._row_index_cache = (
                {int(t): i for i, t in enumerate(self._movie_ids)}
                if self._movie_ids is not None else {}
            )
        return self._row_index_cache

    @property
    def _pop_prior_vec(self) -> np.ndarray:
        """z-score de log(1+vote_count), alinhado a self._movie_ids (cacheado)."""
        if self._pop_prior_cache is None:
            if self._movie_ids is None:
                return np.zeros(0)
            votec = np.array(
                [float(self.catalog.get(int(t), {}).get("vote_count") or 0.0)
                 for t in self._movie_ids], dtype=np.float64)
            self._pop_prior_cache = _zscore(np.log1p(votec))
        return self._pop_prior_cache

    def _movies_by_person_name(self, name: str, role: Optional[str]) -> set[int]:
        """tmdb_ids dos filmes de uma pessoa (nome exato), opcionalmente por papel."""
        sql = ("SELECT mp.tmdb_id AS tmdb_id FROM people p "
               "JOIN movie_people mp ON mp.person_id = p.person_id WHERE p.name = ?")
        params: list[Any] = [name]
        if role in ("actor", "director"):
            sql += " AND mp.role = ?"
            params.append(role)
        return {r["tmdb_id"] for r in db.query(sql, params)}

    def _rank_candidates(self, query: str, cand_ids: list[int]) -> list[tuple[int, float]]:
        return self._rank_candidates_ctx(query, cand_ids)[0]

    def _rank_candidates_ctx(self, query: str, cand_ids: list[int]
                             ) -> tuple[list[tuple[int, float]], dict]:
        """Ranqueia um conjunto restrito (filmes de um diretor/ator) por consulta
        livre, reaproveitando os mesmos sinais z-score da busca por sinopse + o
        nome. Como a pessoa já restringiu o conjunto, o texto quase sempre é uma
        descrição de enredo — então a sinopse pesa mais que na busca global.
        Devolve (scored, ctx) para a explicação."""

        cq = clean_descriptive_query(query)  # mesma query limpa p/ embedding e BM25
        q_emb = self._encode(cq) if self._embeddings is not None else None
        comps = self._synopsis_components(cq, q_emb=q_emb) if self.has_synopsis_index else None
        row = self._row_index

        qk = _name_key(query)
        name_scores = {tid: _name_score(qk, self.catalog.get(tid, {}).get("title") or "")
                       for tid in cand_ids}
        # Intenção: num conjunto já restrito o texto costuma DESCREVER o enredo
        # (base 0.2, nome só desempata); mas se a consulta casa (quase) exato com
        # o título de um candidato, ela É um nome → o nome domina.
        best_title = max(name_scores.values(), default=0.0)
        name_w, intent = self._intent_weight(best_title, 0.2)
        syn_w = 1.0 - name_w
        # Sinopse z-score -> [0,1] dentro do conjunto restrito (comparável ao nome).
        syn_norm: dict[int, float] = {}
        if comps is not None:
            syn_raw = {tid: float(comps["lexical"][row[tid]] + comps["synopsis"][row[tid]]
                                  + comps["keyword"][row[tid]] + comps["entity"][row[tid]]
                                  + comps["plot"][row[tid]] + comps["plot_lexical"][row[tid]]
                                  + comps["plot_maxsim"][row[tid]]) if tid in row else 0.0
                       for tid in cand_ids}
            lo, hi = min(syn_raw.values()), max(syn_raw.values())
            syn_norm = {tid: ((v - lo) / (hi - lo) if hi > lo else 0.0)
                        for tid, v in syn_raw.items()}
        combined = {tid: name_w * name_scores[tid] + syn_w * syn_norm.get(tid, 0.0)
                    for tid in cand_ids}
        scored = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
        ctx = {"comps": comps, "q_emb": q_emb, "name_scores": name_scores,
               "name_w": name_w, "syn_w": syn_w, "syn_norm": syn_norm, "intent": intent}
        return scored, ctx

    def search_combined(self, query: Optional[str] = None, director: Optional[str] = None,
                        actor: Optional[str] = None, n: int = 10,
                        filters: Optional[dict] = None) -> list[dict[str, Any]]:
        """Busca facetada: diretor/ator **restringem** (o filme precisa tê-los) e
        a consulta livre (sinopse/nome) **ranqueia** dentro do conjunto. Sem
        consulta, ordena por popularidade. Sem diretor/ator, cai na busca normal.
        """
        query = (query or "").strip()
        director = (director or "").strip()
        actor = (actor or "").strip()

        # Sem restrição de pessoa: busca de texto normal (auto).
        if not director and not actor:
            return self.search(query, mode="auto", n=n, filters=filters) if query else []

        # Interseção das restrições de pessoa.
        constraint: Optional[set[int]] = None
        for name, role in ((director, "director"), (actor, "actor")):
            if name:
                ids = self._movies_by_person_name(name, role)
                constraint = ids if constraint is None else (constraint & ids)

        cands = [tid for tid in (constraint or set()) if self._passes_filters(tid, filters)]
        if not cands:
            return []

        constraints = {"director": director or None, "actor": actor or None}
        if query:
            scored, ctx = self._rank_candidates_ctx(query, cands)
            # Re-rank por sinopse só para descrição; se o texto é um título exato
            # (intent=name), o nome já manda e reordenar pela sinopse atrapalha.
            if ctx.get("intent") != "name":
                scored = self._maybe_rerank(query, scored, ctx, min_words=1)
        else:
            # Sem texto: ordena por popularidade (a pessoa é a única restrição).
            scored = sorted(
                ((tid, float(self.catalog.get(tid, {}).get("popularity") or 0.0)) for tid in cands),
                key=lambda kv: kv[1], reverse=True,
            )
            ctx = {"q_emb": None}

        rel_scored = scored[:RERANK_POOL] if ctx.get("reranked") else scored
        pool = [s for _tid, s in rel_scored]
        pool_lo, pool_hi = (min(pool), max(pool)) if pool else (0.0, 1.0)
        out: list[dict[str, Any]] = []
        for tid, s in scored[:n]:
            item = self._format(tid, s)
            item["explanation"] = self._build_explanation(
                query, tid, s, ctx, len(out) + 1, pool_lo, pool_hi, constraints=constraints)
            out.append(item)
        return out


def suggest_people(prefix: str, role: Optional[str] = None, limit: int = 10
                   ) -> list[dict[str, Any]]:
    """Autocomplete de pessoas: nomes que contêm `prefix`, ordenados por nº de
    créditos (mais prolíficos primeiro). `role` opcional ('actor'|'director').
    Devolve [{name, credits, roles}]."""
    prefix = (prefix or "").strip()
    if len(prefix) < 2:
        return []
    sql = """
        SELECT p.name AS name, COUNT(*) AS credits,
               GROUP_CONCAT(DISTINCT mp.role) AS roles
        FROM people p JOIN movie_people mp ON mp.person_id = p.person_id
        WHERE p.name LIKE ?
    """
    params: list[Any] = [f"%{prefix}%"]
    if role in ("actor", "director"):
        sql += " AND mp.role = ?"
        params.append(role)
    # Quem começa com o que foi digitado vem primeiro (depois os mais prolíficos),
    # para o nome certo surgir mesmo com poucas letras.
    sql += " GROUP BY p.person_id ORDER BY (p.name LIKE ?) DESC, credits DESC LIMIT ?"
    params.append(f"{prefix}%")
    params.append(limit)
    return db.query(sql, params)


# Singleton preguiçoso para reuso entre requisições.
_engine: Optional[SearchEngine] = None


def get_engine(warmup: bool = True) -> SearchEngine:
    """Singleton do motor de busca. `warmup=True` (padrão) carrega os modelos
    pesados agora — chame no boot do app, não deixe cair na 1ª requisição.
    `RECOMENDAI_NO_WARMUP=1` força o carregamento preguiçoso (útil em testes)."""
    global _engine
    if _engine is None:
        _engine = SearchEngine()
        if warmup and os.environ.get("RECOMENDAI_NO_WARMUP", "0").lower() not in ("1", "true", "yes"):
            _engine.warmup()
    return _engine
