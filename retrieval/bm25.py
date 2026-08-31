"""Índice BM25 (Okapi) sobre as sinopses — o sinal **lexical** da busca.

BM25 supera o TF-IDF cosine como sinal lexical (medido no harness: hits@10
9/12 → 12/12). Dois motivos: satura a frequência de termo (parâmetro `k1`, então
repetir uma palavra não domina) e normaliza pelo tamanho do documento (`b`, então
uma sinopse curta com o termo exato não é penalizada frente a uma longa). Isso
ajuda justamente os enredos descritos por termos próprios — "revivendo o mesmo
dia", "sete pecados capitais".

Persistência: o `CountVectorizer` ajustado + a matriz de contagens esparsa. As
estatísticas derivadas (idf, tamanho dos docs, avgdl) são recomputadas no load
(custa poucos ms), então não precisam ir para disco.
"""

from __future__ import annotations

import pickle
from typing import Optional

import numpy as np
from scipy import sparse

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


class BM25Index:
    """BM25 Okapi sobre uma matriz de contagens (n_docs × vocabulário)."""

    def __init__(self, vectorizer, counts: sparse.spmatrix,
                 k1: float = DEFAULT_K1, b: float = DEFAULT_B):
        self.vectorizer = vectorizer
        self.counts = counts.tocsc()  # acesso por coluna (termo) no scoring
        self.k1 = k1
        self.b = b

        n_docs = self.counts.shape[0]
        self.doc_len = np.asarray(self.counts.sum(axis=1)).ravel().astype(np.float64)
        nz = self.doc_len[self.doc_len > 0]
        self.avgdl = float(nz.mean()) if nz.size else 1.0
        dfreq = np.asarray((self.counts > 0).sum(axis=0)).ravel()
        self.idf = np.log(1.0 + (n_docs - dfreq + 0.5) / (dfreq + 0.5))
        # Fator do denominador que só depende do documento (não do termo).
        self._K = k1 * (1.0 - b + b * self.doc_len / self.avgdl)

    @property
    def n_docs(self) -> int:
        return self.counts.shape[0]

    @classmethod
    def build(cls, docs: list[str], stop_words: Optional[list[str]] = None,
              k1: float = DEFAULT_K1, b: float = DEFAULT_B,
              strip_accents: Optional[str] = "unicode") -> "BM25Index":
        from sklearn.feature_extraction.text import CountVectorizer

        # strip_accents='unicode': "gângster" (consulta PT) casa "gangster"
        # (keyword/overview em inglês). O mesmo analisador é aplicado à consulta
        # no .transform, então os dois lados ficam sem acento automaticamente.
        vectorizer = CountVectorizer(stop_words=stop_words, strip_accents=strip_accents)
        counts = vectorizer.fit_transform(docs)
        return cls(vectorizer, counts, k1=k1, b=b)

    def scores(self, query: str) -> np.ndarray:
        """Vetor de scores BM25 do query contra todos os documentos (linha=doc)."""
        out = np.zeros(self.n_docs, dtype=np.float64)
        term_ids = np.unique(self.vectorizer.transform([query]).indices)
        for t in term_ids:
            col = self.counts.getcol(int(t))
            idx = col.indices
            f = col.data.astype(np.float64)
            out[idx] += self.idf[t] * (f * (self.k1 + 1.0)) / (f + self._K[idx])
        return out

    @property
    def vocab_size(self) -> int:
        return len(self.vectorizer.vocabulary_)

    # ----------------------------------------------------------- persistência
    def save(self, vectorizer_path: str, counts_path: str) -> None:
        with open(vectorizer_path, "wb") as fh:
            pickle.dump(self.vectorizer, fh)
        sparse.save_npz(counts_path, self.counts.tocsr())

    @classmethod
    def load(cls, vectorizer_path: str, counts_path: str,
             k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> "BM25Index":
        with open(vectorizer_path, "rb") as fh:
            vectorizer = pickle.load(fh)
        counts = sparse.load_npz(counts_path)
        return cls(vectorizer, counts, k1=k1, b=b)
