"""Enriquecimento do catálogo com sinais externos para o ranking de essenciais.

A nota/votos da TMDB são uma amostra pequena e enviesada para filme recente
(o Cidadão Kane tem ~6 mil votos na TMDB e ~500 mil no IMDb). Aqui cada filme é
cruzado com sinais melhores, gravados em colunas novas de `movies.db`:

  - imdb_id     — chave universal, obtida na TMDB (/external_ids). Imutável.
  - imdb_rating / imdb_votes — do dataset público do IMDb (amostra ~100× maior).
  - canon_rank  — posição numa lista curada do cânone (Sight & Sound + AFI...),
                  resolvida por busca na TMDB. É o eixo "resistiu ao tempo" que
                  nota e bilheteria não capturam. NULL = fora da lista.
  - metascore / rt_score — nota da CRÍTICA (Metacritic e Rotten Tomatoes) via
                  OMDb. É o eixo que separa o queridinho da crítica (Hereditário,
                  A Bruxa) do gosto do público. Precisa de OMDB_API_KEY.

É um job **offline**, roda uma vez (idempotente/retomável); o ranking em
`core.explore` passa a ler daqui, caindo para os dados da TMDB quando faltar.

    python -m core.enrich                 # tudo
    python -m core.enrich --min-votes 200 # só enriquece imdb_id de filmes com
                                          # ao menos N votos na TMDB (padrão 100)
    python -m core.enrich --skip-ratings --skip-canon   # só imdb_id, etc.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from typing import Any, Optional

import requests

from core import db, tmdb

IMDB_RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
_CACHE_DIR = os.path.join(db._PROJECT_ROOT, "data", "imdb_cache")
_RATINGS_FILE = os.path.join(_CACHE_DIR, "title.ratings.tsv.gz")

# Lista curada do cânone: (título em inglês/original, ano). A ordem é o rank
# (menor = mais canônico). Base: Sight & Sound 2022 (crítica) + clássicos de
# consenso (AFI, cinema mundial). É um sinal de BÔNUS, resolvido por busca na
# TMDB — pode conter erros de casamento (o relatório do job aponta os que não
# casaram). Curadoria: edite à vontade, como a lista de diretores.
CANON: list[tuple[str, int]] = [
    ("Jeanne Dielman, 23 quai du Commerce, 1080 Bruxelles", 1975),
    ("Vertigo", 1958), ("Citizen Kane", 1941), ("Tokyo Story", 1953),
    ("In the Mood for Love", 2000), ("2001: A Space Odyssey", 1968),
    ("Beau Travail", 1999), ("Mulholland Drive", 2001),
    ("Man with a Movie Camera", 1929), ("Singin' in the Rain", 1952),
    ("Sunrise", 1927), ("The Godfather", 1972), ("The Rules of the Game", 1939),
    ("Cléo from 5 to 7", 1962), ("The Searchers", 1956), ("Close-Up", 1990),
    ("Persona", 1966), ("Apocalypse Now", 1979), ("Seven Samurai", 1954),
    ("The Passion of Joan of Arc", 1928), ("Late Spring", 1949),
    ("Playtime", 1967), ("Do the Right Thing", 1989),
    ("The Godfather Part II", 1974), ("Rear Window", 1954), ("Psycho", 1960),
    ("8½", 1963), ("Bicycle Thieves", 1948), ("Breathless", 1960),
    ("Battleship Potemkin", 1925), ("Metropolis", 1927), ("M", 1931),
    ("Rashomon", 1950), ("Andrei Rublev", 1966), ("Stalker", 1979),
    ("Mirror", 1975), ("Taxi Driver", 1976), ("Goodfellas", 1990),
    ("Pulp Fiction", 1994), ("Raging Bull", 1980), ("Lawrence of Arabia", 1962),
    ("Casablanca", 1942), ("Some Like It Hot", 1959), ("The 400 Blows", 1959),
    ("La Dolce Vita", 1960), ("The Seventh Seal", 1957),
    ("Wild Strawberries", 1957), ("Nights of Cabiria", 1957),
    ("Aguirre, the Wrath of God", 1972), ("The Battle of Algiers", 1966),
    ("Solaris", 1972), ("Come and See", 1985), ("Yi Yi", 2000),
    ("Chungking Express", 1994), ("Barry Lyndon", 1975),
    ("A Clockwork Orange", 1971), ("The Shining", 1980),
    ("Dr. Strangelove", 1964), ("Paths of Glory", 1957), ("Blade Runner", 1982),
    ("Alien", 1979), ("Star Wars", 1977), ("Once Upon a Time in the West", 1968),
    ("The Good, the Bad and the Ugly", 1966), ("Chinatown", 1974),
    ("Network", 1976), ("Dog Day Afternoon", 1975), ("There Will Be Blood", 2007),
    ("No Country for Old Men", 2007), ("City Lights", 1931),
    ("Modern Times", 1936), ("The Gold Rush", 1925), ("The General", 1926),
    ("Sunset Boulevard", 1950), ("Notorious", 1946), ("North by Northwest", 1959),
    ("The Third Man", 1949), ("Touch of Evil", 1958), ("Pather Panchali", 1955),
    ("Ordet", 1955), ("Au Hasard Balthazar", 1966), ("L'Avventura", 1960),
    ("The Conformist", 1970), ("Wings of Desire", 1987), ("Spirited Away", 2001),
    ("Grave of the Fireflies", 1988), ("Ran", 1985), ("Ikiru", 1952),
    ("Ugetsu", 1953), ("The Apartment", 1960), ("Dr. Zhivago", 1965),
    ("It's a Wonderful Life", 1946), ("Sansho the Bailiff", 1954),
    ("La Grande Illusion", 1937), ("Pierrot le Fou", 1965), ("Le Mépris", 1963),
    ("Nashville", 1975), ("A Brighter Summer Day", 1991),
    ("Fanny and Alexander", 1982), ("Cinema Paradiso", 1988),
]


def _rw_conn() -> sqlite3.Connection:
    """Conexão de ESCRITA ao catálogo (a do app é read-only)."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Cria as colunas de enriquecimento se ainda não existirem (idempotente)."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(movies)")}
    cols = {
        "imdb_id": "TEXT",
        "imdb_rating": "REAL",
        "imdb_votes": "INTEGER",
        "canon_rank": "INTEGER",
        "metascore": "INTEGER",   # Metacritic 0..100 (crítica), via OMDb
        "rt_score": "INTEGER",    # Rotten Tomatoes tomatometer 0..100, via OMDb
    }
    for name, typ in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE movies ADD COLUMN {name} {typ}")
    # Índice para o join com o dataset do IMDb e para o ranking.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_movies_imdb_id ON movies(imdb_id)")
    conn.commit()


def fill_imdb_ids(conn: sqlite3.Connection, min_votes: int = 100) -> None:
    """Preenche movies.imdb_id via TMDB, para filmes com ao menos `min_votes`
    votos na TMDB e ainda sem imdb_id. Usa cache em disco (retomável)."""
    if not tmdb.is_configured():
        print("  ! TMDB não configurado — pulando imdb_id.")
        return
    rows = conn.execute(
        "SELECT tmdb_id FROM movies "
        "WHERE vote_count >= ? AND (imdb_id IS NULL OR imdb_id = '')",
        (min_votes,),
    ).fetchall()
    tids = [r["tmdb_id"] for r in rows]
    if not tids:
        print("  imdb_id: nada a fazer (já preenchido).")
        return
    print(f"  imdb_id: buscando {len(tids)} filmes na TMDB (threads)...")
    tmdb.prefetch_imdb_ids(tids)  # aquece o cache em paralelo
    # Agora lê do cache (rápido) e grava em lote. "" marca "sem imdb_id" p/ não
    # rebuscar num próximo run.
    updates = [(tmdb.imdb_id(t) or "", t) for t in tids]
    conn.executemany("UPDATE movies SET imdb_id = ? WHERE tmdb_id = ?", updates)
    conn.commit()
    got = sum(1 for v, _ in updates if v)
    print(f"  imdb_id: {got}/{len(tids)} resolvidos.")


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        os.replace(tmp, dest)


def fill_imdb_ratings(conn: sqlite3.Connection, refresh: bool = False) -> None:
    """Baixa o dataset público do IMDb (title.ratings) e grava nota/votos nos
    filmes com imdb_id. Só mantém em memória os tconsts que temos (leve)."""
    ours = {r["imdb_id"] for r in conn.execute(
        "SELECT imdb_id FROM movies WHERE imdb_id IS NOT NULL AND imdb_id <> ''")}
    if not ours:
        print("  notas IMDb: nenhum imdb_id no catálogo ainda — rode imdb_id antes.")
        return
    if refresh or not os.path.exists(_RATINGS_FILE):
        print(f"  notas IMDb: baixando dataset ({IMDB_RATINGS_URL})...")
        _download(IMDB_RATINGS_URL, _RATINGS_FILE)
    updates = []
    with gzip.open(_RATINGS_FILE, "rt", encoding="utf-8") as f:
        next(f)  # cabeçalho: tconst  averageRating  numVotes
        for line in f:
            tconst, avg, n = line.rstrip("\n").split("\t")
            if tconst in ours:
                updates.append((float(avg), int(n), tconst))
    _validate_imdb_rows(updates)  # schema Pandera no resultado do dump do IMDb
    conn.executemany(
        "UPDATE movies SET imdb_rating = ?, imdb_votes = ? WHERE imdb_id = ?",
        updates,
    )
    conn.commit()
    print(f"  notas IMDb: {len(updates)}/{len(ours)} filmes casados.")


def _validate_imdb_rows(updates: list[tuple[float, int, str]]) -> None:
    """Valida (amostra de) as linhas parseadas do dump IMDb com o schema Pandera.
    Só avisa — o dado é do IMDb, aqui a gente só reporta o que não bate."""
    if not updates:
        return
    try:
        import pandas as pd

        from core.schemas import imdb_ratings_schema, summarize_failures
        df = pd.DataFrame(updates, columns=["averageRating", "numVotes", "tconst"])
        try:
            imdb_ratings_schema().validate(df.head(50_000), lazy=True)
        except Exception as e:  # pandera SchemaErrors
            fails = summarize_failures(e)
            print(f"  ! schema do dump IMDb: {len(fails)} tipo(s) de falha — {fails[:2]}")
    except ImportError:
        pass  # pandera opcional


OMDB_URL = "https://www.omdbapi.com/"
_omdb_cache = tmdb._JsonCache(os.path.join(db._PROJECT_ROOT, "data", "tmdb_cache", "omdb.json"))


def _omdb_key() -> str:
    """Chave real do ambiente; senão a de demonstração (só para testes pequenos —
    ela é limitada e não aguenta o catálogo inteiro)."""
    return os.environ.get("OMDB_API_KEY", "").strip() or "trilogy"


def _pct(s: Any) -> Optional[int]:
    try:
        return int(str(s).rstrip("%").split("/")[0])
    except (TypeError, ValueError):
        return None


def _omdb_scores(imdb_id: str) -> tuple[Optional[int], Optional[int], str]:
    """(metascore, rt_score, status) do OMDb para um imdb_id. status:
    cached | ok | empty (filme sem ficha) | limit (cota/chave) | error (rede).
    Só cacheia 'ok' e 'empty'; 'limit'/'error' não cacheiam (para retentar)."""
    if imdb_id in _omdb_cache:
        v = _omdb_cache.get(imdb_id)
        return (v[0], v[1], "cached") if v else (None, None, "cached")
    try:
        d = requests.get(OMDB_URL, params={"i": imdb_id, "apikey": _omdb_key()},
                         timeout=15).json()
    except (requests.RequestException, ValueError):
        return (None, None, "error")  # rede/JSON: não cacheia
    if d.get("Response") == "False":
        err = (d.get("Error") or "").lower()
        if "limit" in err or "api key" in err:  # cota diária ou chave inválida
            return (None, None, "limit")         # NÃO cacheia — retenta depois
        _omdb_cache.set(imdb_id, None)           # filme realmente sem ficha OMDb
        return (None, None, "empty")
    ms = _pct(d.get("Metascore")) if (d.get("Metascore") or "N/A") != "N/A" else None
    rt = None
    for x in d.get("Ratings", []):
        if "Tomatoes" in (x.get("Source") or ""):
            rt = _pct(x.get("Value"))
    _omdb_cache.set(imdb_id, [ms, rt])
    return (ms, rt, "ok")


def fill_critic(conn: sqlite3.Connection, min_votes: int = 25000,
                max_calls: int = 950) -> None:
    """Preenche metascore/rt_score (crítica) via OMDb, para filmes com imdb_id e
    ao menos `min_votes` votos no IMDb, ainda sem crítica. Resumível: para ao
    bater `max_calls` chamadas reais (a chave grátis do OMDb dá 1000/dia) ou ao
    esgotar a cota — rode de novo no dia seguinte para continuar de onde parou."""
    if not os.environ.get("OMDB_API_KEY", "").strip():
        print("  ! OMDB_API_KEY ausente — usando a chave de demonstração (limitada). "
              "Para o catálogo todo, ponha uma chave real no .env.")
    rows = conn.execute(
        "SELECT imdb_id FROM movies WHERE imdb_id <> '' AND imdb_votes >= ? "
        "AND metascore IS NULL AND rt_score IS NULL",
        (min_votes,),
    ).fetchall()
    ids = [r["imdb_id"] for r in rows]
    if not ids:
        print("  crítica: nada a fazer (já preenchido para esse corte).")
        return
    print(f"  crítica: {len(ids)} pendentes (teto de {max_calls} chamadas/rodada)...")
    updates, calls, stopped = [], 0, False
    for iid in ids:
        ms, rt, st = _omdb_scores(iid)
        if st == "limit":
            print(f"  crítica: cota do OMDb esgotada após {calls} chamadas — "
                  "rode de novo amanhã para continuar."); stopped = True; break
        if st in ("ok", "empty", "cached"):
            updates.append((ms, rt, iid))
        if st in ("ok", "empty", "error"):        # gastou 1 chamada real
            calls += 1
        if calls >= max_calls:
            print(f"  crítica: teto de {max_calls} chamadas atingido — "
                  "rode de novo amanhã para continuar."); stopped = True; break
    _omdb_cache.flush()
    conn.executemany("UPDATE movies SET metascore = ?, rt_score = ? WHERE imdb_id = ?",
                     updates)
    conn.commit()
    got = sum(1 for ms, rt, _ in updates if ms is not None or rt is not None)
    restantes = len(ids) - len(updates) if stopped else 0
    print(f"  crítica: +{got} com nota (de {len(updates)} processados; "
          f"~{restantes} ainda pendentes).")


def _resolve(title: str, year: int, tries: int = 3) -> Optional[int]:
    """Resolve (título, ano) → tmdb_id de forma resiliente: throttle leve e, em
    falha, esquece a chave (a TMDB às vezes devolve vazio sob rajada e isso
    envenenaria o cache) antes de tentar de novo com backoff."""
    key = f"{tmdb._norm(title)}|{year or ''}"
    for i in range(tries):
        time.sleep(0.15)  # não estoura o limite de rajada da TMDB
        tid = tmdb.search_movie_id(title, year)
        if tid is not None:
            return tid
        tmdb._search_cache.data.pop(key, None)  # descarta possível None envenenado
        time.sleep(0.4 * (i + 1))
    return None


def fill_canon(conn: sqlite3.Connection) -> None:
    """Marca canon_rank nos filmes da lista CANON, resolvendo (título, ano) para
    tmdb_id via busca na TMDB. Reporta os que não casaram para revisão."""
    if not tmdb.is_configured():
        print("  ! TMDB não configurado — pulando cânone.")
        return
    have_ids = {r["tmdb_id"] for r in conn.execute("SELECT tmdb_id FROM movies")}
    conn.execute("UPDATE movies SET canon_rank = NULL")  # recomeça limpo
    matched, misses = 0, []
    for rank, (title, year) in enumerate(CANON, start=1):
        tid = _resolve(title, year)
        if tid and tid in have_ids:
            conn.execute("UPDATE movies SET canon_rank = ? WHERE tmdb_id = ?",
                         (rank, tid))
            matched += 1
        else:
            misses.append(f"{title} ({year})" + ("" if tid else " [sem tmdb_id]"))
    conn.commit()
    print(f"  cânone: {matched}/{len(CANON)} casados no catálogo.")
    if misses:
        print("  cânone — não casaram (fora do catálogo ou título a ajustar):")
        for m in misses:
            print("      -", m)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Enriquece movies.db com sinais externos.")
    ap.add_argument("--min-votes", type=int, default=100,
                    help="votos mínimos na TMDB para buscar imdb_id (padrão 100)")
    ap.add_argument("--skip-ids", action="store_true")
    ap.add_argument("--skip-ratings", action="store_true")
    ap.add_argument("--skip-canon", action="store_true")
    ap.add_argument("--skip-critic", action="store_true",
                    help="não busca Metacritic/RT no OMDb (padrão: busca)")
    ap.add_argument("--critic-min-votes", type=int, default=25000,
                    help="votos no IMDb para buscar crítica no OMDb (padrão 25000)")
    ap.add_argument("--refresh-imdb", action="store_true",
                    help="rebaixa o dataset do IMDb mesmo se já estiver em cache")
    args = ap.parse_args(argv)

    if not db.has_sqlite():
        print(f"movies.db não encontrado em {db.DB_PATH}", file=sys.stderr)
        return 1

    conn = _rw_conn()
    try:
        print("→ garantindo colunas...")
        ensure_columns(conn)
        if not args.skip_ids:
            print("→ imdb_id (TMDB)...")
            fill_imdb_ids(conn, args.min_votes)
        if not args.skip_ratings:
            print("→ notas/votos do IMDb...")
            fill_imdb_ratings(conn, refresh=args.refresh_imdb)
        if not args.skip_canon:
            print("→ cânone (Sight & Sound + clássicos)...")
            fill_canon(conn)
        if not args.skip_critic:
            print("→ crítica (Metacritic + Rotten Tomatoes via OMDb)...")
            fill_critic(conn, args.critic_min_votes)
        print("✓ enriquecimento concluído.")
    finally:
        conn.close()

    # Relatório de qualidade (taxa de match + casos que falharam).
    try:
        from core import validate as _v
        rep = _v.build_report()
        with open(_v.REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
        rc = rep["reconciliation"].get("match_rate", {})
        if rc:
            print(f"→ relatório: imdb_id {rc['imdb_id']:.1%}, nota IMDb "
                  f"{rc['imdb_rating_overall']:.1%} — data/quality_report.json")
    except Exception as e:  # nunca deixa o relatório derrubar o job
        print(f"  ! relatório de qualidade falhou: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
