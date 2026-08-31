"""Re-ranker cross-encoder — 2º estágio da recuperação.

A 1ª etapa (BM25 + embeddings, fusão z-score) é um *bi-encoder*: rápida, varre os
22k, mas compara query e documento separadamente. O cross-encoder lê **query e
sinopse juntas** e dá um score de relevância muito mais fino — caro demais para os
22k, mas barato sobre o top-K candidato. Padrão clássico *retrieve-and-rerank*.

Modelo default: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (MS MARCO multilíngue,
inclui PT; ~120 MB; roda bem em CPU/MPS/CUDA).
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from core.device import get_device

DEFAULT_RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


class CrossEncoderReranker:
    """Re-ranqueia (query, texto) com um cross-encoder. Carrega sob demanda."""

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL,
                 device: Optional[str] = None, max_length: int = 320):
        self.model_name = model_name
        self.device = device or get_device()
        self.max_length = max_length
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name, device=self.device, max_length=self.max_length
            )
        return self._model

    def rerank(self, query: str, cand_ids: list[int], text_of: Callable[[int], str],
               retr_scores: Optional[dict[int, float]] = None,
               blend: float = 0.0) -> list[tuple[int, float]]:
        """Reordena `cand_ids` pela relevância cross-encoder de (query, texto).

        `text_of(tmdb_id)` -> passagem (sinopse). `blend` (0..1) mistura o score
        do cross-encoder (normalizado) com o score da 1ª etapa (`retr_scores`);
        0 = só cross-encoder. Devolve [(tmdb_id, score)] já ordenado.
        """
        if not cand_ids:
            return []
        pairs = [(query, text_of(tid) or "") for tid in cand_ids]
        scores = np.asarray(self._get_model().predict(
            pairs, batch_size=32, show_progress_bar=False), dtype=np.float64)

        if blend > 0 and retr_scores:
            ce = _minmax(scores)
            rt = _minmax(np.array([retr_scores.get(t, 0.0) for t in cand_ids]))
            scores = (1.0 - blend) * ce + blend * rt

        order = np.argsort(scores)[::-1]
        return [(int(cand_ids[i]), float(scores[i])) for i in order]


def _minmax(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    return (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)
