# -*- coding: utf-8 -*-
"""core/events.py — registros de uso: log fire-and-forget, analytics, retenção,
export por usuário e cascata na exclusão de conta."""

import uuid

from core import events, user_data
from core.user_data import _connect


def _q():
    return "consulta-teste-" + uuid.uuid4().hex[:8]


def test_log_and_analytics():
    q = _q()
    events.log(
        "search", query=q, filters={"genre": "Terror"}, n_results=3, latency_ms=12.0, stage_ms={"retrieval": 5.0}
    )
    events.log("search", query=q, n_results=0, latency_ms=9.0)  # sem resultado
    a = events.analytics(30)

    assert q in {r["query"] for r in a["top_queries"]}
    assert q in {r["query"] for r in a["zero_result"]}
    assert a["by_kind"].get("search", 0) >= 2
    assert "retrieval" in a["latency"]
    assert any(g["genre"] == "Terror" for g in a["filters"]["genres"])


def test_log_never_raises_on_bad_input():
    # tipos zoados não podem derrubar o request
    events.log("search", query=None, filters="não é dict", n_results="x", stage_ms=object())  # type: ignore[arg-type]


def test_retention_purge():
    with _connect() as conn:
        events._ensure(conn)
        conn.execute(
            "INSERT INTO events (ts, day, kind) VALUES (?, ?, 'search')",
            ("2020-01-01T00:00:00Z", "2020-01-01"),
        )
    removed = events.purge_expired()
    assert removed >= 1
    with _connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM events WHERE day = '2020-01-01'").fetchone()[0]
    assert left == 0


def test_export_and_delete_cascade():
    from core import users

    email = f"ev{uuid.uuid4().hex[:8]}@example.com"
    u = users.create_user(email, "password123")
    uid = u["user_id"]

    events.log("search", query=_q(), n_results=1, user_id=uid)
    events.log("similar", query="603", n_results=5, user_id=uid)
    assert len(events.export_for_user(uid)) == 2

    assert users.delete_user(uid) is True
    assert events.export_for_user(uid) == []


def test_engagement_by_day_shape():
    rows = user_data.engagement_by_day(7)
    assert len(rows) == 7
    assert set(rows[0]) == {"day", "ratings", "reviews", "lists"}
