"""Essenciais do cinema — listas ranqueadas por gênero, estilo e diretor.

**Gênero principal (um filme, um gênero):** a TMDB marca vários gêneros por
filme (Pulp Fiction vira "Comédia"; Parasita aparece em três listas). Aqui
cada filme conta só no seu gênero principal: o mais *específico* dos seus
gêneros, segundo uma ordem curada (GENRE_PRIORITY) — gêneros definidores
(Terror, Animação, Faroeste...) vencem gêneros guarda-chuva (Drama, Comédia,
Thriller). Ex.: Pulp Fiction → Crime; Parasita → Thriller; Psicose → Terror.

**Score de essencial:** nota alta sozinha não faz clássico (nicho superfã
infla nota; bilheteria não faz cânone). A base é uma NOTA BAYESIANA — a nota
puxada para a média global conforme o filme tem poucos votos — calculada de
preferência sobre os dados do IMDb (amostra ~100× maior e menos enviesada para
lançamento que a da TMDB; enriquecidos por core.enrich), com fallback para a
TMDB. Sobre ela, dois bônus multiplicativos e limitados: durabilidade (filme
que envelheceu e segue relevante) e cânone (estar na lista curada Sight & Sound
+ clássicos). Assim a popularidade entra só como piso de confiança, não como
motor. Os pesos são constantes no topo do módulo — fáceis de recalibrar.

Estilos são keywords da TMDB (em inglês no banco) com curadoria e rótulo em
português. Tudo requer o catálogo SQLite (as rotas degradam com 503 sem ele).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

from core import catalog, db

# Ranking de essencial. A base é uma NOTA BAYESIANA (esteem): a nota do filme
# puxada para a média global conforme ele tem POUCOS votos — assim superfã de
# nicho não infla, e a popularidade entra só como piso de confiança, não como
# motor. Preferimos os dados do IMDb (amostra ~100× maior que a da TMDB; ver
# core.enrich), caindo para a TMDB quando faltam. Sobre essa nota, dois bônus
# MULTIPLICATIVOS e limitados dão o "cânone" sem virar lista de crítico:
#   - durabilidade: filme que envelheceu e continua relevante (até +W_DURABILITY);
#   - cânone: estar na lista curada (Sight & Sound + clássicos) — até +W_CANON no
#     topo, decaindo pelo rank. Recalibre à vontade.
IMDB_FLOOR = 25000    # piso de votos do IMDb para a nota bayesiana (M)
TMDB_FLOOR = 500      # piso equivalente quando só há dados da TMDB
_IMDB_MIN_TRUST = 1000  # votos do IMDb a partir dos quais preferimos o IMDb
# Crítica (via OMDb): a nota bayesiana é do PÚBLICO (IMDb); quando há nota da
# crítica, misturamos as duas — W_CRITIC é o peso da crítica na base (0 = só
# público; 0.5 = meio a meio). Preferimos o Rotten Tomatoes ao Metacritic (ver
# _esteem). Em 0.40 fica o equilíbrio: a crítica resgata o clássico reavaliado
# (The Thing, O Iluminado) sem afogar quem tem público forte. Peso maior começa
# a virar "ranking de % do RT" e derruba esses mesmos filmes. Sem nota de crítica
# o filme fica 100% no público (degrada liso).
W_CRITIC = 0.40
W_DURABILITY = 0.06   # bônus máximo por longevidade
W_CANON = 0.12        # bônus máximo por estar no topo do cânone
DURA_YEARS = 45       # idade em que o bônus de durabilidade satura

# Do mais específico (define o filme) ao mais guarda-chuva. O gênero principal
# de um filme é o primeiro desta ordem entre os gêneros dele. Curadoria — se um
# filme cair na prateleira errada, ajusta-se aqui.
GENRE_PRIORITY = [
    "Documentário",
    "Animação",
    "Faroeste",
    "Música",
    "Guerra",
    "Terror",
    "Ficção científica",
    "História",
    "Fantasia",
    "Crime",
    "Mistério",
    "Romance",
    "Aventura",
    "Ação",
    "Thriller",
    "Comédia",
    "Drama",
    "Família",
    "Cinema TV",
]
_GENRE_RANK = {g: i for i, g in enumerate(GENRE_PRIORITY)}

# Curadoria de estilos/subgêneros: (keyword na TMDB, rótulo em pt-BR).
# Ordem alfabética pelo rótulo; só aparecem os que existem no catálogo.
STYLES: list[tuple[str, str]] = [
    ("alien invasion", "Invasão alienígena"),
    ("anime", "Anime"),
    ("martial arts", "Artes marciais"),
    ("heist", "Assalto (heist)"),
    ("boxing", "Boxe"),
    ("haunted house", "Casa mal-assombrada"),
    ("coming of age", "Amadurecimento"),
    ("conspiracy", "Conspiração"),
    ("cyberpunk", "Cyberpunk"),
    ("dystopia", "Distopia"),
    ("mockumentary", "Falso documentário"),
    ("film noir", "Film noir"),
    ("folk horror", "Folk horror"),
    ("found footage", "Found footage"),
    ("gangster", "Gângster"),
    ("giallo", "Giallo"),
    ("body horror", "Horror corporal"),
    ("home invasion", "Invasão domiciliar"),
    ("kaiju", "Kaiju"),
    ("kung fu", "Kung fu"),
    ("mafia", "Máfia"),
    ("mecha", "Mecha"),
    ("monster", "Monstros"),
    ("musical", "Musical"),
    ("neo-noir", "Neo-noir"),
    ("parody", "Paródia"),
    ("post-apocalyptic future", "Pós-apocalíptico"),
    ("road movie", "Road movie"),
    ("robot", "Robôs"),
    ("samurai", "Samurai"),
    ("satire", "Sátira"),
    ("serial killer", "Serial killer"),
    ("slasher", "Slasher"),
    ("space opera", "Space opera"),
    ("stop motion", "Stop motion"),
    ("superhero", "Super-heróis"),
    ("survival horror", "Terror de sobrevivência"),
    ("psychological horror", "Terror psicológico"),
    ("time travel", "Viagem no tempo"),
    ("courtroom", "Tribunal"),
    ("vampire", "Vampiros"),
    ("revenge", "Vingança"),
    ("werewolf", "Lobisomens"),
    ("spaghetti western", "Western spaghetti"),
    ("whodunit", "Whodunit"),
    ("wuxia", "Wuxia"),
    ("zombie", "Zumbis"),
]

# Cânone curado de diretores essenciais (nomes exatos como estão na TMDB).
# NÃO é ranking de bilheteria: se fosse por votos, a lista encheria de
# tarefeiros de blockbuster (Russo, Yates, Bay, Columbus...) e enterraria os
# autores. Aqui entra só quem é referência de cinema — os mestres clássicos e
# os autores modernos. Qualquer outro diretor continua acessível pela busca
# livre do catálogo; esta lista é só o destaque. Curadoria — edite à vontade.
DIRECTORS: list[str] = [
    # Mestres clássicos (Hollywood clássica, cinema mudo e do pós-guerra)
    "Alfred Hitchcock", "Stanley Kubrick", "Orson Welles", "Billy Wilder",
    "John Ford", "Howard Hawks", "Fritz Lang", "Charlie Chaplin", "David Lean",
    "Sidney Lumet", "Elia Kazan", "Serguei Eisenstein",
    # Autores europeus e do mundo
    "Ingmar Bergman", "Federico Fellini", "Michelangelo Antonioni",
    "Vittorio De Sica", "Sergio Leone", "Jean Renoir", "Robert Bresson",
    "Jean-Luc Godard", "François Truffaut", "Jean-Pierre Melville",
    "Éric Rohmer", "Agnès Varda", "Luis Buñuel", "Roman Polanski",
    "Krzysztof Kieślowski", "Andrei Tarkovsky", "Béla Tarr", "Aki Kaurismäki",
    "Werner Herzog", "Michael Haneke", "Pedro Almodóvar", "Lars von Trier",
    # Cinema asiático
    "Akira Kurosawa", "Yasujiro Ozu", "Hayao Miyazaki", "Satyajit Ray",
    "Bong Joon-Ho",
    # Autores modernos (EUA e adjacências)
    "Francis Ford Coppola", "Martin Scorsese", "Steven Spielberg",
    "Brian De Palma", "Terrence Malick", "David Lynch", "David Cronenberg",
    "Woody Allen", "Spike Lee", "Jim Jarmusch", "John Cassavetes",
    "Dario Argento", "Quentin Tarantino", "Joel Coen", "Ethan Coen",
    "Paul Thomas Anderson", "Wes Anderson", "David Fincher",
    "Christopher Nolan", "Denis Villeneuve", "Ridley Scott", "James Cameron",
    "Peter Jackson", "Clint Eastwood", "Guillermo del Toro", "Alfonso Cuarón",
]

_MOVIE_COLS = ("m.tmdb_id AS tmdb_id, m.title AS title, m.release_year AS release_year, "
               "m.vote_average AS vote_average, m.vote_count AS vote_count, "
               "m.imdb_rating AS imdb_rating, m.imdb_votes AS imdb_votes, "
               "m.canon_rank AS canon_rank, m.metascore AS metascore, "
               "m.rt_score AS rt_score, m.overview AS overview")


def _sort_key(s: str) -> str:
    """Chave de ordenação alfabética ignorando acentos (Éric, François, Béla...)."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


@lru_cache(maxsize=1)
def _global_mean() -> float:
    """Nota média global da TMDB (C da fórmula), sobre filmes com votação relevante."""
    val = db.query_scalar("SELECT AVG(vote_average) FROM movies WHERE vote_count >= 50")
    return float(val or 6.3)


@lru_cache(maxsize=1)
def _imdb_mean() -> float:
    """Nota média global do IMDb (C da fórmula no espaço IMDb)."""
    val = db.query_scalar("SELECT AVG(imdb_rating) FROM movies WHERE imdb_votes >= 10000")
    return float(val or 6.8)


@lru_cache(maxsize=1)
def _canon_max() -> int:
    """Maior canon_rank presente — para escalar o bônus de cânone por posição."""
    val = db.query_scalar("SELECT MAX(canon_rank) FROM movies")
    return int(val or 100)


@lru_cache(maxsize=1)
def _primary_by_movie() -> dict[int, str]:
    """Gênero principal de cada filme (o mais específico via GENRE_PRIORITY)."""
    out: dict[int, str] = {}
    for tid, genres in catalog.genres_by_movie().items():
        if genres:
            out[tid] = min(genres, key=lambda g: _GENRE_RANK.get(g, len(GENRE_PRIORITY)))
    return out


def genre_options() -> list[dict[str, Any]]:
    """Gêneros com contagem por gênero PRINCIPAL (cada filme conta uma vez),
    dos maiores para os menores."""
    counts: dict[str, int] = {}
    for g in _primary_by_movie().values():
        counts[g] = counts.get(g, 0) + 1
    return [{"name": g, "count": c}
            for g, c in sorted(counts.items(), key=lambda x: -x[1])]


def style_options() -> list[dict[str, Any]]:
    """Estilos curados presentes no catálogo: {key, label, count}."""
    keys = [k for k, _ in STYLES]
    placeholders = ",".join("?" for _ in keys)
    rows = db.query(
        f"""SELECT k.name AS name, COUNT(*) AS count
            FROM keywords k JOIN movie_keywords mk ON mk.keyword_id = k.keyword_id
            WHERE k.name IN ({placeholders})
            GROUP BY k.keyword_id""",
        keys,
    )
    counts = {r["name"]: r["count"] for r in rows}
    out, seen = [], set()
    for key, label in STYLES:
        if label in seen or key not in counts:
            continue
        seen.add(label)
        out.append({"key": key, "label": label, "count": counts[key]})
    out.sort(key=lambda x: x["label"].lower())
    return out


def director_options() -> list[dict[str, Any]]:
    """Cânone curado de diretores (DIRECTORS) presente no catálogo, em ordem
    alfabética: {name, films}. Popularidade não entra — é lista de referência,
    não ranking de bilheteria."""
    placeholders = ",".join("?" for _ in DIRECTORS)
    rows = db.query(
        f"""SELECT p.name AS name, COUNT(*) AS films
            FROM movie_people mp
            JOIN people p ON p.person_id = mp.person_id
            JOIN movies m ON m.tmdb_id = mp.tmdb_id
            WHERE mp.role = 'director' AND p.name IN ({placeholders})
            GROUP BY p.person_id""",
        DIRECTORS,
    )
    counts = {r["name"]: r["films"] for r in rows}
    out = [{"name": name, "films": counts[name]}
           for name in DIRECTORS if name in counts]
    out.sort(key=lambda d: _sort_key(d["name"]))
    return out


def _pool(genre: Optional[str], style: Optional[str],
          director: Optional[str]) -> list[dict[str, Any]]:
    if genre:
        return db.query(
            f"""SELECT {_MOVIE_COLS} FROM movies m
                JOIN movie_genres mg ON mg.tmdb_id = m.tmdb_id
                JOIN genres g ON g.genre_id = mg.genre_id
                WHERE g.name = ? COLLATE NOCASE""",
            (genre,),
        )
    if style:
        return db.query(
            f"""SELECT {_MOVIE_COLS} FROM movies m
                JOIN movie_keywords mk ON mk.tmdb_id = m.tmdb_id
                JOIN keywords k ON k.keyword_id = mk.keyword_id
                WHERE k.name = ? COLLATE NOCASE""",
            (style,),
        )
    if director:
        return db.query(
            f"""SELECT {_MOVIE_COLS} FROM movies m
                JOIN movie_people mp ON mp.tmdb_id = m.tmdb_id
                JOIN people p ON p.person_id = mp.person_id
                WHERE p.name = ? COLLATE NOCASE AND mp.role = 'director'""",
            (director,),
        )
    return []


def _esteem(r: dict[str, Any], year_now: int) -> tuple[float, float, str]:
    """Score de essencial do filme: nota bayesiana do público (esteem), misturada
    com a nota da crítica quando existe, × bônus limitados de durabilidade e
    cânone. Devolve (score, nota_pública_usada, fonte)."""
    iv = int(r.get("imdb_votes") or 0)
    if iv >= _IMDB_MIN_TRUST:                       # amostra do IMDb é confiável
        R, V, M, C, source = float(r.get("imdb_rating") or 0.0), float(iv), \
            IMDB_FLOOR, _imdb_mean(), "imdb"
    else:                                            # fallback: dados da TMDB
        R, V, M, C, source = float(r.get("vote_average") or 0.0), \
            float(r.get("vote_count") or 0), TMDB_FLOOR, _global_mean(), "tmdb"
    wr = V / (V + M) * R + M / (V + M) * C           # nota bayesiana (0..10)

    # Crítica: preferimos o Rotten Tomatoes (tomatômetro, 0..100) ao Metascore.
    # O Metacritic ancora na crítica da ÉPOCA e subestima clássico reavaliado
    # (The Thing: Metascore 57 vs RT 85%). Metascore fica de reserva. Sem nota
    # de crítica, o filme fica 100% no público.
    critic = r.get("rt_score")
    if critic is None:
        critic = r.get("metascore")
    quality = wr if critic is None else (1 - W_CRITIC) * wr + W_CRITIC * (critic / 10.0)

    age = max(0, year_now - (r.get("release_year") or year_now))
    dura = W_DURABILITY * min(age, DURA_YEARS) / DURA_YEARS
    canon = 0.0
    if r.get("canon_rank"):
        canon = W_CANON * (1 - (r["canon_rank"] - 1) / max(_canon_max(), 1))
    return quality * (1 + dura + canon), R, source


def essentials(genre: Optional[str] = None, style: Optional[str] = None,
               director: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """Ranking completo de essenciais do grupo (até `limit`), melhor primeiro.

    Em modo gênero, o grupo contém só filmes cujo gênero PRINCIPAL é o pedido
    (um filme, um gênero). O score mistura a nota do público (preferindo o IMDb)
    com a da crítica (Metacritic/RT), mais bônus limitados de durabilidade e
    cânone — ver o topo do módulo. Em grupos grandes, filmes com <10 votos
    saem do páreo."""
    rows = _pool(genre, style, director)
    if genre and rows:
        primary = _primary_by_movie()
        want = genre.strip().lower()
        rows = [r for r in rows if (primary.get(r["tmdb_id"]) or "").lower() == want]
    if not rows:
        return []
    if len(rows) > 30:
        rows = [r for r in rows if (r["vote_count"] or 0) >= 10]

    year_now = datetime.now().year
    for r in rows:
        score, rating, source = _esteem(r, year_now)
        r["score"] = round(score, 4)
        r["rating"] = round(rating, 1)          # nota do público que entrou no ranking
        r["rating_source"] = source             # "imdb" ou "tmdb"
        r["critic"] = r.get("rt_score") if r.get("rt_score") is not None \
            else r.get("metascore")             # crítica (0..100): RT preferido, MC reserva
        r["overview"] = (r["overview"] or "")[:220]

    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]
