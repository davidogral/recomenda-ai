# -*- coding: utf-8 -*-
"""GET /engineering — payload da aba Engenharia (ablação, encoder, protocolo,
latência ao vivo). Lê os JSON versionados de eval/results/ com fallback."""


def test_engineering_payload_shape(client):
    d = client.get("/engineering").get_json()

    assert set(d) >= {"ablation", "encoder", "protocol", "live_latency", "decisions", "links"}

    abl = d["ablation"]
    assert isinstance(abl["rows"], list) and abl["rows"]
    by_pipe = {r["pipeline"]: r for r in abl["rows"]}
    assert "fusion" in by_pipe
    fusion = by_pipe["fusion"]
    # a fusão é o número-título: bem acima de qualquer sinal isolado
    assert fusion["ndcg@10"] > 0.5
    assert fusion["ndcg@10"] > by_pipe["bm25"]["ndcg@10"]
    assert "label" in fusion

    enc = d["encoder"]
    assert enc["rows"] and any(r["default"] for r in enc["rows"])

    p = d["protocol"]
    assert p["dev"] + p["test"] == p["n_queries"]

    assert "stages" in d["live_latency"]
    assert len(d["decisions"]) >= 2
    assert d["links"]["metrics"] == "/metrics"


def test_stage_percentiles_reservoir():
    from core import metrics

    for _ in range(50):
        with metrics.stage_timer("unit_probe"):
            pass
    pcts = metrics.stage_percentiles()
    assert "unit_probe" in pcts
    row = pcts["unit_probe"]
    assert row["n"] == 50
    assert row["p50"] <= row["p99"]
