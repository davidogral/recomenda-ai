"""RecomendAI — API Flask.

Três rotas principais:
  - POST /search       → busca textual (sinopse + nome + filtros) — motor superior
  - POST /submit_ratings → recomendar a partir de filmes favoritos (qualquer quantidade)
  - POST /recommend    → recomendação via upload ratings.csv do Letterboxd

O motor de busca (índice + modelo de embeddings) é **pré-carregado no boot**
(ver o bloco de warmup no fim deste arquivo); os fatores do recomendador
carregam sob demanda no 1º uso da rota.
"""

from __future__ import annotations

import gzip
import os
import secrets
import time

from flask import Flask, g, jsonify, render_template, request
from flask_login import current_user, login_required

from core import catalog, events, metrics, posters, security


def _uid_or_none():
    """user_id se logado, senão None — para os registros de uso (`core.events`)."""
    try:
        return current_user.id if current_user.is_authenticated else None
    except Exception:
        return None


def _sid():
    """Identificador de sessão (cookie `sid`, sem dado pessoal) — encadeia as
    ações de uma visita para o funil/analytics. Gerado sob demanda."""
    s = getattr(g, "_sid", None)
    if s:
        return s
    s = request.cookies.get("sid")
    if not s or not (8 <= len(s) <= 64) or not s.replace("-", "").replace("_", "").isalnum():
        s = secrets.token_urlsafe(16)
        g._sid_new = True
    g._sid = s
    return s


app = Flask(__name__, static_folder="frontend", template_folder="frontend")

# --- Atrás do proxy (Caddy termina o TLS) em produção: confiar em UM hop de
#     X-Forwarded-* para o Flask enxergar https/host reais — sem isso os links
#     de verificação/reset saem com esquema http. ---
if security.is_production():
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# --- Observabilidade: contadores/latência Prometheus + rota /metrics ---
metrics.init_flask(app)

# --- Rate limiting (Flask-Limiter). storage em memória: vale por processo;
#     para multi-worker/multi-instância, aponte RATELIMIT_STORAGE_URI p/ Redis. ---
RATE_SEARCH = os.environ.get("RECOMENDAI_RATE_SEARCH", "30 per minute")
RATE_HEAVY = os.environ.get("RECOMENDAI_RATE_HEAVY", "12 per minute")
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        headers_enabled=True,
    )
except Exception:  # flask-limiter ausente → decorador vira no-op

    class _NoLimiter:
        def limit(self, *_a, **_k):
            return lambda f: f

    limiter = _NoLimiter()

# --- Autenticação: sessão, CSRF, rotas /auth/* (cadastro, login, reset…) ---
from core import auth_routes  # noqa: E402

auth_routes.init_auth(app, limiter)


def _parse_providers(raw) -> "list[int] | None":
    """Normaliza `providers` (string "8,119" ou lista) numa lista de IDs, ou None."""
    if raw is None:
        return None
    parts = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    ids: list[int] = []
    for x in parts:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    return ids or None


# =========================================================================
# ROTA PRINCIPAL
# =========================================================================


@app.route("/")
def home_page():
    return render_template("index.html")


@app.route("/privacidade")
def privacy_page():
    return render_template("privacidade.html")


# =========================================================================
# ROTAS DE BUSCA (motor superior — davidogral)
# =========================================================================


@app.route("/genres")
def genres():
    """Lista de gêneros (para o filtro da busca)."""
    names = sorted(catalog.get_genre_names().values())
    return jsonify(names)


@app.route("/search", methods=["POST"])
@limiter.limit(RATE_SEARCH)
def search():
    """Busca facetada. Body JSON: {query, director, actor, n, year_min,
    year_max, genre, language}. Diretor/ator restringem; query ranqueia."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    director = (data.get("director") or "").strip()
    actor = (data.get("actor") or "").strip()
    if not query and not director and not actor:
        return jsonify({"error": "Informe um termo, um diretor ou um ator."}), 400

    try:
        n = max(1, min(int(data.get("n", 12)), 50))
    except (TypeError, ValueError):
        n = 12

    filters = {}
    for key in ("year_min", "year_max"):
        val = data.get(key)
        if val not in (None, ""):
            try:
                filters[key] = int(val)
            except (TypeError, ValueError):
                pass
    if data.get("genre"):
        filters["genre"] = data["genre"]
    if data.get("language"):
        filters["language"] = data["language"]

    _t0 = time.perf_counter()
    try:
        from core import inference_client

        with metrics.capture_stages() as _stages:
            results = inference_client.search_combined(
                query=query, director=director, actor=actor, n=n, filters=filters or None
            )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _f = dict(filters)
    if director:
        _f["director"] = 1
    if actor:
        _f["actor"] = 1
    events.log(
        "search",
        query=query,
        filters=_f or None,
        n_results=len(results),
        latency_ms=(time.perf_counter() - _t0) * 1000,
        stage_ms=_stages or None,
        user_id=_uid_or_none(),
        sid=_sid(),
    )

    return jsonify(
        {
            "query": query,
            "director": director,
            "actor": actor,
            "count": len(results),
            "results": posters.attach(results),
        }
    )


@app.route("/people")
def people():
    """Autocomplete de diretor/ator. Query params: q, role (actor|director)."""
    q = request.args.get("q", "")
    role = request.args.get("role") if request.args.get("role") in ("actor", "director") else None
    from retrieval.search_engine import suggest_people

    return jsonify(suggest_people(q, role=role, limit=10))


# =========================================================================
# ROTA: CATÁLOGO (para autocomplete de filmes — compatibilidade)
# =========================================================================


@app.route("/get_movies")
def get_movies():
    """Retorna todos os filmes do catálogo para autocomplete no frontend."""
    try:
        cat = catalog.get_catalog()
        from core.catalog import get_movie_genres

        movie_list = [
            {
                "movie_id": tid,
                "title": mv.get("title", ""),
                "poster_path": "",
                "genres": get_movie_genres(tid),
            }
            for tid, mv in cat.items()
        ]
        movie_list.sort(key=lambda x: x["title"])
        return jsonify(movie_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/popular")
def popular():
    """Filmes mais famosos (por nº de votos) para a seleção visual — com pôster.

    Fama ≈ vote_count (quantas pessoas avaliaram); filtramos por vote_average>=6.5
    para não trazer famoso-porém-ruim. Paginação simples via `offset`."""
    try:
        n = max(1, min(int(request.args.get("n", 48)), 120))
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        n, offset = 48, 0
    cat = catalog.get_catalog()
    ranked = sorted(
        (m for m in cat.values() if (m.get("vote_average") or 0) >= 6.5),
        key=lambda m: m.get("vote_count") or 0,
        reverse=True,
    )[offset : offset + n]
    items = [{"tmdb_id": m["tmdb_id"], "title": m.get("title"), "release_year": m.get("release_year")} for m in ranked]
    return jsonify(posters.attach(items))


# =========================================================================
# ROTAS: STREAMING (onde assistir — watch providers da TMDB/JustWatch)
# =========================================================================


@app.route("/providers")
def providers():
    """Serviços de streaming populares na região (para o usuário escolher os seus).
    Query param: region (default TMDB_WATCH_REGION, ex.: BR). Vazio se sem TMDB."""
    from core import tmdb

    region = (request.args.get("region") or "").strip() or None
    return jsonify(
        {
            "region": (region or tmdb.WATCH_REGION).upper(),
            "providers": tmdb.list_watch_providers(region),
        }
    )


@app.route("/watch_providers", methods=["POST"])
def watch_providers():
    """Provedores de streaming de um lote de filmes (para decorar/filtrar os cards).
    Body JSON: {movie_ids: [...], region?}. Resposta: {region, providers: {id: [...]}}."""
    data = request.get_json(silent=True) or {}
    region = (data.get("region") or "").strip() or None
    ids: list[int] = []
    for x in data.get("movie_ids") or []:
        try:
            if int(x) > 0:
                ids.append(int(x))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))[:60]  # dedup + teto por requisição

    from core import tmdb

    tmdb.prefetch_watch_providers(ids, region)
    return jsonify(
        {
            "region": (region or tmdb.WATCH_REGION).upper(),
            "providers": {str(tid): tmdb.movie_watch_providers(tid, region) for tid in ids},
        }
    )


# =========================================================================
# ROTA: FICHA DO FILME (sinopse, elenco e TODAS as formas de assistir)
# =========================================================================


@app.route("/movie/<int:tmdb_id>")
def movie_sheet(tmdb_id: int):
    """Ficha completa do filme: sinopse, direção/elenco, nota, franquia e TODAS
    as formas de assistir na região (assinatura, grátis, com anúncios, aluguel
    e compra). Dados ao vivo da TMDB (cache 3 dias); sem credencial/rede,
    degrada para o catálogo local. Query param: region."""
    from core import tmdb

    # registro de uso: abriu a ficha (ref = de onde veio; q/pos = a busca que levou até aqui)
    _ref = (request.args.get("ref") or "direct").strip()[:20]
    try:
        _pos = int(request.args.get("pos")) if request.args.get("pos") else None
    except (TypeError, ValueError):
        _pos = None
    events.log(
        "open",
        query=(request.args.get("q") or "")[:300],
        ref=_ref,
        pos=_pos,
        item_id=tmdb_id,
        user_id=_uid_or_none(),
        sid=_sid(),
    )

    region = (request.args.get("region") or "").strip() or None
    details = tmdb.movie_details(tmdb_id)

    if details is None:  # sem TMDB → monta a ficha com o que há no catálogo
        mv = catalog.get_movie(tmdb_id)
        if mv is None:
            return jsonify({"error": "Filme não encontrado."}), 404
        directors: list[str] = []
        cast: list[dict] = []
        try:
            people = catalog.get_movie_people(tmdb_id)
            directors = [p["name"] for p in people if p["role"] == "director"]
            cast = [
                {"name": p["name"], "character": p.get("character") or "", "photo": None}
                for p in people
                if p["role"] == "actor"
            ][:12]
        except RuntimeError:
            pass  # modo JSON (sem SQLite) → ficha sem pessoas
        details = {
            "tmdb_id": tmdb_id,
            "title": mv.get("title") or "",
            "original_title": "",
            "tagline": "",
            "overview": mv.get("overview") or "",
            "release_date": mv.get("release_date") or "",
            "release_year": mv.get("release_year"),
            "runtime": mv.get("runtime_minutes"),
            "genres": catalog.get_movie_genres(tmdb_id),
            "vote_average": mv.get("vote_average") or 0,
            "vote_count": mv.get("vote_count") or 0,
            "original_language": mv.get("original_language") or "",
            "poster": None,
            "backdrop": None,
            "imdb_id": None,
            "collection": None,
            "directors": directors,
            "cast": cast,
        }
    if not details.get("poster"):
        details["poster"] = posters.get_poster(tmdb_id, details.get("title"))

    from core import user_data

    my_rating = user_data.get_rating(current_user.id, tmdb_id) if current_user.is_authenticated else None
    return jsonify(
        {
            "region": (region or tmdb.WATCH_REGION).upper(),
            "details": details,
            "providers": tmdb.movie_watch_providers_full(tmdb_id, region),
            "my_rating": my_rating,
            "versions": user_data.list_versions(tmdb_id),
        }
    )


# =========================================================================
# ROTAS: VERSÕES DOS FILMES (cortes existentes + qual é a melhor — curadoria)
# =========================================================================


@app.route("/versions/<int:tmdb_id>")
def versions_list(tmdb_id: int):
    """Versões registradas de um filme (Director's Cut, Final Cut...)."""
    from core import user_data

    return jsonify({"versions": user_data.list_versions(tmdb_id)})


@app.route("/versions", methods=["POST"])
@login_required
@auth_routes.verified_required
def versions_save():
    """Cria (sem version_id) ou edita (com version_id) uma versão. Body JSON:
    {tmdb_id, name, runtime?, notes?, is_best?, version_id?}. Marcar is_best
    desmarca as demais versões do filme."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    try:
        tid = int(data.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    name = str(data.get("name") or "").strip()[:200]
    if tid <= 0 or not name:
        return jsonify({"error": "Informe tmdb_id e o nome da versão."}), 400

    runtime = data.get("runtime")
    try:
        runtime = int(runtime) if runtime not in (None, "") else None
        if runtime is not None and not (1 <= runtime <= 1000):
            return jsonify({"error": "Duração deve ser entre 1 e 1000 minutos."}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Duração inválida."}), 400

    version_id = data.get("version_id")
    try:
        version_id = int(version_id) if version_id not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "version_id inválido."}), 400

    saved = user_data.save_version(
        tid,
        name,
        runtime=runtime,
        notes=str(data.get("notes") or "").strip()[:5000],
        is_best=bool(data.get("is_best")),
        version_id=version_id,
    )
    if saved is None:
        return jsonify({"error": "Versão não encontrada para editar."}), 404
    return jsonify({"message": "Versão salva!", "version": saved})


@app.route("/versions/<int:version_id>", methods=["DELETE"])
@login_required
def versions_delete(version_id: int):
    """Remove uma versão registrada."""
    from core import user_data

    if not user_data.delete_version(version_id):
        return jsonify({"error": "Versão não encontrada."}), 404
    return jsonify({"message": "Versão removida."})


# =========================================================================
# ROTAS: MINHAS AVALIAÇÕES (diário estilo Letterboxd — por usuário)
# =========================================================================


@app.route("/ratings")
@login_required
def ratings_list():
    """Meu diário: todas as avaliações (nota, ❤, resenha, data), recentes primeiro."""
    from core import user_data

    rows = user_data.list_ratings(current_user.id)
    return jsonify({"count": len(rows), "ratings": rows})


@app.route("/ratings", methods=["POST"])
@login_required
@auth_routes.verified_required
def ratings_save():
    """Grava/substitui a avaliação de um filme. Body JSON: {tmdb_id, rating?,
    liked?, review?, watched_date?, title?, release_year?, poster?}. O frontend
    manda o estado completo do formulário da ficha."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    try:
        tid = int(data.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return jsonify({"error": "tmdb_id inválido."}), 400

    try:
        rating = user_data.normalize_rating(data.get("rating"))
        watched = user_data.normalize_date(data.get("watched_date"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Título/ano do request (a ficha sempre tem), com o catálogo local de reserva.
    mv = catalog.get_movie(tid) or {}
    title = (data.get("title") or mv.get("title") or "").strip()
    year = data.get("release_year") or mv.get("release_year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    poster = (data.get("poster") or "").strip()
    if not poster or poster.startswith("https://placehold.co"):
        poster = None  # placeholder não é pôster — o diário regenera na exibição

    saved = user_data.upsert_rating(
        current_user.id,
        tid,
        rating=rating,
        liked=bool(data.get("liked")),
        review=str(data.get("review") or "").strip()[:10000],
        watched_date=watched,
        title=title,
        release_year=year,
        poster=poster,
    )
    return jsonify({"message": "Avaliação salva!", "rating": saved})


@app.route("/ratings/<int:tmdb_id>", methods=["DELETE"])
@login_required
def ratings_delete(tmdb_id: int):
    """Remove a avaliação de um filme do diário."""
    from core import user_data

    if not user_data.delete_rating(current_user.id, tmdb_id):
        return jsonify({"error": "Este filme não está no seu diário."}), 404
    return jsonify({"message": "Avaliação removida."})


# =========================================================================
# ROTAS: ESSENCIAIS (por gênero, estilo e diretor)
# =========================================================================


@app.route("/explore/options")
def explore_options():
    """Opções de navegação dos essenciais: gêneros, estilos curados e
    diretores famosos do catálogo (requer SQLite)."""
    from core import explore

    try:
        return jsonify(
            {
                "genres": explore.genre_options(),
                "styles": explore.style_options(),
                "directors": explore.director_options(),
            }
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503


@app.route("/explore/essentials")
@limiter.limit(RATE_HEAVY)
def explore_essentials():
    """Essenciais de um grupo — exatamente um de: genre, style, director.
    Query params: n (1–60), region, providers (filtro de streaming opcional).
    Ranking por nota ponderada (aclamação × relevância)."""
    from core import explore, tmdb

    genre = (request.args.get("genre") or "").strip() or None
    style = (request.args.get("style") or "").strip() or None
    director = (request.args.get("director") or "").strip() or None
    if sum(bool(x) for x in (genre, style, director)) != 1:
        return jsonify({"error": "Informe exatamente um de: genre, style, director."}), 400

    try:
        n = max(1, min(int(request.args.get("n", 30)), 120))
    except (TypeError, ValueError):
        n = 30

    try:
        ranked = explore.essentials(genre=genre, style=style, director=director)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    if not ranked:
        return jsonify({"error": "Nada encontrado para esse grupo."}), 404

    # Filtro de streaming opcional: mantém os N melhores disponíveis.
    region = (request.args.get("region") or "").strip() or None
    provider_ids = _parse_providers(request.args.get("providers"))
    if provider_ids:
        keep = set(tmdb.filter_available([r["tmdb_id"] for r in ranked], provider_ids, region, limit=n))
        ranked = [r for r in ranked if r["tmdb_id"] in keep]
    results = ranked[:n]
    for i, r in enumerate(results, start=1):
        r["rank"] = i

    events.log(
        "essentials",
        query=(genre or director or style or ""),
        filters={"by": "genre" if genre else "director" if director else "style", "providers": bool(provider_ids)},
        n_results=len(results),
        user_id=_uid_or_none(),
        sid=_sid(),
    )

    return jsonify(
        {
            "label": genre or director or style,
            "count": len(results),
            "results": posters.attach(results),
        }
    )


# =========================================================================
# ROTAS: MINHAS LISTAS (ordem de assistir)
# =========================================================================


@app.route("/lists")
@login_required
def lists_index():
    """Todas as minhas listas (com contagem e pôsteres para a colagem)."""
    from core import user_data

    return jsonify({"lists": user_data.get_lists(current_user.id)})


@app.route("/lists", methods=["POST"])
@login_required
@auth_routes.verified_required
def lists_create():
    """Cria uma lista. Body JSON: {name, description?}."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:200]
    if not name:
        return jsonify({"error": "Dê um nome para a lista."}), 400
    lst = user_data.create_list(current_user.id, name, str(data.get("description") or "")[:2000])
    return jsonify({"message": "Lista criada!", "list": lst})


@app.route("/lists/<int:list_id>")
@login_required
def lists_get(list_id: int):
    """Uma lista com os itens na ordem de assistir."""
    from core import user_data

    lst = user_data.get_list(current_user.id, list_id)
    if lst is None:
        return jsonify({"error": "Lista não encontrada."}), 404
    return jsonify(lst)


@app.route("/lists/<int:list_id>", methods=["DELETE"])
@login_required
def lists_delete(list_id: int):
    """Apaga a lista inteira."""
    from core import user_data

    if not user_data.delete_list(current_user.id, list_id):
        return jsonify({"error": "Lista não encontrada."}), 404
    return jsonify({"message": "Lista apagada."})


@app.route("/lists/<int:list_id>/items", methods=["POST"])
@login_required
@auth_routes.verified_required
def lists_add_item(list_id: int):
    """Acrescenta um filme ao fim da lista. Body: {tmdb_id, title?,
    release_year?, poster?} (a ficha manda tudo; catálogo local de reserva)."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    try:
        tid = int(data.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return jsonify({"error": "tmdb_id inválido."}), 400

    mv = catalog.get_movie(tid) or {}
    title = (data.get("title") or mv.get("title") or "").strip()
    year = data.get("release_year") or mv.get("release_year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    poster = (data.get("poster") or "").strip()
    if not poster or poster.startswith("https://placehold.co"):
        poster = None

    added = user_data.add_list_item(current_user.id, list_id, tid, title=title, release_year=year, poster=poster)
    if added is None:
        return jsonify({"error": "Lista não encontrada."}), 404
    return jsonify({"message": "Adicionado!" if added else "Já estava na lista.", "added": added})


@app.route("/lists/<int:list_id>/items/<int:tmdb_id>", methods=["DELETE"])
@login_required
def lists_remove_item(list_id: int, tmdb_id: int):
    """Remove um filme da lista (posições são renumeradas)."""
    from core import user_data

    if not user_data.remove_list_item(current_user.id, list_id, tmdb_id):
        return jsonify({"error": "Filme não está nessa lista."}), 404
    return jsonify({"message": "Removido da lista."})


@app.route("/lists/<int:list_id>/order", methods=["PUT"])
@login_required
@auth_routes.verified_required
def lists_reorder(list_id: int):
    """Define a ordem de assistir. Body: {tmdb_ids: [...]} na ordem desejada."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    ids = data.get("tmdb_ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Envie tmdb_ids na ordem desejada."}), 400
    if not user_data.reorder_list(current_user.id, list_id, ids):
        return jsonify({"error": "Lista não encontrada."}), 404
    return jsonify({"message": "Ordem salva."})


@app.route("/lists/from_collection", methods=["POST"])
@login_required
@auth_routes.verified_required
def lists_from_collection():
    """Cria uma lista a partir de uma franquia da TMDB (collection), já na
    ordem de lançamento. Body: {collection_id, name?}."""
    from core import tmdb, user_data

    data = request.get_json(silent=True) or {}
    try:
        cid = int(data.get("collection_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid <= 0:
        return jsonify({"error": "collection_id inválido."}), 400

    coll = tmdb.collection_movies(cid)
    if coll is None:
        return jsonify({"error": "Franquia não encontrada (ou TMDB indisponível)."}), 503
    if not coll["parts"]:
        return jsonify({"error": "Essa franquia não tem filmes listados."}), 404

    name = str(data.get("name") or "").strip()[:200] or coll["name"] or "Franquia"
    lst = user_data.create_list(current_user.id, name, "Franquia importada da TMDB — ordem de lançamento.")
    for p in coll["parts"]:
        user_data.add_list_item(
            current_user.id,
            lst["list_id"],
            p["tmdb_id"],
            title=p["title"],
            release_year=p["release_year"],
            poster=p["poster"],
        )
    return jsonify(
        {"message": "Lista da franquia criada!", "list": user_data.get_list(current_user.id, lst["list_id"])}
    )


@app.route("/recommend_history", methods=["POST"])
@limiter.limit(RATE_HEAVY)
@login_required
def recommend_history():
    """Recomendações a partir do meu diário (avaliações locais com estrelas/❤).

    Body JSON: {n?, region?, providers?}. Usa o mesmo fluxo de perfil do
    Letterboxd, mas com as notas gravadas aqui na plataforma."""
    from core import user_data

    data = request.get_json(silent=True) or {}
    try:
        n = max(1, min(int(data.get("n", 15)), 50))
    except (TypeError, ValueError):
        n = 15

    detail = []
    for r in user_data.list_ratings(current_user.id):
        rating = r.get("rating")
        if rating is None and r.get("liked"):
            rating = 4.5  # curtiu sem dar estrelas → conta como amado
        if rating is None:
            continue  # só assistido/resenhado, sem sinal de gosto
        detail.append(
            {
                "tmdb_id": r["tmdb_id"],
                "rating": float(rating),
                "name": r.get("title"),
                "year": r.get("release_year"),
                "review": r.get("review") or "",
            }
        )
    if not detail:
        return jsonify({"error": "Avalie alguns filmes primeiro (estrelas ou ❤)."}), 400

    try:
        from core import inference_client

        region = (data.get("region") or "").strip() or None
        provider_ids = _parse_providers(data.get("providers"))
        result = inference_client.recommend_from_profile(detail, n=n, region=region, provider_ids=provider_ids)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    events.log(
        "recommend_history",
        filters={"providers": bool(provider_ids)},
        n_results=len(result["recommendations"]),
        user_id=_uid_or_none(),
        sid=_sid(),
    )

    return jsonify(
        {
            "rated_count": len(detail),
            "profile": result["profile"],
            "recommendations": posters.attach(result["recommendations"]),
        }
    )


# =========================================================================
# ROTA: SIMILAR (filmes parecidos com um filme — item-to-item)
# =========================================================================


@app.route("/similar/<int:movie_id>")
@limiter.limit(RATE_HEAVY)
def similar(movie_id: int):
    """Filmes parecidos com `movie_id` (conteúdo e5 + colaborativo item-item).

    Ex.: gostei de Blade Runner → me dá parecidos que talvez eu goste.
    Query param: n (1–50). 404 se o filme não está no catálogo."""
    try:
        n = max(1, min(int(request.args.get("n", 15)), 50))
    except (TypeError, ValueError):
        n = 15
    region = (request.args.get("region") or "").strip() or None
    provider_ids = _parse_providers(request.args.get("providers"))

    try:
        from core import inference_client

        result = inference_client.similar(movie_id, n=n, region=region, provider_ids=provider_ids)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    if result["seed"] is None:
        return jsonify({"error": "Filme não encontrado no catálogo."}), 404

    events.log(
        "similar",
        query=str(movie_id),
        filters={"providers": bool(provider_ids)},
        n_results=len(result["recommendations"]),
        user_id=_uid_or_none(),
        sid=_sid(),
        item_id=movie_id,
    )

    return jsonify(
        {
            "seed": result["seed"],
            "count": len(result["recommendations"]),
            "recommendations": posters.attach(result["recommendations"]),
        }
    )


# =========================================================================
# ROTA: SUBMIT RATINGS (filmes favoritos — qualquer quantidade)
# =========================================================================


@app.route("/submit_ratings", methods=["POST"])
@limiter.limit(RATE_HEAVY)
def submit_ratings():
    """Recomenda a partir de filmes favoritos selecionados (qualquer quantidade).

    Aceita JSON {movie_ids: [...]} ou form (movie_ids repetido / movie_id_1..N).
    Usa o fluxo de perfil (vetor de gosto por conteúdo + colaborativo) e devolve
    também o perfil traçado."""
    try:
        ids: list[int] = []
        data = request.get_json(silent=True) or {}
        raw = data.get("movie_ids") if isinstance(data.get("movie_ids"), list) else None
        if raw is None:
            raw = request.form.getlist("movie_ids") or [request.form.get(f"movie_id_{i}") for i in range(1, 11)]
        for x in raw:
            try:
                if x is not None and int(x) > 0:
                    ids.append(int(x))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))  # dedup preservando ordem
        if not ids:
            return jsonify({"error": "Selecione ao menos um filme."}), 400

        from core import inference_client

        region = (data.get("region") or "").strip() or None
        provider_ids = _parse_providers(data.get("providers"))
        detail = [{"tmdb_id": mid, "rating": 5.0, "name": None, "year": None, "review": ""} for mid in ids]
        result = inference_client.recommend_from_profile(detail, n=15, region=region, provider_ids=provider_ids)
        events.log(
            "recommend",
            filters={"seeds": len(ids), "providers": bool(provider_ids)},
            n_results=len(result["recommendations"]),
            user_id=_uid_or_none(),
            sid=_sid(),
        )
        return jsonify(
            {
                "message": "Recomendações personalizadas geradas!",
                "profile": result["profile"],
                "recommendations": posters.attach(result["recommendations"]),
            }
        )

    except RuntimeError as e:
        return jsonify(
            {
                "error": str(e),
                "message": "Modelo de recomendação não encontrado. Use a busca para encontrar filmes!",
                "recommendations": [],
            }
        ), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================================================
# ROTA: RECOMMEND (via Letterboxd ratings.csv — davidogral)
# =========================================================================


@app.route("/recommend", methods=["POST"])
@limiter.limit(RATE_HEAVY)
def recommend():
    """Recomendação via upload ratings.csv do Letterboxd."""
    file = request.files.get("ratings")
    if file is None or not file.filename:
        return jsonify({"error": "Envie o arquivo ratings.csv do Letterboxd."}), 400
    try:
        n = max(1, min(int(request.form.get("n", 20)), 50))
    except (TypeError, ValueError):
        n = 20

    try:
        from core import inference_client
        from recommender.letterboxd import import_ratings

        imported = import_ratings(file, resolver="auto")
        if not imported.matched:
            return jsonify(
                {
                    "error": "Nenhum filme do seu ratings.csv foi encontrado no catálogo.",
                    "total_rows": imported.total_rows,
                    "matched": 0,
                }
            ), 422
        # Perfil de gosto: vetor de conteúdo (embeddings) + resumo + colaborativo.
        region = (request.form.get("region") or "").strip() or None
        provider_ids = _parse_providers(request.form.get("providers"))
        result = inference_client.recommend_from_profile(
            imported.matched_detail, n=n, region=region, provider_ids=provider_ids
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    events.log(
        "recommend_letterboxd",
        filters={"matched": len(imported.matched), "match_rate": round(imported.match_rate, 3)},
        n_results=len(result["recommendations"]),
        user_id=_uid_or_none(),
        sid=_sid(),
    )

    return jsonify(
        {
            "total_rows": imported.total_rows,
            "matched": len(imported.matched),
            "match_rate": round(imported.match_rate, 3),
            "unmatched_count": len(imported.unmatched),
            "method": "perfil (conteúdo + colaborativo)",
            "profile": result["profile"],
            "recommendations": posters.attach(result["recommendations"]),
        }
    )


# =========================================================================
# ENGENHARIA — números do motor de recuperação para a página "Engenharia".
# Lê os JSON versionados em eval/results/ (atualizados por `python -m eval.run`
# / `eval.bench`) + os percentis ao vivo deste processo. Sem os JSON, cai em
# constantes de reserva. Cada campo diz sua proveniência (commit, device, data).
# =========================================================================
_EVAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "results")

_ABLATION_LABELS = {
    "bm25": "BM25 — lexical",
    "embedding": "Embedding — semântico",
    "thematic": "Temático — keywords",
    "fusion": "Fusão z-score (produção)",
    "fusion_rerank": "Fusão + cross-encoder (experimental)",
}
_ENGINEERING_FALLBACK = {
    "ablation": {
        "stale": True,
        "source": {"git_commit": "7d96851", "split": "test", "n_queries": 47},
        "rows": [
            {
                "pipeline": "bm25",
                "ndcg@10": 0.360,
                "mrr": 0.316,
                "recall@10": 0.532,
                "recall@50": 0.660,
                "median_rank": 7,
            },
            {
                "pipeline": "embedding",
                "ndcg@10": 0.299,
                "mrr": 0.260,
                "recall@10": 0.447,
                "recall@50": 0.532,
                "median_rank": 25,
            },
            {
                "pipeline": "thematic",
                "ndcg@10": 0.310,
                "mrr": 0.256,
                "recall@10": 0.511,
                "recall@50": 0.638,
                "median_rank": 10,
            },
            {
                "pipeline": "fusion",
                "ndcg@10": 0.733,
                "mrr": 0.686,
                "recall@10": 0.894,
                "recall@50": 0.936,
                "median_rank": 1,
            },
            {
                "pipeline": "fusion_rerank",
                "ndcg@10": 0.754,
                "mrr": 0.705,
                "recall@10": 0.915,
                "recall@50": 0.936,
                "median_rank": 1,
            },
        ],
    },
    "encoder": {
        "stale": True,
        "source": {"git_commit": "e2ed3ce", "device": "cpu"},
        "rows": [
            {
                "name": "e5-large fp32",
                "default": True,
                "ndcg@10": 0.733,
                "recall@10": 0.894,
                "recall@50": 0.936,
                "encode_p50": 169.2,
                "encode_p99": 352.1,
                "rss_mb": 2318,
            },
            {
                "name": "e5-large INT8 (ONNX)",
                "default": False,
                "ndcg@10": 0.729,
                "recall@10": 0.894,
                "recall@50": 0.936,
                "encode_p50": 212.0,
                "encode_p99": 368.9,
                "rss_mb": 1676,
            },
            {
                "name": "e5-small fp32",
                "default": False,
                "ndcg@10": 0.693,
                "recall@10": 0.830,
                "recall@50": 0.936,
                "encode_p50": 22.0,
                "encode_p99": 105.0,
                "rss_mb": 1026,
            },
        ],
    },
}


def _load_eval(name: str):
    try:
        with open(os.path.join(_EVAL_DIR, f"latest__{name}.json"), encoding="utf-8") as fh:
            import json

            return json.load(fh)
    except (OSError, ValueError):
        return None


def _engineering_payload() -> dict:
    ablation = dict(_ENGINEERING_FALLBACK["ablation"])
    test = _load_eval("test")
    if test and test.get("results"):
        run = test.get("run", {})
        ablation = {
            "stale": False,
            "source": {
                "git_commit": run.get("git_commit"),
                "timestamp_utc": run.get("timestamp_utc"),
                "split": run.get("split"),
                "n_queries": run.get("n_queries"),
                "dataset_sha1": run.get("dataset_sha1"),
            },
            "rows": [
                {
                    "pipeline": key,
                    "ndcg@10": round(r["metrics"].get("ndcg@10", 0), 3),
                    "mrr": round(r["metrics"].get("mrr", 0), 3),
                    "recall@10": round(r["metrics"].get("recall@10", 0), 3),
                    "recall@50": round(r["metrics"].get("recall@50", 0), 3),
                    "median_rank": r["metrics"].get("median_rank"),
                }
                for key, r in test["results"].items()
            ],
        }
    for row in ablation["rows"]:
        row["label"] = _ABLATION_LABELS.get(row["pipeline"], row["pipeline"])

    encoder = dict(_ENGINEERING_FALLBACK["encoder"])
    bench = _load_eval("bench-embed")
    if bench and bench.get("rows"):
        brun = bench.get("run", {})
        encoder = {
            "stale": False,
            "source": {
                "git_commit": brun.get("git_commit"),
                "device": brun.get("device"),
                "timestamp_utc": brun.get("timestamp_utc"),
            },
            "rows": [
                {
                    "name": row.get("name"),
                    "default": row.get("name", "").startswith("e5-large fp32"),
                    "ndcg@10": round(row["metrics"].get("ndcg@10", 0), 3),
                    "recall@10": round(row["metrics"].get("recall@10", 0), 3),
                    "recall@50": round(row["metrics"].get("recall@50", 0), 3),
                    "encode_p50": row["latency"]["encode_cold"]["p50"],
                    "encode_p99": row["latency"]["encode_cold"]["p99"],
                    "rss_mb": round(row["rss_mb"]["after_load"]),
                }
                for row in bench["rows"]
                if row.get("kind") == "embed"
            ],
        }

    lat = _load_eval("latency-test")
    protocol = {
        "task": "known-item — 1 filme relevante por consulta sobre ~22 mil títulos",
        "n_queries": 142,
        "dev": 95,
        "test": 47,
        "split_seed": 20260831,
        "reported": "só o split de teste (held-out)",
    }
    if test and test.get("run", {}).get("split_counts"):
        sc = test["run"]["split_counts"]
        protocol.update(n_queries=sc.get("total", 142), dev=sc.get("dev", 95), test=sc.get("test", 47))

    return {
        "ablation": ablation,
        "encoder": encoder,
        "protocol": protocol,
        "live_latency": {
            "stages": metrics.stage_percentiles(),
            "note": "p50/p95/p99 (ms) do tráfego real deste processo desde o último deploy",
        },
        "offline_latency": (lat or {}).get("stages", {}),
        "decisions": [
            {
                "title": "Cross-encoder desligado em produção",
                "body": "O re-ranker de 2º estágio era o default. A varredura (eval.run --sweep-rerank) mostra ganho "
                "dentro do ruído (teste +0,02 nDCG@10, dev −0,01) a ~250× de latência. Fica atrás de RECOMENDAI_RERANK=1.",
            },
            {
                "title": "e5-large fp32 continua o encoder padrão",
                "body": "INT8 dinâmico corta 28% de RAM mas não acelera o encode em CPU ARM (sem VNNI). e5-small é "
                "7,7× mais rápido no encode e usa metade da RAM, ao custo de −0,04 nDCG@10 — a um env var de distância.",
            },
            {
                "title": "Fusão z-score + prior de popularidade",
                "body": "Os 3 sinais são padronizados (z-score) e somados com um prior de popularidade. Nenhum sinal "
                "isolado passa de nDCG@10 ≈ 0,36; a fusão salta para ≈ 0,73 e leva a mediana da posição para #1.",
            },
        ],
        "links": {
            "metrics": "/metrics",
            "metodologia": "https://github.com/davidogral/recomenda-ai/blob/main/docs/METODOLOGIA.md",
            "eval": "https://github.com/davidogral/recomenda-ai/tree/main/eval",
            "adr": "https://github.com/davidogral/recomenda-ai/tree/main/docs/adr",
        },
    }


@app.route("/engineering")
def engineering():
    """Números do motor (ablação, encoder, protocolo, latência ao vivo) para a aba Engenharia."""
    return jsonify(_engineering_payload())


# =========================================================================
# PAINEL ADMIN — allowlist RECOMENDAI_ADMIN_EMAILS. Não-admin recebe 404
# (ver core/auth_routes.admin_required). Só o operador (LGPD: controlador dos
# dados) acessa; nunca expõe hash de senha.
# =========================================================================


@app.route("/admin")
@auth_routes.admin_required
def admin_page():
    return render_template("admin.html")


@app.route("/admin/api/overview")
@auth_routes.admin_required
def admin_overview():
    from core import users

    return jsonify({"users": users.count_users(), "signups_by_day": users.signups_by_day(30)})


@app.route("/admin/api/users")
@auth_routes.admin_required
def admin_users():
    from core import users

    return jsonify({"users": users.list_users()})


@app.route("/admin/api/analytics")
@auth_routes.admin_required
def admin_analytics():
    from core import events, user_data, users

    try:
        days = max(1, min(int(request.args.get("days", 30)), 365))
    except (TypeError, ValueError):
        days = 30
    counts = user_data.counts_by_user()
    engaged = sum(1 for c in counts.values() if c.get("ratings") or c.get("lists"))
    payload = {
        "users": {**users.count_users(), "engaged": engaged},
        "engagement_by_day": user_data.engagement_by_day(days),
        **events.analytics(days),
    }
    # anexa título aos filmes mais abertos
    for it in payload.get("top_items", []):
        mv = catalog.get_movie(it["tmdb_id"]) or {}
        it["title"] = mv.get("title") or f"#{it['tmdb_id']}"
        it["year"] = mv.get("release_year")
    return jsonify(payload)


@app.route("/admin/api/users/<int:uid>")
@auth_routes.admin_required
def admin_user_detail(uid: int):
    from core import user_data, users

    acc = users.get_user(uid)
    if acc is None:
        return jsonify({"error": "Conta não encontrada."}), 404
    return jsonify({"account": acc, **user_data.export_user_data(uid)})


@app.route("/admin/api/users/<int:uid>/<action>", methods=["POST"])
@auth_routes.admin_required
def admin_user_action(uid: int, action: str):
    from core import mailer, users

    acc = users.get_user(uid)
    if acc is None:
        return jsonify({"error": "Conta não encontrada."}), 404

    if action == "deactivate":
        users.set_active(uid, False)
        return jsonify({"ok": True, "is_active": False})
    if action == "reactivate":
        users.set_active(uid, True)
        return jsonify({"ok": True, "is_active": True})
    if action == "delete":
        users.delete_user(uid)
        return jsonify({"ok": True, "deleted": True})
    if action == "resend-verification":
        if acc["email_verified"]:
            return jsonify({"ok": True, "already": True})
        token = users.make_token("verify", uid)
        link = request.url_root.rstrip("/") + "/auth/verify/" + token
        mailer.send_verify(acc["email"], link)
        return jsonify({"ok": True, "sent": True})
    return jsonify({"error": "Ação desconhecida."}), 400


# =========================================================================
# HEALTH CHECK
# =========================================================================


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(429)
def _rate_limited(e):
    """Resposta JSON para o rate limit (o frontend lê `error`)."""
    return jsonify(
        {"error": "Muitas requisições — tente de novo em instantes.", "detail": str(getattr(e, "description", e))}
    ), 429


# =========================================================================
# ENTREGA — cache de estáticos + gzip de texto.
# Em produção o proxy (Caddy/Cloudflare) normalmente já comprime; isto cobre
# `gunicorn app:app` cru e garante os cabeçalhos de cache.
# =========================================================================
_GZIP_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")


_STATIC_PREFIX = (app.static_url_path or "/static").rstrip("/") + "/"


@app.after_request
def _persist_sid(resp):
    """Grava o cookie `sid` quando um handler gerou um novo (só nas ações
    registradas em analytics). HttpOnly / SameSite=Lax / Secure em produção."""
    if getattr(g, "_sid_new", False) and getattr(g, "_sid", None):
        resp.set_cookie(
            "sid",
            g._sid,
            max_age=90 * 24 * 3600,
            httponly=True,
            samesite="Lax",
            secure=security.cookie_secure(),
        )
    return resp


@app.after_request
def _delivery(resp):
    path = request.path
    if path.startswith(_STATIC_PREFIX):
        # estáticos versionados por ?v= na tag → 1 dia é seguro. Sobrescreve o
        # "no-cache" que o handler estático do Flask 3 põe por padrão.
        resp.headers["Cache-Control"] = "public, max-age=86400"
    elif path == "/":
        resp.headers.setdefault("Cache-Control", "no-cache")

    try:
        accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
        ctype = resp.headers.get("Content-Type", "")
        if (
            accepts_gzip
            and resp.status_code == 200
            and not resp.headers.get("Content-Encoding")
            and ctype.startswith(_GZIP_TYPES)
        ):
            if resp.direct_passthrough:  # arquivos estáticos são passthrough
                resp.direct_passthrough = False
            body = resp.get_data()
            if len(body) > 1024:
                packed = gzip.compress(body, 6)
                if len(packed) < len(body):
                    resp.set_data(packed)
                    resp.headers["Content-Encoding"] = "gzip"
                    resp.headers["Content-Length"] = str(len(packed))
                    vary = resp.headers.get("Vary")
                    resp.headers["Vary"] = f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"
    except Exception:  # compressão nunca pode derrubar a resposta
        pass
    return resp


# =========================================================================
# WARMUP — pré-carrega o motor de busca no boot (não na 1ª requisição).
# Roda no import do módulo, então vale tanto para `python app.py` quanto para
# `gunicorn app:app` (cada worker aquece o seu). `RECOMENDAI_NO_WARMUP=1` pula.
# =========================================================================
if os.environ.get("RECOMENDAI_NO_WARMUP", "0").lower() not in ("1", "true", "yes"):
    try:
        from retrieval.search_engine import get_engine

        get_engine()  # constrói o singleton + warmup() dos modelos
        print("[boot] motor de busca pré-carregado")
    except Exception as e:  # nunca deixa o warmup derrubar o processo
        print(f"[boot] warmup do motor de busca falhou (segue sob demanda): {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    # use_reloader=False: o reloader do Werkzeug reimporta o módulo e o warmup
    # carregaria o modelo de embeddings 2× em dev. Para hot-reload, rode
    # `RECOMENDAI_NO_WARMUP=1 flask --app app run --debug`.
    app.run(debug=True, host="0.0.0.0", port=port, use_reloader=False)
