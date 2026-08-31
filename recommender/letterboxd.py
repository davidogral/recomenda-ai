"""Importação do perfil Letterboxd: `ratings.csv` → [(tmdb_id, rating)].

O export oficial do Letterboxd (Settings → Import & Export) traz um `ratings.csv`
com as colunas: `Date, Name, Year, Letterboxd URI, Rating` (Name em inglês/original;
Rating 0.5–5.0 — mesma escala do nosso modelo, sem reescalar).

Resolução de `tmdb_id`:
  - `resolver="tmdb"`: resolve via TMDB `/search/movie` (nome+ano) → tmdb_id e
    confere se esse id está no catálogo. Preciso mesmo com títulos PT no catálogo,
    porque o id do TMDB é o mesmo nas duas bases. Filmes que o TMDB não resolver
    caem para o fuzzy. Requer credencial TMDB (`.env`).
  - `resolver="fuzzy"`: casa por **título + ano** direto no catálogo com rapidfuzz.
    ⚠️ Limitação: títulos do catálogo em PT e `Name` do Letterboxd em inglês fazem
    muitos filmes não casarem. Fallback quando não há credencial TMDB.
  - `resolver="auto"` (default): usa TMDB se houver credencial, senão fuzzy.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Optional, Union

from core import catalog

# Limiar de similaridade de título para aceitar um casamento fuzzy.
DEFAULT_TITLE_CUTOFF = 82.0


@dataclass
class ImportResult:
    matched: list[tuple[int, float]] = field(default_factory=list)  # [(tmdb_id, rating)]
    unmatched: list[dict] = field(default_factory=list)             # linhas não resolvidas
    total_rows: int = 0
    # Detalhe por filme casado: {tmdb_id, rating, name, year, review} — alimenta o
    # perfil de gosto (embeddings + resumo). `matched` continua sendo o par básico.
    matched_detail: list[dict] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return len(self.matched) / self.total_rows if self.total_rows else 0.0


def _read_csv(source: Union[str, io.IOBase]) -> list[dict]:
    """Lê o ratings.csv de um caminho ou file-like. Tolera colunas ausentes."""
    if hasattr(source, "read"):
        text = source.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    else:
        f = open(source, "r", encoding="utf-8-sig", newline="")
        reader = csv.DictReader(f)
    rows = [dict(r) for r in reader]
    if hasattr(source, "read") is False:
        f.close()
    return rows


def _parse_rows(rows: list[dict]) -> list[dict]:
    """Normaliza para {name, year, rating} a partir das colunas do Letterboxd."""
    parsed = []
    for r in rows:
        name = (r.get("Name") or "").strip()
        if not name:
            continue
        try:
            rating = float(r.get("Rating") or "")
        except ValueError:
            continue  # filmes sem nota não servem para o colaborativo
        year_raw = (r.get("Year") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        review = (r.get("Review") or "").strip()
        parsed.append({"name": name, "year": year, "rating": rating, "review": review})
    return parsed


class _FuzzyResolver:
    """Casa (título, ano) contra o catálogo com rapidfuzz."""

    def __init__(self, title_cutoff: float = DEFAULT_TITLE_CUTOFF):
        from rapidfuzz import fuzz, process

        self._fuzz = fuzz
        self._process = process
        self.title_cutoff = title_cutoff

        self._catalog = catalog.get_catalog()
        # Listas paralelas para o rapidfuzz; índice por ano para restringir o espaço.
        self._ids: list[int] = []
        self._titles: list[str] = []
        self._by_year: dict[int, list[int]] = {}  # year -> índices em self._ids
        for tid, mv in self._catalog.items():
            i = len(self._ids)
            self._ids.append(tid)
            self._titles.append(mv.get("title") or "")
            y = mv.get("release_year")
            if y is not None:
                self._by_year.setdefault(int(y), []).append(i)

    def _candidate_indices(self, year: Optional[int]) -> list[int]:
        if year is None:
            # Sem ano: o título sozinho é fraco; só busca em todo o catálogo.
            return list(range(len(self._ids)))
        # Ano exato ±1 (lançamentos divergem 1 ano entre bases). Se nada existe
        # nessa janela, o filme não está no catálogo -> sem match (evita falso
        # positivo de cair para todos os anos).
        idx: list[int] = []
        for y in (year, year - 1, year + 1):
            idx.extend(self._by_year.get(y, []))
        return idx

    def resolve(self, name: str, year: Optional[int]) -> Optional[tuple[int, float]]:
        cand_idx = self._candidate_indices(year)
        choices = {i: self._titles[i] for i in cand_idx}
        best = self._process.extractOne(
            name, choices, scorer=self._fuzz.WRatio, score_cutoff=self.title_cutoff
        )
        if best is None:
            return None
        _title, score, idx = best
        return self._ids[idx], score


def _record_match(result: ImportResult, tmdb_id: int, row: dict) -> None:
    """Registra um casamento em `matched` (par básico) e `matched_detail` (rico)."""
    result.matched.append((int(tmdb_id), row["rating"]))
    result.matched_detail.append({
        "tmdb_id": int(tmdb_id), "rating": float(row["rating"]),
        "name": row.get("name"), "year": row.get("year"),
        "review": row.get("review", ""),
    })


def _resolve_via_fuzzy(parsed: list[dict], title_cutoff: float) -> ImportResult:
    """Casa cada linha por título+ano contra o catálogo (rapidfuzz)."""
    result = ImportResult(total_rows=len(parsed))
    fr = _FuzzyResolver(title_cutoff=title_cutoff)
    for row in parsed:
        hit = fr.resolve(row["name"], row["year"])
        if hit is None:
            result.unmatched.append(row)
        else:
            _record_match(result, hit[0], row)
    return result


def _resolve_via_tmdb(parsed: list[dict], title_cutoff: float,
                      fallback_fuzzy: bool = True) -> ImportResult:
    """Resolve via TMDB `/search/movie`; cai para fuzzy no que o TMDB não pegar.

    Só conta como match quando o tmdb_id resolvido existe no catálogo (é um filme
    que o sistema conhece e pode usar na recomendação).
    """
    from core import tmdb

    if not tmdb.is_configured():
        if fallback_fuzzy:
            return _resolve_via_fuzzy(parsed, title_cutoff)
        raise RuntimeError(
            "Credencial TMDB ausente. Defina TMDB_API_TOKEN/TMDB_API_KEY no .env "
            "ou use resolver='fuzzy'."
        )

    cat = catalog.get_catalog()
    tmdb.prefetch_search([(r["name"], r["year"]) for r in parsed])

    result = ImportResult(total_rows=len(parsed))
    fuzzy: Optional[_FuzzyResolver] = None
    for row in parsed:
        tid = tmdb.search_movie_id(row["name"], row["year"])
        if tid is not None and tid in cat:
            _record_match(result, tid, row)
            continue
        if fallback_fuzzy:
            if fuzzy is None:
                fuzzy = _FuzzyResolver(title_cutoff=title_cutoff)
            hit = fuzzy.resolve(row["name"], row["year"])
            if hit is not None and hit[0] in cat:
                _record_match(result, hit[0], row)
                continue
        result.unmatched.append(row)
    return result


def import_ratings(
    source: Union[str, io.IOBase],
    resolver: str = "auto",
    title_cutoff: float = DEFAULT_TITLE_CUTOFF,
    tmdb_api_key: Optional[str] = None,  # mantido por compatibilidade; credencial vem do .env
) -> ImportResult:
    """Lê o `ratings.csv` do Letterboxd e devolve [(tmdb_id, rating)] resolvidos.

    `resolver`: 'auto' (TMDB se houver credencial, senão fuzzy), 'tmdb' ou 'fuzzy'.
    """
    parsed = _parse_rows(_read_csv(source))

    if resolver == "auto":
        from core import tmdb
        resolver = "tmdb" if tmdb.is_configured() else "fuzzy"

    if resolver == "tmdb":
        return _resolve_via_tmdb(parsed, title_cutoff)
    if resolver == "fuzzy":
        return _resolve_via_fuzzy(parsed, title_cutoff)
    raise ValueError(f"resolver desconhecido: {resolver!r}")
