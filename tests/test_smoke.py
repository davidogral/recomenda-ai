# -*- coding: utf-8 -*-
"""Smoke tests hermético (sem modelos, sem rede) para o portão de CI."""

import os

os.environ.setdefault("RECOMENDAI_NO_WARMUP", "1")
os.environ.setdefault("RECOMENDAI_TMDB_NAMES", "0")


def test_app_boots_and_exposes_health_and_metrics():
    import app

    c = app.app.test_client()
    assert c.get("/health").get_json()["status"] == "ok"
    m = c.get("/metrics")
    assert m.status_code == 200 and b"recomendaai_" in m.data


def test_inference_client_defaults_to_local():
    from core import inference_client

    assert inference_client.is_remote() is False


def test_inference_service_contract():
    import inference.main as im

    paths = {getattr(r, "path", "") for r in im.app.routes}
    assert {"/v1/search_combined", "/v1/similar", "/v1/recommend_from_profile", "/health"} <= paths


def test_catalog_schema_validates_current_catalog():
    from core import catalog
    from core.schemas import catalog_schema

    df = catalog.get_catalog_df()
    catalog_schema().validate(df.head(1000), lazy=True)


def test_tmdb_rate_cap_configured():
    from core import tmdb

    st = tmdb.tmdb_rate_state()
    assert set(st) == {"calls_last_60s", "max_rpm"}
    assert st["max_rpm"] >= 0


def test_metrics_helpers_are_safe_noops_without_labels():
    from core import metrics

    metrics.query_cache_event(True)
    metrics.tmdb_call("ok")
    with metrics.stage_timer("retrieval"):
        pass
    body, ctype = metrics.render_latest()
    assert isinstance(body, (bytes, bytearray)) and "text" in ctype
