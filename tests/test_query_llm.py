# -*- coding: utf-8 -*-
"""core.query_llm: nunca deve quebrar/bloquear a busca, mesmo sem chave, com
rede fora, timeout ou resposta malformada do Groq."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Cache em disco isolado por teste — não toca data/tmdb_cache/ real nem
    vaza estado entre testes (o cache é um global do módulo)."""
    from core import query_llm

    monkeypatch.setattr(query_llm, "_cache", None)
    monkeypatch.setattr("core.db._PROJECT_ROOT", str(tmp_path))
    yield
    monkeypatch.setattr(query_llm, "_cache", None)


def test_understand_default_when_not_configured(monkeypatch):
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "")
    plan = query_llm.understand("qualquer consulta")
    assert plan == query_llm.QueryPlan()
    assert plan.ok is False
    assert plan.tipo == "generico"


def test_understand_empty_query_short_circuits(monkeypatch):
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    assert query_llm.understand("   ").ok is False


def _fake_post(status_code=200, content=None, raise_exc=None):
    def _post(*args, **kwargs):
        if raise_exc:
            raise raise_exc

        class _Resp:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                return {"choices": [{"message": {"content": content}}]}

        return _Resp()

    return _post


def test_understand_parses_object_query(monkeypatch):
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({
        "tipo": "objeto",
        "consulta_reescrita": "Nissan Skyline azul e prata",
        "pistas_pessoa": [],
        "pistas_objeto": ["Nissan Skyline", "azul e prata"],
    })
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    plan = query_llm.understand("Arrancada com skyline azul e prata")
    assert plan.ok is True
    assert plan.tipo == "objeto"
    assert plan.pistas_objeto == ["Nissan Skyline", "azul e prata"]


def test_understand_caches_successful_result(monkeypatch):
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    calls = {"n": 0}

    def _post(*args, **kwargs):
        calls["n"] += 1
        return _fake_post(content=json.dumps({"tipo": "generico"}))()

    monkeypatch.setattr(query_llm.requests, "post", _post)

    query_llm.understand("mesma consulta")
    query_llm.understand("MESMA CONSULTA")  # normaliza por lower()
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raise_exc": TimeoutError("timeout")},
        {"status_code": 500, "content": "{}"},
        {"content": "isso nao e json"},
        {"content": json.dumps({"tipo": "generico"})[:-1]},  # JSON truncado
    ],
)
def test_understand_never_raises_on_failure(monkeypatch, kwargs):
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(**kwargs))

    plan = query_llm.understand("consulta qualquer " + str(kwargs))
    assert plan.ok is False
    assert plan.tipo == "generico"


def test_failed_call_is_not_cached(monkeypatch):
    """Falha transiente não deve virar 'sem plano' permanente pra essa consulta."""
    from core import query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(raise_exc=RuntimeError("down")))
    query_llm.understand("consulta instavel")
    assert "consulta instavel" not in query_llm._get_cache()


def test_rewrite_query_passthrough_without_llm(monkeypatch):
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "")
    q = "carro azul e prata dando arrancada numa corrida de rua a noite"
    assert inference_client._understand_and_rewrite(q) == (q, [])
    assert inference_client._understand_and_rewrite("") == ("", [])


def test_rewrite_query_appends_pistas_objeto_never_replaces(monkeypatch):
    """Achado 2026-09-08: SUBSTITUIR a consulta por só as pistas encolhe o
    texto o bastante pra disparar por engano a heurística de 'consulta curta
    = título' (uma consulta de musical virou só 'bar' e passou a casar com
    qualquer título contendo essa substring). Corrigido pra só ACRESCENTAR."""
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({
        "tipo": "objeto",
        "consulta_reescrita": "nao devia aparecer no resultado",
        "pistas_objeto": ["Nissan Skyline GT-R"],
    })
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    q = "carro azul e prata dando arrancada numa corrida de rua a noite"
    out, pistas_pessoa = inference_client._understand_and_rewrite(q)
    assert out.startswith(q)  # original PRESERVADO, nunca substituído
    assert "Nissan Skyline GT-R" in out
    assert "nao devia aparecer" not in out  # consulta_reescrita não é usada
    assert pistas_pessoa == []


def test_rewrite_query_short_query_never_calls_groq(monkeypatch):
    """Consulta curta (<=7 palavras) nem chama o Groq — já é bem servida pelo
    canal de nome/personagem tolerante a erro de grafia; "consertar" a
    grafia (medido: 'Mcquen'->'McQueen') pode ATRAPALHAR esse canal."""
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    calls = {"n": 0}
    monkeypatch.setattr(query_llm.requests, "post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))

    assert inference_client._understand_and_rewrite("Mcquen") == ("Mcquen", [])
    assert inference_client._understand_and_rewrite("Brian oconner") == ("Brian oconner", [])
    assert calls["n"] == 0


def test_rewrite_query_generico_does_not_change_query(monkeypatch):
    """tipo=generico ainda não tem uma forma segura validada de melhorar a
    consulta (ver docstring de _understand_and_rewrite) — por ora só loga/
    decompõe, não altera o texto buscado."""
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({"tipo": "generico", "consulta_reescrita": "consulta limpa e curta"})
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    q = "consulta bem barroca e cheia de enrolacao mas ainda assim descritiva"
    assert inference_client._understand_and_rewrite(q) == (q, [])


def test_rewrite_query_returns_pistas_pessoa_for_tipo_pessoa(monkeypatch):
    """tipo=pessoa não altera o texto buscado (mesma cautela do genérico),
    mas devolve pistas_pessoa pro canal person_match do search_engine."""
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({
        "tipo": "pessoa",
        "consulta_reescrita": "nao devia importar aqui",
        "pistas_pessoa": ["condecorado pela realeza britanica", "tinha uma banda de heavy metal"],
    })
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    q = "ator condecorado pela rainha da inglaterra que tinha uma banda de heavy metal"
    out, pistas_pessoa = inference_client._understand_and_rewrite(q)
    assert out == q  # texto da busca não muda
    assert pistas_pessoa == ["condecorado pela realeza britanica", "tinha uma banda de heavy metal"]
