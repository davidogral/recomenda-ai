# -*- coding: utf-8 -*-
"""Registros de uso — buscas, filtros, latência (LGPD: dado pessoal quando
`user_id` não é nulo).

Tabela `events` no mesmo `data/user.db`. `log()` é **fire-and-forget**: nunca
levanta e nunca deixa uma falha de registro quebrar a requisição. Retenção
padrão de 90 dias (`RECOMENDAI_EVENT_RETENTION_DAYS`), aplicada de forma
preguiçosa (uma fração das escritas apaga o que passou do prazo).

Base legal: legítimo interesse (entender o uso, achar buracos de catálogo) e,
quando associado à conta logada, o consentimento do cadastro. Entra no
`GET /auth/export` e é apagado junto com a conta (`core/users.delete_user`).
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.user_data import _connect

RETENTION_DAYS = int(os.environ.get("RECOMENDAI_EVENT_RETENTION_DAYS", "90"))
_MAX_QUERY = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    day        TEXT NOT NULL,
    kind       TEXT NOT NULL,
    query      TEXT NOT NULL DEFAULT '',
    filters    TEXT NOT NULL DEFAULT '',
    n_results  INTEGER,
    found      INTEGER,
    latency_ms REAL,
    stage_ms   TEXT NOT NULL DEFAULT '',
    user_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_kind_day ON events(kind, day);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
"""

_ensured = False


def _ensure(conn) -> None:
    global _ensured
    if not _ensured:
        conn.executescript(_SCHEMA)
        _ensured = True


def _now():
    return datetime.now(timezone.utc)


def log(
    kind: str,
    *,
    query: str = "",
    filters: Optional[dict] = None,
    n_results: Optional[int] = None,
    latency_ms: Optional[float] = None,
    stage_ms: Optional[dict] = None,
    user_id: Optional[int] = None,
) -> None:
    """Registra um evento. Silencioso em qualquer falha."""
    try:
        now = _now()
        with _connect() as conn:
            _ensure(conn)
            conn.execute(
                "INSERT INTO events (ts, day, kind, query, filters, n_results, found, latency_ms, stage_ms, user_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    now.strftime("%Y-%m-%d"),
                    str(kind)[:40],
                    (query or "").strip()[:_MAX_QUERY],
                    json.dumps(filters, ensure_ascii=False, sort_keys=True) if filters else "",
                    n_results,
                    None if n_results is None else (1 if n_results > 0 else 0),
                    round(latency_ms, 2) if latency_ms is not None else None,
                    json.dumps(stage_ms, sort_keys=True) if stage_ms else "",
                    int(user_id) if user_id else None,
                ),
            )
            if random.random() < 0.03:
                cutoff = (now - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
                conn.execute("DELETE FROM events WHERE day < ?", (cutoff,))
    except Exception:
        pass


def purge_expired() -> int:
    """Apaga eventos além da retenção; devolve quantos. Uso manual/manutenção."""
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    try:
        with _connect() as conn:
            _ensure(conn)
            return conn.execute("DELETE FROM events WHERE day < ?", (cutoff,)).rowcount
    except Exception:
        return 0


def export_for_user(user_id: int) -> list[dict]:
    """Eventos de uma conta — para o `GET /auth/export`."""
    try:
        with _connect() as conn:
            _ensure(conn)
            rows = conn.execute(
                "SELECT ts, kind, query, filters, n_results, latency_ms FROM events WHERE user_id = ? ORDER BY ts DESC",
                (int(user_id),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def delete_for_user(conn, user_id: int) -> None:
    """Apaga os eventos de uma conta — chamado de dentro de `users.delete_user`
    (mesma conexão/transação)."""
    conn.execute("DELETE FROM events WHERE user_id = ?", (int(user_id),))


# --------------------------------------------------------------------- analytics
def _pct(xs: list) -> dict:
    xs = sorted(xs)
    n = len(xs)

    def p(q: float) -> float:
        return round(xs[min(n - 1, max(0, int(round(q / 100.0 * n)) - 1))], 1)

    return {"p50": p(50), "p95": p(95), "p99": p(99), "n": n}


def analytics(days: int = 30) -> dict:
    """Resumo dos últimos `days` dias para o painel admin."""
    days = max(1, min(int(days), 365))
    since = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out: dict = {"days": days, "since": since}
    try:
        with _connect() as conn:
            _ensure(conn)

            out["by_kind"] = dict(
                conn.execute("SELECT kind, COUNT(*) FROM events WHERE day >= ? GROUP BY kind", (since,))
            )

            byday: dict = {}
            for d, k, n in conn.execute(
                "SELECT day, kind, COUNT(*) FROM events WHERE day >= ? GROUP BY day, kind", (since,)
            ):
                byday.setdefault(d, {})[k] = n
            out["by_day"] = [{"day": d, **v} for d, v in sorted(byday.items())]

            out["top_queries"] = [
                {"query": q, "count": c}
                for q, c in conn.execute(
                    "SELECT query, COUNT(*) c FROM events"
                    " WHERE kind = 'search' AND query <> '' AND day >= ?"
                    " GROUP BY lower(query) ORDER BY c DESC LIMIT 25",
                    (since,),
                )
            ]
            out["zero_result"] = [
                {"query": q, "count": c}
                for q, c in conn.execute(
                    "SELECT query, COUNT(*) c FROM events"
                    " WHERE kind = 'search' AND found = 0 AND query <> '' AND day >= ?"
                    " GROUP BY lower(query) ORDER BY c DESC LIMIT 25",
                    (since,),
                )
            ]

            n_search = out["by_kind"].get("search", 0)
            gen: dict = {}
            yr = lang = with_filter = 0
            for (fj,) in conn.execute(
                "SELECT filters FROM events WHERE kind = 'search' AND filters <> '' AND day >= ?", (since,)
            ):
                try:
                    f = json.loads(fj)
                except Exception:
                    continue
                with_filter += 1
                if f.get("genre"):
                    gen[f["genre"]] = gen.get(f["genre"], 0) + 1
                if f.get("year_min") or f.get("year_max"):
                    yr += 1
                if f.get("language"):
                    lang += 1
            out["filters"] = {
                "of_searches": n_search,
                "with_any_filter": with_filter,
                "year_range": yr,
                "language": lang,
                "genres": sorted(({"genre": g, "count": c} for g, c in gen.items()), key=lambda x: -x["count"])[:15],
            }

            samples: dict = {}
            for (sj,) in conn.execute(
                "SELECT stage_ms FROM events WHERE stage_ms <> '' AND day >= ? ORDER BY event_id DESC LIMIT 5000",
                (since,),
            ):
                try:
                    s = json.loads(sj)
                except Exception:
                    continue
                for k, v in s.items():
                    try:
                        samples.setdefault(k, []).append(float(v))
                    except (TypeError, ValueError):
                        pass
            out["latency"] = {k: _pct(v) for k, v in samples.items() if v}
    except Exception as e:  # analytics nunca derruba o painel
        out["error"] = str(e)
    return out
