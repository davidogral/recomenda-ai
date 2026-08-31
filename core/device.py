"""SeleÃ§Ã£o automÃ¡tica de device para os modelos (embeddings, re-ranker, traduÃ§Ã£o).

Ordem: `cuda` (NVIDIA) â†’ `mps` (GPU da Apple via Metal/PyTorch) â†’ `cpu`.
Override por ambiente: `RECOMENDAI_DEVICE=cuda|mps|cpu`.

Nota: para modelos HuggingFace/sentence-transformers, o caminho de GPU no Mac Ã©
**MPS** (Metal), nÃ£o MLX â€” MLX Ã© um framework separado que exigiria reimplementar
os modelos. MPS dÃ¡ a aceleraÃ§Ã£o de GPU da Apple aqui.
"""

from __future__ import annotations

import os
from typing import Optional

_cached: Optional[str] = None


def get_device() -> str:
    """Retorna o melhor device disponÃ­vel (cacheado)."""
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
