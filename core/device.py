"""Seleção automática de device para os modelos (embeddings, re-ranker, tradução).

Ordem: `cuda` (NVIDIA) → `mps` (GPU da Apple via Metal/PyTorch) → `cpu`.
Override por ambiente: `RECOMENDAI_DEVICE=cuda|mps|cpu`.

Nota: para modelos HuggingFace/sentence-transformers, o caminho de GPU no Mac é
**MPS** (Metal), não MLX — MLX é um framework separado que exigiria reimplementar
os modelos. MPS dá a aceleração de GPU da Apple aqui.
"""

from __future__ import annotations

import os
from typing import Optional

_cached: Optional[str] = None


def get_device() -> str:
    """Retorna o melhor device disponível (cacheado)."""
    global _cached
    if _cached is not None:
        return _cached

    forced = os.environ.get("RECOMENDAI_DEVICE")
    if forced:
        _cached = forced.strip().lower()
        return _cached

    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
        else:
            mps = getattr(torch.backends, "mps", None)
            if mps is not None and mps.is_available():
                device = "mps"
    except Exception:
        device = "cpu"

    _cached = device
    return device
