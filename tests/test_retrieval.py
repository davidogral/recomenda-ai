# -*- coding: utf-8 -*-
"""Funções puras do índice/busca que rodam sem modelo nem índice serializado.
O canal de entidade ponta-a-ponta é medido em `eval/` (split `entity`)."""

from retrieval.index_builder import _clean_character


def test_clean_character_keeps_names_drops_junk():
    assert _clean_character("Dominic Toretto") == "Dominic Toretto"
    assert _clean_character("Roman Pearce") == "Roman Pearce"
    assert _clean_character("Bruce Wayne / Batman") == "Bruce Wayne / Batman"
    assert _clean_character("Dominic Toretto (voice)") == "Dominic Toretto"  # tira o parêntese
    assert _clean_character("Party Guest uncredited") is None
    assert _clean_character("Guard #2") is None
    assert _clean_character("Soldier # 14") is None
    assert _clean_character("Himself") is None
    assert _clean_character("") is None
    assert _clean_character(None) is None
    assert _clean_character("x" * 80) is None  # descrição, não nome
    assert _clean_character("Q") == "Q"


def test_entity_split_present_in_eval_set():
    from eval.dataset import load_queries

    qs = load_queries("entity")
    assert len(qs) >= 15
    joined = " ".join(q.query.lower() for q in qs)
    assert "toretto" in joined or "toreto" in joined  # o caso âncora
