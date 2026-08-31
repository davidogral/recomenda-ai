"""Cliente TMDB — capas de pôster e resolução de títulos (Letterboxd).

Credenciais lidas de variáveis de ambiente (carregadas de `.env` se presente):
  - TMDB_API_TOKEN  → "API Read Access Token" (v4), enviado como `Bearer` (preferido).
  - TMDB_API_KEY    → chave (v3), usada como fallback via `?api_key=`.

Caches persistentes em `data/tmdb_cache/` (JSON), para nunca repetir uma chamada:
  - posters.json:  "tmdb_id" -> poster_path  ("" = filme sem pôster)
  - search.json:   "nome|ano" -> tmdb_id     (null = nada encontrado)
  - details.json:  "tmdb_id|lang" -> ficha completa do filme (TTL de 3 dias)

Tudo degrada com elegância: sem credenciais (ou sem rede), as funções devolvem
`None` e o resto do sistema cai para placeholder / casamento fuzzy.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import requests

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# .env (loader minimalista, sem dependência externa)
# --------------------------------------------------------------------------
def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # `setdefault`: variável de ambiente real tem prioridade sobre o .env.
                os.environ.setdefault(key, val)
    except OSError:
        pass


_load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

TMDB_API_TOKEN = os.environ.get("TMDB_API_TOKEN", "").strip()
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "").strip()
TMDB_LANG = os.environ.get("TMDB_LANG", "pt-BR").strip()
# Região (mercado) para disponibilidade de streaming — JustWatch via TMDB.
WATCH_REGION = (os.environ.get("TMDB_WATCH_REGION", "BR").strip() or "BR").upper()

_API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
LOGO_BASE = "https://image.tmdb.org/t/p/w92"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w780"
PROFILE_BASE = "https://image.tmdb.org/t/p/w185"


def is_configured() -> bool:
    """True se há ao menos uma credencial (token v4 ou chave v3)."""
    return bool(TMDB_API_TOKEN or TMDB_API_KEY)


# --------------------------------------------------------------------------
# Sessão HTTP (uma por processo, com keep-alive)
# --------------------------------------------------------------------------
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                s.headers.update({"accept": "application/json"})
                if TMDB_API_TOKEN:
                    s.headers.update({"Authorization": f"Bearer {TMDB_API_TOKEN}"})
                _session = s
    return _session


def _params(extra: Optional[dict] = None) -> dict:
    p = dict(extra or {})
    # Token v4 vai no header; só mandamos api_key (v3) se não houver token.
    if not TMDB_API_TOKEN and TMDB_API_KEY:
        p["api_key"] = TMDB_API_KEY
    return p


def _get(path: str, params: Optional[dict] = None, timeout: float = 10.0) -> Optional[dict]:
    if not is_configured():
        return None
    try:
        resp = _get_session().get(f"{_API_BASE}{path}", params=_params(params), timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except (requests.RequestException, ValueError):
        return None
    return None


# --------------------------------------------------------------------------
# Cache JSON persistente (thread-safe, escrita atômica)
# --------------------------------------------------------------------------
class _JsonCache:
    def __init__(self, path: str, flush_every: int = 30):
        self.path = path
        self.flush_every = flush_every
        self._lock = threading.Lock()
        self._dirty = 0
        self.data: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data[key] = value
            self._dirty += 1
            if self._dirty >= self.flush_every:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self._dirty == 0 and os.path.exists(self.path):
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._dirty = 0


_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "tmdb_cache")
_poster_cache = _JsonCache(os.path.join(_CACHE_DIR, "posters.json"))
_search_cache = _JsonCache(os.path.join(_CACHE_DIR, "search.json"))
# Disponibilidade de streaming muda com o tempo → cache com expiração (TTL).
_provider_cache = _JsonCache(os.path.join(_CACHE_DIR, "watch_providers.json"))
_provider_list_cache = _JsonCache(os.path.join(_CACHE_DIR, "provider_list.json"))
_PROVIDERS_TTL = 7 * 24 * 3600  # 7 dias
# Ficha do filme (sinopse, elenco, nota) — nota/votos mudam devagar → 3 dias.
_details_cache = _JsonCache(os.path.join(_CACHE_DIR, "details.json"))
_DETAILS_TTL = 3 * 24 * 3600
# IDs externos (imdb_id) — imutáveis, cache permanente. "" = filme sem imdb_id.
_extids_cache = _JsonCache(os.path.join(_CACHE_DIR, "external_ids.json"))


@atexit.register
def _flush_all() -> None:
    _poster_cache.flush()
    _search_cache.flush()
    _provider_cache.flush()
    _provider_list_cache.flush()
    _details_cache.flush()
    _extids_cache.flush()


# --------------------------------------------------------------------------
# Pôsteres
# --------------------------------------------------------------------------
def poster_path(tmdb_id: int) -> Optional[str]:
    """`poster_path` do filme (ex.: "/abc.jpg"), ou None. Cacheado em disco."""
    key = str(int(tmdb_id))
    if key in _poster_cache:
        return _poster_cache.get(key) or None
    data = _get(f"/movie/{int(tmdb_id)}", {"language": TMDB_LANG})
    pp = (data or {}).get("poster_path") or ""
    _poster_cache.set(key, pp)
    return pp or None


def poster_url(tmdb_id: int) -> Optional[str]:
    """URL completa do pôster (w342), ou None."""
    pp = poster_path(tmdb_id)
    return f"{IMAGE_BASE}{pp}" if pp else None


def prefetch_posters(ids: Iterable[int], max_workers: int = 16) -> None:
    """Aquece o cache de pôsteres de vários filmes em paralelo (apenas os ausentes)."""
    if not is_configured():
        return
    missing = []
    seen = set()
    for i in ids:
        try:
            tid = int(i)
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in seen or str(tid) in _poster_cache:
            continue
        seen.add(tid)
        missing.append(tid)
    if not missing:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(poster_path, missing))
    _poster_cache.flush()


# --------------------------------------------------------------------------
# IDs externos (imdb_id) — chave para cruzar com IMDb / Metacritic etc.
# --------------------------------------------------------------------------
def imdb_id(tmdb_id: int) -> Optional[str]:
    """imdb_id do filme (ex.: "tt0068646"), ou None. Cacheado em disco."""
    key = str(int(tmdb_id))
    if key in _extids_cache:
        return _extids_cache.get(key) or None
    data = _get(f"/movie/{int(tmdb_id)}/external_ids")
    val = (data or {}).get("imdb_id") or ""
    _extids_cache.set(key, val)
    return val or None


def prefetch_imdb_ids(ids: Iterable[int], max_workers: int = 16) -> None:
    """Aquece o cache de imdb_id de vários filmes em paralelo (só os ausentes)."""
    if not is_configured():
        return
    missing, seen = [], set()
    for i in ids:
        try:
            tid = int(i)
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in seen or str(tid) in _extids_cache:
            continue
        seen.add(tid)
        missing.append(tid)
    if not missing:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(imdb_id, missing))
    _extids_cache.flush()


# --------------------------------------------------------------------------
# Disponibilidade de streaming (watch providers — JustWatch via TMDB)
# --------------------------------------------------------------------------
def _fresh(entry) -> bool:
    """True se a entrada de cache (com TTL) ainda é válida."""
    return bool(entry) and (time.time() - float(entry.get("t", 0))) < _PROVIDERS_TTL


def _shape_provider(p: dict) -> Optional[dict]:
    pid = p.get("provider_id")
    if pid is None:
        return None
    logo = p.get("logo_path")
    return {
        "id": int(pid),
        "name": p.get("provider_name") or "",
        "logo": f"{LOGO_BASE}{logo}" if logo else None,
        "priority": int(p.get("display_priority") or 999),
    }


def movie_watch_providers(tmdb_id: int, region: Optional[str] = None) -> list[dict]:
    """Serviços de streaming por assinatura (flatrate/grátis/com anúncio) onde o
    filme está disponível na região. Lista de {id, name, logo, priority}, ordenada
    por prioridade. Cacheada em disco com TTL. Ignora aluguel/compra de propósito —
    o foco é "está incluso num serviço que eu assino". Vazia sem credencial/rede."""
    region = (region or WATCH_REGION).upper()
    key = f"{int(tmdb_id)}|{region}"
    cached = _provider_cache.get(key)
    if _fresh(cached):
        return cached.get("v") or []

    data = _get(f"/movie/{int(tmdb_id)}/watch/providers")
    region_data = ((data or {}).get("results") or {}).get(region) or {}
    seen: dict[int, dict] = {}
    for kind in ("flatrate", "free", "ads"):
        for p in region_data.get(kind) or []:
            shaped = _shape_provider(p)
            if shaped and shaped["id"] not in seen:
                seen[shaped["id"]] = shaped
    out = sorted(seen.values(), key=lambda x: x["priority"])
    if data is not None:  # só cacheia resposta real (não falha de rede)
        _provider_cache.set(key, {"v": out, "t": time.time()})
    return out


def movie_watch_providers_full(tmdb_id: int, region: Optional[str] = None) -> dict:
    """TODAS as formas de assistir o filme na região, agrupadas por tipo:
    {flatrate, free, ads, rent, buy} (listas de {id, name, logo, priority}) +
    `link` da página "onde assistir" da TMDB (dados JustWatch — o link credita
    a fonte). Diferente de `movie_watch_providers`, inclui aluguel e compra —
    é a visão da ficha do filme. Cacheada em disco com TTL."""
    region = (region or WATCH_REGION).upper()
    key = f"full|{int(tmdb_id)}|{region}"
    cached = _provider_cache.get(key)
    if _fresh(cached):
        return cached.get("v") or {}

    data = _get(f"/movie/{int(tmdb_id)}/watch/providers")
    region_data = ((data or {}).get("results") or {}).get(region) or {}
    out: dict = {"link": region_data.get("link")}
    for kind in ("flatrate", "free", "ads", "rent", "buy"):
        shaped = [s for s in (_shape_provider(p) for p in region_data.get(kind) or []) if s]
        shaped.sort(key=lambda x: x["priority"])
        out[kind] = shaped
    if data is not None:  # só cacheia resposta real (não falha de rede)
        _provider_cache.set(key, {"v": out, "t": time.time()})
    return out


def list_watch_providers(region: Optional[str] = None, limit: int = 32) -> list[dict]:
    """Serviços de streaming populares na região, para o usuário escolher os seus.
    Ordenados pela prioridade de exibição da TMDB (os mais usados primeiro)."""
    region = (region or WATCH_REGION).upper()
    key = f"list|{region}"
    cached = _provider_list_cache.get(key)
    if _fresh(cached):
        return (cached.get("v") or [])[:limit]

    data = _get("/watch/providers/movie", {"watch_region": region, "language": TMDB_LANG})
    out = [s for s in (_shape_provider(p) for p in (data or {}).get("results") or []) if s]
    out.sort(key=lambda x: x["priority"])
    if data is not None:
        _provider_list_cache.set(key, {"v": out, "t": time.time()})
    return out[:limit]


def prefetch_watch_providers(ids: Iterable[int], region: Optional[str] = None,
                             max_workers: int = 16) -> None:
    """Aquece o cache de provedores de vários filmes em paralelo (só os ausentes/expirados)."""
    if not is_configured():
        return
    region = (region or WATCH_REGION).upper()
    missing, seen = [], set()
    for i in ids:
        try:
            tid = int(i)
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in seen:
            continue
        seen.add(tid)
        if not _fresh(_provider_cache.get(f"{tid}|{region}")):
            missing.append(tid)
    if not missing:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(lambda t: movie_watch_providers(t, region), missing))
    _provider_cache.flush()


def filter_available(tmdb_ids: Iterable[int], provider_ids, region: Optional[str] = None,
                     limit: Optional[int] = None, scan_cap: Optional[int] = None) -> list[int]:
    """Mantém, **na ordem dada**, os filmes disponíveis em algum dos `provider_ids`
    (assinatura) na região, parando ao atingir `limit` — "os N melhores disponíveis".

    Limita a varredura aos `scan_cap` candidatos do topo do ranking e busca os
    provedores numa **única rajada paralela** (rápido na 1ª vez; instantâneo depois,
    via cache). Sem `provider_ids`, devolve a lista como veio."""
    ids = [int(t) for t in tmdb_ids]
    pset = {int(x) for x in (provider_ids or [])}
    if not pset:
        return ids[:limit] if limit else ids
    region = (region or WATCH_REGION).upper()

    cap = scan_cap if scan_cap is not None else max((limit or 12) * 6, 72)
    scan = ids[:cap]
    prefetch_watch_providers(scan, region, max_workers=32)  # uma rajada paralela
    out: list[int] = []
    for tid in scan:
        if any(p["id"] in pset for p in movie_watch_providers(tid, region)):
            out.append(tid)
            if limit and len(out) >= limit:
                break
    return out


# --------------------------------------------------------------------------
# Ficha do filme (detalhes completos, ao vivo da TMDB)
# --------------------------------------------------------------------------
def movie_details(tmdb_id: int) -> Optional[dict]:
    """Ficha completa do filme, ao vivo da TMDB (cache em disco, TTL 3 dias).

    Uma chamada com `append_to_response=credits` traz tudo: sinopse (no idioma
    TMDB_LANG, com fallback para en-US quando a tradução não existe), duração,
    gêneros, nota, direção, elenco (top 12, com foto), franquia (collection),
    pôster e backdrop. Se a rede cair, serve o cache mesmo vencido; devolve
    None só sem credencial ou se o filme não existe na TMDB."""
    key = f"{int(tmdb_id)}|{TMDB_LANG}"
    cached = _details_cache.get(key)
    if cached and (time.time() - float(cached.get("t", 0))) < _DETAILS_TTL:
        return cached.get("v")

    data = _get(f"/movie/{int(tmdb_id)}",
                {"language": TMDB_LANG, "append_to_response": "credits"})
    if data is None:
        return (cached or {}).get("v")  # rede caiu → melhor a ficha velha que nada

    overview = data.get("overview") or ""
    if not overview and TMDB_LANG.lower() != "en-us":
        en = _get(f"/movie/{int(tmdb_id)}", {"language": "en-US"})
        overview = (en or {}).get("overview") or ""

    credits = data.get("credits") or {}
    directors = [c.get("name") for c in credits.get("crew") or []
                 if c.get("job") == "Director" and c.get("name")]
    cast = [{
        "name": c.get("name") or "",
        "character": c.get("character") or "",
        "photo": f"{PROFILE_BASE}{c['profile_path']}" if c.get("profile_path") else None,
    } for c in (credits.get("cast") or [])[:12]]

    release_date = data.get("release_date") or ""
    collection = data.get("belongs_to_collection") or None
    v = {
        "tmdb_id": int(tmdb_id),
        "title": data.get("title") or data.get("original_title") or "",
        "original_title": data.get("original_title") or "",
        "tagline": data.get("tagline") or "",
        "overview": overview,
        "release_date": release_date,
        "release_year": int(release_date[:4]) if release_date[:4].isdigit() else None,
        "runtime": data.get("runtime") or None,
        "genres": [g["name"] for g in data.get("genres") or [] if g.get("name")],
        "vote_average": data.get("vote_average") or 0,
        "vote_count": data.get("vote_count") or 0,
        "original_language": data.get("original_language") or "",
        "poster": f"{IMAGE_BASE}{data['poster_path']}" if data.get("poster_path") else None,
        "backdrop": f"{BACKDROP_BASE}{data['backdrop_path']}" if data.get("backdrop_path") else None,
        "imdb_id": data.get("imdb_id") or None,
        "collection": ({"id": collection.get("id"), "name": collection.get("name") or ""}
                       if collection else None),
        "directors": directors,
        "cast": cast,
    }
    _details_cache.set(key, {"v": v, "t": time.time()})
    return v


def collection_movies(collection_id: int) -> Optional[dict]:
    """Filmes de uma franquia (collection da TMDB), em ordem de lançamento.

    {id, name, parts: [{tmdb_id, title, release_year, poster}]} — inclui filmes
    anunciados/futuros (sem data vão para o fim). Cache TTL de 3 dias; se a
    rede cair, serve o cache mesmo vencido."""
    key = f"coll|{int(collection_id)}|{TMDB_LANG}"
    cached = _details_cache.get(key)
    if cached and (time.time() - float(cached.get("t", 0))) < _DETAILS_TTL:
        return cached.get("v")

    data = _get(f"/collection/{int(collection_id)}", {"language": TMDB_LANG})
    if data is None:
        return (cached or {}).get("v")

    parts = []
    raw = sorted(data.get("parts") or [], key=lambda p: p.get("release_date") or "9999-99")
    for p in raw:
        if not p.get("id"):
            continue
        rd = p.get("release_date") or ""
        parts.append({
            "tmdb_id": int(p["id"]),
            "title": p.get("title") or p.get("original_title") or "",
            "release_year": int(rd[:4]) if rd[:4].isdigit() else None,
            "poster": f"{IMAGE_BASE}{p['poster_path']}" if p.get("poster_path") else None,
        })
    v = {"id": int(collection_id), "name": data.get("name") or "", "parts": parts}
    _details_cache.set(key, {"v": v, "t": time.time()})
    return v


# --------------------------------------------------------------------------
# Busca / resolução de título → tmdb_id (para o import do Letterboxd)
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _pick_best(results: list, name: str, year: Optional[int]) -> Optional[int]:
    """Escolhe o melhor candidato do `/search/movie`.

    Com filtro de ano o TMDB **não** ordena por popularidade, então `results[0]`
    erra (ex.: "Parasite" 2019 traz um curta obscuro antes do filme real). Ranqueia
    por: título exato → ano exato → popularidade → nº de votos.
    """
    if not results:
        return None
    nq = _norm(name)

    def score(r: dict) -> tuple:
        exact = 1 if nq in (_norm(r.get("title")), _norm(r.get("original_title"))) else 0
        ry = (r.get("release_date") or "")[:4]
        year_ok = 1 if (year and ry == str(year)) else 0
        pop = float(r.get("popularity") or 0.0)
        votes = int(r.get("vote_count") or 0)
        return (exact, year_ok, pop, votes)

    best = max(results, key=score)
    return int(best["id"])


def search_movie_id(name: str, year: Optional[int] = None) -> Optional[int]:
    """Resolve (nome, ano) para um tmdb_id via `/search/movie`. Cacheado.

    Sem `language` na busca: o `title` volta no idioma original (o `Name` do
    Letterboxd é em inglês), o que ajuda o casamento exato de título.
    """
    name = (name or "").strip()
    if not name:
        return None
    key = f"{_norm(name)}|{year or ''}"
    if key in _search_cache:
        v = _search_cache.get(key)
        return int(v) if v is not None else None
    params = {"query": name, "include_adult": "false"}
    if year:
        params["year"] = int(year)
    data = _get("/search/movie", params)
    if data is None:
        return None  # erro de rede/HTTP: não cacheia None (senão envenena o cache)
    tid = _pick_best(data.get("results") or [], name, year)
    _search_cache.set(key, tid)  # None aqui = "de fato não achou" → cacheável
    return tid


def search_movies(query: str, limit: int = 12) -> list[dict]:
    """Candidatos do `/search/movie` para uma consulta livre de título.

    Multilíngue e tolerante a digitação: resolve títulos em qualquer idioma
    (ex.: "The Godfather") e erros leves. Cada item: {id, title, original_title,
    year}. Ordenado pela relevância da TMDB. Cacheado em disco (sem `language`,
    para o `title` voltar no idioma original e ajudar o casamento)."""
    query = (query or "").strip()
    if not query:
        return []
    key = f"q::{_norm(query)}"
    if key in _search_cache:
        cached = _search_cache.get(key)
        return list(cached or [])[:limit]
    data = _get("/search/movie", {"query": query, "include_adult": "false"})
    results = (data or {}).get("results") or []
    out = []
    for r in results[:20]:
        if not r.get("id"):
            continue
        out.append({
            "id": int(r["id"]),
            "title": r.get("title") or "",
            "original_title": r.get("original_title") or "",
            "year": (r.get("release_date") or "")[:4] or None,
        })
    _search_cache.set(key, out)
    return out[:limit]


def prefetch_search(items: Iterable[tuple], max_workers: int = 16) -> None:
    """Aquece o cache de busca de vários (nome, ano) em paralelo (só os ausentes)."""
    if not is_configured():
        return
    todo = []
    seen = set()
    for name, year in items:
        key = f"{_norm(name)}|{year or ''}"
        if key in seen or key in _search_cache:
            continue
        seen.add(key)
        todo.append((name, year))
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(lambda t: search_movie_id(t[0], t[1]), todo))
    _search_cache.flush()
