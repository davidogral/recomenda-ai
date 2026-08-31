# -*- coding: utf-8 -*-
"""Schemas **Pandera** para os dados que entram no sistema.

Valida três coisas:

1. `CATALOG_SCHEMA` — o catálogo de filmes (`catalog.get_catalog_df()`), venha
   ele do SQLite ou do JSON de fallback.
2. `IMDB_RATINGS_SCHEMA` — o dump público do IMDb (`title.ratings.tsv.gz`) que
   `core/enrich.py` baixa e cruza com o catálogo.
3. `RECONCILED_SCHEMA` — as linhas de `movies` já enriquecidas (imdb_id + notas),
   para pegar reconciliação quebrada (nota fora de 1–10, tconst malformado…).

Rode `python -m core.validate` para checar tudo e gerar `data/quality_report.json`
(taxa de match + casos que falharam). Import de `pandera` é preguiçoso — o app
não depende dele em runtime.
"""

from __future__ import annotations


def _pa():
    import pandera.pandas as pa

    return pa


def catalog_schema():
    pa = _pa()
    from pandera.pandas import Column

    return pa.DataFrameSchema(
        {
            "tmdb_id": Column(int, checks=pa.Check.gt(0), unique=True, coerce=True),
            "title": Column(str, nullable=False, coerce=True),
            "release_date": Column(str, nullable=True, coerce=True, required=False),
            "release_year": Column(
                "Int64", nullable=True, coerce=True, checks=pa.Check.in_range(1870, 2100), required=False
            ),
            "runtime_minutes": Column(
                "Int64", nullable=True, coerce=True, checks=pa.Check.in_range(0, 1200), required=False
            ),
            "original_language": Column(
                str, nullable=True, coerce=True, checks=pa.Check.str_length(0, 12), required=False
            ),
            "overview": Column(str, nullable=True, coerce=True),
            "vote_average": Column(float, nullable=True, coerce=True, checks=pa.Check.in_range(0.0, 10.0)),
            "vote_count": Column("Int64", nullable=True, coerce=True, checks=pa.Check.ge(0)),
            "popularity": Column(float, nullable=True, coerce=True, checks=pa.Check.ge(0.0)),
        },
        strict=False,  # colunas extras (origin_countries, imdb_*, canon_rank…) são ok
        coerce=True,
        name="catalog",
    )


def imdb_ratings_schema():
    pa = _pa()
    from pandera.pandas import Column

    return pa.DataFrameSchema(
        {
            "tconst": Column(str, checks=pa.Check.str_matches(r"^tt\d+$"), unique=True),
            "averageRating": Column(float, checks=pa.Check.in_range(1.0, 10.0), coerce=True),
            "numVotes": Column("Int64", checks=pa.Check.ge(1), coerce=True),
        },
        strict=True,
        name="imdb_title_ratings",
    )


def reconciled_schema():
    """Linhas de `movies` COM imdb_id preenchido (após `core.enrich`)."""
    pa = _pa()
    from pandera.pandas import Column

    return pa.DataFrameSchema(
        {
            "tmdb_id": Column(int, checks=pa.Check.gt(0), unique=True, coerce=True),
            "imdb_id": Column(str, checks=pa.Check.str_matches(r"^tt\d+$")),
            "imdb_rating": Column(float, nullable=True, coerce=True, checks=pa.Check.in_range(1.0, 10.0)),
            "imdb_votes": Column("Int64", nullable=True, coerce=True, checks=pa.Check.ge(0)),
        },
        strict=False,
        coerce=True,
        name="reconciled_movies",
    )


def summarize_failures(err) -> list[dict]:
    """Extrai (coluna, check, nº de casos, amostra) de um SchemaErrors do Pandera."""
    try:
        fc = err.failure_cases  # DataFrame: schema_context, column, check, failure_case, index
    except AttributeError:
        return [{"error": str(err)[:500]}]
    out = []
    for (col, check), grp in fc.groupby(["column", "check"], dropna=False):
        out.append(
            {
                "column": None if col != col else str(col),  # NaN-safe
                "check": str(check),
                "n": int(len(grp)),
                "sample": grp["failure_case"].astype(str).head(5).tolist(),
            }
        )
    return out
