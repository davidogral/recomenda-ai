# -*- coding: utf-8 -*-
"""Valida os dados com os schemas Pandera e gera `data/quality_report.json`.

    python -m core.validate            # checa e escreve o relatório
    python -m core.validate --strict   # sai != 0 se algum schema falhar (CI)

Cobre:
  * catálogo (SQLite ou JSON)                    -> core.schemas.catalog_schema
  * dump IMDb title.ratings (amostra)            -> core.schemas.imdb_ratings_schema
  * reconciliação catálogo x IMDb (taxa de match + casos que falharam)
  * cânone (matched/total + títulos que não casaram)

O relatório documenta a **taxa de match** e os **casos que falharam** — pedido do
professor para o projeto ir a produção.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time

from core import db

_ROOT = db._PROJECT_ROOT
REPORT_PATH = os.path.join(_ROOT, "data", "quality_report.json")
_IMDB_TSV = os.path.join(_ROOT, "data", "imdb_cache", "title.ratings.tsv.gz")


def _validate(df, schema, sample: int | None = None) -> dict:
    """Roda um schema Pandera e devolve {ok, n_rows, failures[]}."""
    import pandera.errors as pae

    frame = df.sample(n=min(sample, len(df)), random_state=0) if sample else df
    try:
        schema.validate(frame, lazy=True)
        return {"ok": True, "n_rows": int(len(frame)), "failures": []}
    except pae.SchemaErrors as e:
        from core.schemas import summarize_failures
        return {"ok": False, "n_rows": int(len(frame)), "failures": summarize_failures(e)}


def check_catalog() -> dict:
    from core import catalog
    from core.schemas import catalog_schema

    df = catalog.get_catalog_df()
    res = _validate(df, catalog_schema())
    res["source"] = "sqlite" if db.has_sqlite() else "json"
    res["n_movies"] = int(len(df))
    return res


def check_imdb_dump() -> dict:
    if not os.path.exists(_IMDB_TSV):
        return {"ok": None, "skipped": "data/imdb_cache/title.ratings.tsv.gz ausente "
                "(rode `python -m core.enrich`)"}
    import pandas as pd
    from core.schemas import imdb_ratings_schema

    df = pd.read_csv(_IMDB_TSV, sep="\t", dtype=str, na_values=["\\N"])
    df = df.rename(columns=str.strip)
    res = _validate(df, imdb_ratings_schema(), sample=200_000)
    res["n_rows_total"] = int(len(df))
    return res


def check_reconciliation() -> dict:
    """Taxa de match catálogo->imdb_id->nota IMDb + casos que falharam."""
    if not db.has_sqlite():
        return {"ok": None, "skipped": "sem movies.db — reconciliação vive lá"}
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(movies)")}
    if "imdb_id" not in cols:
        conn.close()
        return {"ok": None, "skipped": "colunas de enriquecimento ausentes "
                "(rode `python -m core.enrich`)"}

    total = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    with_id = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE imdb_id IS NOT NULL AND imdb_id <> ''").fetchone()[0]
    with_rating = conn.execute(
        "SELECT COUNT(*) FROM movies WHERE imdb_rating IS NOT NULL").fetchone()[0]
    # filmes populares (>=100 votos TMDB) que deveriam ter imdb_id e não têm
    miss_id = [dict(r) for r in conn.execute(
        "SELECT tmdb_id, title, release_year, vote_count FROM movies "
        "WHERE (imdb_id IS NULL OR imdb_id = '') AND vote_count >= 100 "
        "ORDER BY vote_count DESC LIMIT 15")]
    # tem imdb_id mas o dump do IMDb não trouxe nota (TV movie, curta, raridade)
    miss_rating = [dict(r) for r in conn.execute(
        "SELECT tmdb_id, title, release_year, imdb_id FROM movies "
        "WHERE imdb_id <> '' AND imdb_rating IS NULL AND vote_count >= 100 "
        "ORDER BY vote_count DESC LIMIT 15")]

    # schema nas linhas reconciliadas
    import pandas as pd
    from core.schemas import reconciled_schema
    rec_df = pd.read_sql_query(
        "SELECT tmdb_id, imdb_id, imdb_rating, imdb_votes FROM movies "
        "WHERE imdb_id IS NOT NULL AND imdb_id <> ''", conn)
    schema_res = _validate(rec_df, reconciled_schema())

    canon = None
    if "canon_rank" in cols:
        n_canon = conn.execute(
            "SELECT COUNT(*) FROM movies WHERE canon_rank IS NOT NULL").fetchone()[0]
        try:
            from core.enrich import CANON
            canon = {"matched": int(n_canon), "total": len(CANON),
                     "rate": round(n_canon / len(CANON), 4)}
        except Exception:
            canon = {"matched": int(n_canon)}
    conn.close()

    return {
        "ok": schema_res["ok"],
        "schema_failures": schema_res["failures"],
        "counts": {"catalog": total, "with_imdb_id": with_id, "with_imdb_rating": with_rating},
        "match_rate": {
            "imdb_id": round(with_id / total, 4) if total else 0.0,
            "imdb_rating_given_id": round(with_rating / with_id, 4) if with_id else 0.0,
            "imdb_rating_overall": round(with_rating / total, 4) if total else 0.0,
        },
        "failed_cases": {
            "popular_without_imdb_id": miss_id,
            "has_id_but_no_imdb_rating": miss_rating,
        },
        "canon": canon,
    }


def build_report() -> dict:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ratings_provenance": "unconfirmed",  # ver README §Proveniência dos Dados
        "ratings_provenance_note": (
            "~2,9M avaliações em movies.db.ratings — cara de MovieLens/GroupLens "
            "(escala 0.5–5.0, reconciliação movieId->tmdb_id), release exato e "
            "script de ingestão NÃO documentados. Bloqueador de deploy público."),
        "catalog": check_catalog(),
        "imdb_dump": check_imdb_dump(),
        "reconciliation": check_reconciliation(),
    }


def _md(rep: dict) -> str:
    L = ["## Relatório de qualidade dos dados", "",
         f"_gerado em {rep['generated_at']}_", ""]
    c = rep["catalog"]
    L.append(f"**Catálogo** ({c['source']}, {c.get('n_movies', '?')} filmes): "
             + ("✅ schema ok" if c["ok"] else f"❌ {len(c['failures'])} tipo(s) de falha"))
    for f in c.get("failures", []):
        L.append(f"  - `{f.get('column')}` / {f.get('check')}: {f['n']} casos, ex.: {f['sample']}")

    d = rep["imdb_dump"]
    if d.get("skipped"):
        L.append(f"\n**Dump IMDb**: ⏭️ {d['skipped']}")
    else:
        L.append(f"\n**Dump IMDb** ({d.get('n_rows_total', '?')} linhas, amostra {d['n_rows']}): "
                 + ("✅ schema ok" if d["ok"] else f"❌ {len(d['failures'])} falha(s)"))

    r = rep["reconciliation"]
    if r.get("skipped"):
        L.append(f"\n**Reconciliação catálogo × IMDb**: ⏭️ {r['skipped']}")
    else:
        mr = r["match_rate"]
        L += ["\n**Reconciliação catálogo × IMDb**",
              f"  - `imdb_id` resolvido: **{mr['imdb_id']:.1%}** dos filmes",
              f"  - nota IMDb (entre os com id): **{mr['imdb_rating_given_id']:.1%}**  "
              f"(geral: {mr['imdb_rating_overall']:.1%})",
              f"  - schema das linhas reconciliadas: "
              + ("✅ ok" if r["ok"] else f"❌ {len(r['schema_failures'])} falha(s)")]
        if r.get("canon"):
            k = r["canon"]
            L.append(f"  - cânone: {k.get('matched')}/{k.get('total', '?')} casados")
        mi = r["failed_cases"]["popular_without_imdb_id"]
        if mi:
            L.append(f"  - populares SEM `imdb_id` (top {len(mi)}): "
                     + ", ".join(f"{m['title']} ({m['release_year']})" for m in mi[:8]))
        mrr = r["failed_cases"]["has_id_but_no_imdb_rating"]
        if mrr:
            L.append(f"  - com `imdb_id` mas SEM nota no dump: "
                     + ", ".join(f"{m['title']} ({m['release_year']})" for m in mrr[:8]))

    L.append(f"\n**Proveniência dos ratings**: ⚠️ `{rep['ratings_provenance']}` — {rep['ratings_provenance_note']}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Valida dados (Pandera) e gera quality_report.json.")
    ap.add_argument("--strict", action="store_true", help="exit != 0 se algum schema falhar")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    rep = build_report()
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2)
    if not a.quiet:
        print(_md(rep))
        print(f"\n» {os.path.relpath(REPORT_PATH, _ROOT)}")

    failed = [k for k in ("catalog", "imdb_dump", "reconciliation")
              if rep[k].get("ok") is False]
    if a.strict and failed:
        print(f"\nSTRICT: schema falhou em {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
