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


def test_object_split_present_in_eval_set():
    from eval.dataset import load_queries

    qs = load_queries("object")
    assert len(qs) >= 40  # v3 ampliado: 15 -> 42 consultas de objeto/cena icônica
    joined = " ".join(q.query.lower() for q in qs)
    assert "skyline" in joined  # alvo dos canais de enredo (embedding + lexical)


def test_plot_channels_default():
    """Três formas de usar o enredo da Wikipédia. Embedding do plot INTEIRO segue
    desligado (ablação 2026-09-04: negativo, e5 trunca em 512 tokens). BM25 sobre o
    texto (`plot_lexical`) e MaxSim sobre trechos (`plot_maxsim`) vão LIGADOS —
    ambos medidos subindo object/entity/test juntos, sem regressão; inertes até o
    índice ter os arquivos correspondentes, então seguros mesmo antes de um build
    completo. `plot_lexical` nasceu OFF (levemente negativo em fusão pura) e só
    virou positivo depois do Z_SCORE_CLIP (2026-09-08) — sem teto, o outlier de
    termo raro que motivou o clip também enterrava esse canal."""
    from retrieval import search_engine as se

    assert se.DEFAULT_PLOT_WEIGHT == 0.0
    assert se.DEFAULT_PLOT_BM25_WEIGHT == 0.1
    assert se.DEFAULT_PLOT_CHUNK_WEIGHT == 0.5
    assert {"plot", "plot_lexical", "plot_maxsim"} <= set(se.SIGNAL_LABELS)


def test_person_match_channel_off_by_default():
    """Canal de trivia de pessoa (bio de elenco/diretor, ver core.query_llm
    tipo="pessoa") ainda não foi medido — índice novo, só ~500 pessoas. Fica
    OFF até uma ablação real, mesmo padrão dos outros canais de enredo."""
    from retrieval import search_engine as se

    assert se.DEFAULT_PERSON_MATCH_WEIGHT == 0.0
    assert "person_match" in se.SIGNAL_LABELS


def test_chunk_words_windows_long_text():
    from retrieval.index_builder import _chunk_words

    assert _chunk_words("") == []
    assert _chunk_words("um dois tres") == ["um dois tres"]  # curto: 1 janela
    chunks = _chunk_words(" ".join(str(i) for i in range(1000)), size=380, overlap=50)
    assert len(chunks) >= 3  # 1000 palavras -> várias janelas
    assert all(len(c.split()) <= 380 for c in chunks)
    assert chunks[0].split()[-50:] == chunks[1].split()[:50]  # janelas se sobrepõem em 50


def test_clean_wikitext_strips_markup_keeps_prose():
    from core.enrich import _clean_wikitext

    raw = (
        "'''Dom Cobb''' is an [[extractor]] using {{lang|la|foo}} dream tech."
        "<ref name=x>cite web</ref> He drives the [[Nissan Skyline GT-R|Skyline]]."
        "<!-- editor note --> [[File:poster.jpg|thumb|A poster]] The end."
    )
    out = _clean_wikitext(raw)
    assert "{{" not in out and "[[" not in out and "<ref" not in out and "File:" not in out
    assert "Dom Cobb is an extractor" in out
    assert "Skyline" in out  # o nome do carro sobrevive — é o ponto do canal
    assert "The end." in out
