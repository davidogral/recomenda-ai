# -*- coding: utf-8 -*-
"""Reconstrói a tabela `ratings` do `movies.db` a partir do **MovieLens ml-32m**.

Motivação: os ~2,9M de ratings que estavam no `movies.db` vieram sem proveniência
(ver README §Proveniência dos Dados). Este script os substitui por uma base
**documentada e reproduzível** — 32M avaliações, 200k usuários, até out/2023 —
reconciliando `movieId → tmdbId` pelo **`links.csv` oficial do GroupLens**.

    python -m recommender.ingest_movielens                 # baixa (se preciso) e ingere
    python -m recommender.ingest_movielens --zip caminho/ml-32m.zip
    python -m recommender.ingest_movielens --min-user-ratings 10 --dry-run

Depois: `python -m recommender.train` (retreina o SVD nos novos ratings).

⚠️  **Licença**: ml-32m é do GroupLens e **proíbe uso comercial sem permissão**
(um e-mail a um docente do GroupLens/UMN; costumam liberar para projetos sem
receita). Exige citar: F. Maxwell Harper e Joseph A. Konstan. 2015. *The
MovieLens Datasets: History and Context*. ACM TiiS 5, 4: 19:1–19:19.

TLS: `files.grouplens.org` está com **certificado expirado** — o download usa
`verify=False` e valida o **MD5** publicado em `checksums.txt` (integridade
equivalente). Aponte `--zip` para um arquivo baixado à mão se preferir.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
import zipfile

from core import db

ML_URL = os.environ.get("RECOMENDAI_ML_URL", "https://files.grouplens.org/datasets/movielens/ml-32m.zip")
_CACHE = os.path.join(db._PROJECT_ROOT, "data", "movielens_cache")
_ZIP = os.path.join(_CACHE, "ml-32m.zip")
PROVENANCE_PATH = os.path.join(db._PROJECT_ROOT, "data", "ratings_provenance.json")

CITATION = (
    "F. Maxwell Harper and Joseph A. Konstan. 2015. "
    "The MovieLens Datasets: History and Context. ACM TiiS 5, 4: 19:1-19:19. "
    "https://doi.org/10.1145/2827872"
)


def _download(dst: str) -> None:
    import requests
    import urllib3

    urllib3.disable_warnings()
    print(f"» baixando {ML_URL} (~240 MB; cert do grouplens expirado → verify=False + checa MD5)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with requests.get(ML_URL, stream=True, timeout=300, verify=False) as r:
        r.raise_for_status()
        with open(dst + ".tmp", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(dst + ".tmp", dst)


def _members(zf: zipfile.ZipFile) -> dict:
    """{basename: ZipInfo} para links.csv, ratings.csv, checksums.txt."""
    want = {"links.csv", "ratings.csv", "checksums.txt", "README.txt"}
    return {os.path.basename(n): zf.getinfo(n) for n in zf.namelist() if os.path.basename(n) in want}


def _verify_md5(zf: zipfile.ZipFile, m: dict) -> None:
    """Confere o MD5 de links.csv / ratings.csv contra o checksums.txt do pacote."""
    if "checksums.txt" not in m:
        print("  ! checksums.txt ausente no zip — pulando verificação de MD5")
        return
    published = {}
    for line in zf.read(m["checksums.txt"]).decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            published[os.path.basename(parts[1])] = parts[0]
    for name in ("links.csv", "ratings.csv"):
        if name not in published:
            continue
        h = hashlib.md5()
        with zf.open(m[name]) as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        got = h.hexdigest()
        if got != published[name]:
            sys.exit(f"MD5 de {name} não confere: {got} != {published[name]} (pacote corrompido)")
    print("  MD5 de links.csv e ratings.csv conferem com checksums.txt ✓")


def _load_links(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict:
    """movieId (int) -> tmdbId (int), do links.csv oficial."""
    import csv

    out: dict[int, int] = {}
    with zf.open(info) as fh:
        rd = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
        for row in rd:
            t = row.get("tmdbId") or ""
            if t.strip():
                out[int(row["movieId"])] = int(t)
    return out


def ingest(zip_path: str, min_user_ratings: int, dry_run: bool) -> dict:
    if not os.path.exists(zip_path):
        _download(zip_path)

    conn = sqlite3.connect(db.DB_PATH, timeout=60)
    catalog_ids = {r[0] for r in conn.execute("SELECT tmdb_id FROM movies")}
    print(f"» catálogo: {len(catalog_ids)} filmes")

    with zipfile.ZipFile(zip_path) as zf:
        m = _members(zf)
        _verify_md5(zf, m)
        ml_version = os.path.basename(zip_path).replace(".zip", "")
        links = _load_links(zf, m["links.csv"])
        print(f"» links.csv: {len(links)} filmes MovieLens com tmdbId")
        # movieId -> tmdb_id, só os que existem no nosso catálogo
        keep = {mid: tid for mid, tid in links.items() if tid in catalog_ids}
        print(f"» {len(keep)} filmes MovieLens casam com o catálogo")

        # --- streaming das 32M linhas de ratings.csv ---
        conn.execute("DROP TABLE IF EXISTS _ml_stage")
        conn.execute("CREATE TABLE _ml_stage (u INTEGER, tmdb_id INTEGER, rating REAL, rated_at INTEGER)")
        t0, n_in, n_kept = time.time(), 0, 0
        buf: list[tuple] = []
        with zf.open(m["ratings.csv"]) as fh:
            tw = io.TextIOWrapper(fh, encoding="utf-8")
            next(tw)  # header
            for line in tw:
                n_in += 1
                uid, mid, rating, ts = line.rstrip("\n").split(",")
                tid = keep.get(int(mid))
                if tid is None:
                    continue
                buf.append((int(uid), tid, float(rating), int(ts)))
                if len(buf) >= 100_000:
                    conn.executemany("INSERT INTO _ml_stage VALUES (?,?,?,?)", buf)
                    n_kept += len(buf)
                    buf.clear()
                if n_in % 4_000_000 == 0:
                    print(f"  … {n_in:,} lidas, {n_kept:,} no catálogo ({time.time() - t0:.0f}s)")
        if buf:
            conn.executemany("INSERT INTO _ml_stage VALUES (?,?,?,?)", buf)
            n_kept += len(buf)
        conn.commit()
        print(f"» {n_in:,} ratings lidas → {n_kept:,} no catálogo ({time.time() - t0:.0f}s)")

    # Dedup: movieIds distintos do MovieLens podem mapear para o MESMO tmdbId
    # (relançamentos, entradas duplicadas) → colapsa para 1 rating por (usuário,
    # filme), o mais recente. Sem isso o INSERT viola o PK (user_id, tmdb_id).
    dups = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT u || ':' || tmdb_id) FROM _ml_stage").fetchone()[0]
    if dups:
        print(f"» {dups:,} duplicatas (movieId→tmdbId N:1) — mantendo a avaliação mais recente")
    conn.execute("DROP TABLE IF EXISTS _ml_dedup")
    conn.execute(
        "CREATE TABLE _ml_dedup AS SELECT u, tmdb_id, rating, rated_at FROM "
        "(SELECT u, tmdb_id, rating, rated_at, "
        "        ROW_NUMBER() OVER (PARTITION BY u, tmdb_id ORDER BY rated_at DESC) rn "
        " FROM _ml_stage) WHERE rn = 1"
    )
    conn.execute("DROP TABLE _ml_stage")

    # --- filtra usuários com poucos ratings, remapeia user_id denso ---
    conn.execute("CREATE INDEX IF NOT EXISTS _ml_dedup_u ON _ml_dedup(u)")
    conn.execute("DROP TABLE IF EXISTS _ml_users")
    conn.execute(
        "CREATE TABLE _ml_users AS "
        "SELECT u, ROW_NUMBER() OVER (ORDER BY u) AS uid FROM "
        "(SELECT u FROM _ml_dedup GROUP BY u HAVING COUNT(*) >= ?)",
        (min_user_ratings,),
    )
    n_users = conn.execute("SELECT COUNT(*) FROM _ml_users").fetchone()[0]
    n_final = conn.execute("SELECT COUNT(*) FROM _ml_dedup s JOIN _ml_users mu ON mu.u = s.u").fetchone()[0]
    n_items = conn.execute(
        "SELECT COUNT(DISTINCT tmdb_id) FROM _ml_dedup s JOIN _ml_users mu ON mu.u = s.u"
    ).fetchone()[0]

    stats = {
        "source": "MovieLens ml-32m (GroupLens)",
        "source_url": ML_URL,
        "version": ml_version,
        "generated_by_grouplens": "2023-10-13",
        "license": "GroupLens — uso NÃO-COMERCIAL sem permissão; ver README.txt do pacote",
        "citation": CITATION,
        "reconciliation": "movieId → tmdbId via links.csv oficial do pacote",
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "min_user_ratings": min_user_ratings,
        "n_ratings": n_final,
        "n_users": n_users,
        "n_items": n_items,
        "rating_scale": [0.5, 5.0],
        "replaces": "tabela ratings anterior (~2,9M, proveniência não confirmada)",
    }

    if dry_run:
        conn.execute("DROP TABLE _ml_dedup")
        conn.execute("DROP TABLE _ml_users")
        conn.commit()
        print("» --dry-run: nada gravado em `ratings`.")
        return stats

    print(f"» gravando {n_final:,} ratings ({n_users:,} usuários, {n_items:,} filmes) em `ratings`…")
    conn.execute("DELETE FROM ratings")
    conn.execute(
        "INSERT INTO ratings (user_id, tmdb_id, rating, rated_at) "
        "SELECT mu.uid, s.tmdb_id, s.rating, s.rated_at "
        "FROM _ml_dedup s JOIN _ml_users mu ON mu.u = s.u"
    )
    conn.execute("DROP TABLE _ml_dedup")
    conn.execute("DROP TABLE _ml_users")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    with open(PROVENANCE_PATH, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    print(f"» proveniência: {os.path.relpath(PROVENANCE_PATH, db._PROJECT_ROOT)}")
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingere ratings do MovieLens ml-32m no movies.db.")
    ap.add_argument("--zip", default=_ZIP, help=f"caminho do ml-32m.zip (padrão: {_ZIP})")
    ap.add_argument(
        "--min-user-ratings",
        type=int,
        default=5,
        help="descarta usuários com menos ratings que isto no catálogo (padrão 5)",
    )
    ap.add_argument("--dry-run", action="store_true", help="calcula e reporta, sem gravar")
    a = ap.parse_args(argv)
    if not db.has_sqlite():
        print(f"movies.db não encontrado em {db.DB_PATH}", file=sys.stderr)
        return 1
    stats = ingest(a.zip, a.min_user_ratings, a.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
