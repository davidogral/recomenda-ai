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
    assert inference_client._rewrite_query("Nissan Skyline azul") == "Nissan Skyline azul"
    assert inference_client._rewrite_query("") == ""


def test_rewrite_query_uses_pistas_objeto(monkeypatch):
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({
        "tipo": "objeto",
        "consulta_reescrita": "nao devia usar isso",
        "pistas_objeto": ["Nissan Skyline", "azul e prata"],
    })
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    out = inference_client._rewrite_query("Arrancada com skyline azul e prata")
    assert out == "Nissan Skyline azul e prata"


def test_rewrite_query_uses_consulta_reescrita_for_generico(monkeypatch):
    from core import inference_client, query_llm

    monkeypatch.setattr(query_llm, "GROQ_API_KEY", "fake-key")
    payload = json.dumps({"tipo": "generico", "consulta_reescrita": "consulta limpa"})
    monkeypatch.setattr(query_llm.requests, "post", _fake_post(content=payload))

    assert inference_client._rewrite_query("consulta bem barroca e cheia de enrolacao") == "consulta limpa"
