# -*- coding: utf-8 -*-
"""Encoder de embeddings via **ONNX Runtime**, com quantização dinâmica **INT8**.

Motivação: o forward do `intfloat/multilingual-e5-large` (fp32, ~560 M params) é a
etapa mais cara da busca por sinopse quando a consulta não está no cache (medido:
p50 ~43 ms / p99 ~167 ms em MPS; muito pior em CPU). Exportar para ONNX e
quantizar os pesos para INT8 corta RAM (~2,2 GB → ~0,6 GB) e acelera o encode em
CPU, ao custo de um ruído de quantização no vetor da consulta.

Uso:
    # exporta e quantiza (uma vez)
    python -m retrieval.onnx_embed export intfloat/multilingual-e5-large
    # confere fidelidade vs sentence-transformers
    python -m retrieval.onnx_embed check intfloat/multilingual-e5-large

Em produção, `SearchEngine` usa este encoder quando
`RECOMENDAI_EMBED_BACKEND=onnx-int8` (ou `onnx-fp32`).

`OnnxEncoder.encode(...)` replica o contrato do `SentenceTransformer.encode`
usado no projeto: **mean pooling** mascarado + **L2-norm** (o e5 usa average
pooling). Os prefixos `query:` / `passage:` continuam sendo responsabilidade de
quem chama (search_engine / index_builder), igual ao caminho sentence-transformers.
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
ONNX_DIR = os.path.join(_HERE, "onnx")


def _slug(model_name: str) -> str:
    return model_name.replace("/", "__")


def _cleanup_cwd_temp() -> None:
    """torch.onnx.export / quant_pre_process largam arquivos temporários no CWD."""
    import glob
    for f in glob.glob("*.data") + ["sym_shape_infer_temp.onnx"]:
        try:
            os.remove(f)
        except OSError:
            pass


def paths_for(model_name: str) -> dict[str, str]:
    base = os.path.join(ONNX_DIR, _slug(model_name))
    return {"dir": base,
            "fp32": os.path.join(base, "model.onnx"),
            "int8": os.path.join(base, "model.int8.onnx")}


# --------------------------------------------------------------------- export
def export(model_name: str, opset: int = 17, overwrite: bool = False) -> dict:
    """Exporta o modelo HF para ONNX (fp32) e gera a versão INT8 dinâmica."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    p = paths_for(model_name)
    os.makedirs(p["dir"], exist_ok=True)
    tok = AutoTokenizer.from_pretrained(model_name)
    tok.save_pretrained(p["dir"])

    if overwrite or not os.path.exists(p["fp32"]):
        model = AutoModel.from_pretrained(model_name).eval()

        class _Wrap(torch.nn.Module):
            def __init__(self, m):
                super().__init__()
                self.m = m

            def forward(self, input_ids, attention_mask):  # só o last_hidden_state
                return self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

        dummy = tok(["passage: exemplo de sinopse", "query: um filme sobre"],
                    return_tensors="pt", padding=True, truncation=True, max_length=32)
        torch.onnx.export(
            _Wrap(model), (dummy["input_ids"], dummy["attention_mask"]), p["fp32"],
            input_names=["input_ids", "attention_mask"], output_names=["last_hidden_state"],
            dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"},
                          "last_hidden_state": {0: "b", 1: "s"}},
            opset_version=opset, do_constant_folding=True,
        )

    if overwrite or not os.path.exists(p["int8"]):
        from onnxruntime.quantization import QuantType, quantize_dynamic
        from onnxruntime.quantization.shape_inference import quant_pre_process

        pre = p["fp32"].replace(".onnx", ".pre.onnx")
        try:
            quant_pre_process(p["fp32"], pre)
            src = pre
        except Exception:
            src = p["fp32"]
        quantize_dynamic(src, p["int8"], weight_type=QuantType.QInt8)
        if os.path.exists(pre):
            os.remove(pre)

    _cleanup_cwd_temp()
    return {"model": model_name,
            "fp32_mb": round(os.path.getsize(p["fp32"]) / 1e6, 1),
            "int8_mb": round(os.path.getsize(p["int8"]) / 1e6, 1),
            **p}


# -------------------------------------------------------------------- encoder
class OnnxEncoder:
    """Contrato mínimo de `SentenceTransformer.encode` sobre uma sessão ORT."""

    def __init__(self, onnx_path: str, tokenizer_dir: str, max_length: int = 512,
                 intra_op_threads: int | None = None):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        so = ort.SessionOptions()
        if intra_op_threads:
            so.intra_op_num_threads = intra_op_threads
        self.session = ort.InferenceSession(onnx_path, sess_options=so,
                                            providers=["CPUExecutionProvider"])
        self.tok = AutoTokenizer.from_pretrained(tokenizer_dir)
        self.max_length = max_length
        self.onnx_path = onnx_path

    @classmethod
    def from_env(cls, model_name: str) -> "OnnxEncoder":
        """Escolhe fp32/int8 por `RECOMENDAI_EMBED_BACKEND` (onnx-int8 = padrão
        quando o backend começa com 'onnx'). `RECOMENDAI_EMBED_ONNX` sobrepõe o
        caminho do arquivo."""
        p = paths_for(model_name)
        override = os.environ.get("RECOMENDAI_EMBED_ONNX")
        if override:
            onnx_path = override
        else:
            backend = os.environ.get("RECOMENDAI_EMBED_BACKEND", "onnx-int8").lower()
            onnx_path = p["fp32"] if backend.endswith("fp32") else p["int8"]
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(
                f"{onnx_path} não existe. Rode: python -m retrieval.onnx_embed export {model_name}")
        return cls(onnx_path, p["dir"])

    def encode(self, texts, batch_size: int = 32, normalize_embeddings: bool = True,
               convert_to_numpy: bool = True, show_progress_bar: bool = False,
               **_) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        chunks = []
        rng = range(0, len(texts), batch_size)
        if show_progress_bar:
            try:
                from tqdm import tqdm
                rng = tqdm(rng, desc="onnx encode")
            except Exception:
                pass
        for i in rng:
            batch = texts[i:i + batch_size]
            enc = self.tok(batch, padding=True, truncation=True,
                           max_length=self.max_length, return_tensors="np")
            feeds = {"input_ids": enc["input_ids"].astype(np.int64),
                     "attention_mask": enc["attention_mask"].astype(np.int64)}
            (lhs,) = self.session.run(None, feeds)                 # (b, s, d)
            mask = enc["attention_mask"].astype(np.float32)[..., None]
            emb = (lhs * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
            if normalize_embeddings:
                emb = emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
            chunks.append(emb.astype(np.float32))
        return np.vstack(chunks)


# ---------------------------------------------------------------------- check
def check(model_name: str, samples: Iterable[str] | None = None) -> dict:
    """Compara ONNX fp32 e INT8 contra o sentence-transformers (cosseno médio)."""
    from sentence_transformers import SentenceTransformer

    samples = list(samples or [
        "query: um homem que perde a memória e investiga a morte da esposa",
        "passage: dois detetives caçam um assassino que mata pelos sete pecados",
        "query: filme de ficção científica sobre uma simulação da realidade",
        "passage: uma família pobre se infiltra na casa de uma família rica",
        "query: comédia romântica em paris nos anos 1920",
    ])
    st = SentenceTransformer(model_name)
    ref = st.encode(samples, normalize_embeddings=True, convert_to_numpy=True)

    p = paths_for(model_name)
    out = {"model": model_name, "n_samples": len(samples)}
    for tag, path in (("fp32", p["fp32"]), ("int8", p["int8"])):
        if not os.path.exists(path):
            continue
        enc = OnnxEncoder(path, p["dir"])
        got = enc.encode(samples, normalize_embeddings=True)
        cos = float(np.mean(np.sum(ref * got, axis=1)))
        out[f"cos_vs_st_{tag}"] = round(cos, 5)
        out[f"size_mb_{tag}"] = round(os.path.getsize(path) / 1e6, 1)
    return out


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Exporta/quantiza/valida encoder ONNX.")
    ap.add_argument("cmd", choices=["export", "check"])
    ap.add_argument("model", nargs="?", default="intfloat/multilingual-e5-large")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    fn = export(a.model, overwrite=a.overwrite) if a.cmd == "export" else check(a.model)
    print(json.dumps(fn, ensure_ascii=False, indent=2))
