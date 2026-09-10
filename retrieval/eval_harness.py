"""Harness legado de avaliação da busca por sinopse (52 casos, MRR/hits@k).

⚠️  SUPERSEDIDO por `eval/` — use `python -m eval.run` (split dev/teste, nDCG@10,
    MRR, Recall@50, tabela de ablação, saída em JSON versionado). Este arquivo
    fica só como referência histórica da calibração dos pesos da fusão.

Para cada par (consulta em PT, filme esperado), roda o ranking de sinopse sobre
todo o catálogo e reporta a posição (1-based) do filme alvo; agrega com MRR,
hits@k e posição mediana/média.

Rodar:  .venv/bin/python -m retrieval.eval_harness
        .venv/bin/python -m retrieval.eval_harness --verbose
        .venv/bin/python -m retrieval.eval_harness --core
        .venv/bin/python -m retrieval.eval_harness --weights lexical=0.25,embed=0.5,keyword=0.45
"""

from __future__ import annotations

import argparse
import statistics
from typing import Optional

import numpy as np

from core import catalog
from retrieval.search_engine import SearchEngine

# (consulta no modo sinopse, dica de título PT, ano) — alvos vetados do plano.
CORE_SET: list[tuple[str, str, int]] = [
    ("um homem incapaz de formar novas memórias caça o assassino da esposa usando fotos e tatuagens",
     "Amnésia", 2000),
    ("uma família pobre se infiltra trabalhando na casa de uma família rica escondendo que são parentes",
     "Parasita", 2019),
    ("um escritor enlouquece cuidando de um hotel isolado e vazio no inverno com a família na neve",
     "O Iluminado", 1980),
    ("um tubarão gigante aterroriza uma cidade praiana atacando banhistas no verão",
     "Tubarão", 1975),
    ("um jovem baterista é levado ao limite por um maestro tirânico numa escola de música",
     "Whiplash", 2014),
    ("dois ilusionistas rivais obcecados pelo segredo de um truque de teletransporte",
     "O Grande Truque", 2006),
    ("dois detetives caçam um assassino que mata pelos sete pecados capitais",
     "Seven", 1995),
    ("um programador testa uma robô com inteligência artificial na mansão de um bilionário recluso",
     "Ex_Machina", 2015),
    ("um filme mudo de um homem com uma câmera filmando a cidade",
     "Um Homem com uma Câmera", 1929),
    ("um homem comum descobre que toda a sua vida é um programa de televisão e que todos ao seu redor são atores",
     "O Show de Truman", 1998),
    ("após um término doloroso um casal apaga da mente as lembranças um do outro num procedimento",
     "Brilho Eterno", 2004),
    ("um repórter fica preso revivendo o mesmo dia de inverno repetidas vezes",
     "Feitiço do Tempo", 1993),
]

# Filmes famosos, paráfrases estilo-usuário (todos resolvem no catálogo PT).
EXTENDED_SET: list[tuple[str, str, int]] = [
    ("um hacker descobre que a realidade é uma simulação controlada por máquinas e aprende a dobrar suas regras", "Matrix", 1999),
    ("um homem insone forma um clube secreto de brigas com um vendedor de sabão carismático", "Clube da Luta", 1999),
    ("dois meninos crescem numa favela violenta; um vira fotógrafo e o outro chefe do tráfico", "Cidade de Deus", 2002),
    ("um adolescente viaja ao passado num carro modificado e precisa fazer seus pais se apaixonarem", "De Volta para o Futuro", 1985),
    ("um hobbit parte numa jornada para destruir um anel maligno no fogo de uma montanha", "Senhor dos Anéis", 2001),
    ("brinquedos ganham vida e um cowboy sente ciúmes de um boneco astronauta", "Toy Story", 1995),
    ("um homem simples e bondoso vive por acaso os grandes momentos da história do país", "Forrest Gump", 1994),
    ("o filho relutante de um chefão da máfia acaba assumindo os negócios da família", "O Poderoso Chefão", 1972),
    ("um romance entre um artista pobre e uma jovem rica a bordo de um transatlântico que afunda", "Titanic", 1997),
    ("um parque temático com dinossauros clonados sai do controle numa ilha", "Jurassic Park", 1993),
    ("um ciborgue assassino é enviado do futuro para matar uma mulher", "O Exterminador do Futuro", 1984),
    ("um general romano traído é escravizado e se torna gladiador para se vingar do imperador", "Gladiador", 2000),
    ("um piloto cruza um buraco de minhoca em busca de um novo planeta para salvar a humanidade", "Interestelar", 2014),
    ("um comediante fracassado e doente mental mergulha na loucura e se torna um vilão", "Coringa", 2019),
    ("um grupo de soldados judeus caça e aterroriza nazistas na frança ocupada", "Bastardos Inglórios", 2009),
    ("um escravo liberto vira caçador de recompensas para resgatar a esposa de um fazendeiro", "Django", 2012),
    ("um pelotão atravessa a frança durante a guerra para resgatar um único soldado", "Resgate do Soldado Ryan", 1998),
    ("um peixe-palhaço atravessa o oceano para reencontrar o filho capturado por mergulhadores", "Procurando Nemo", 2003),
    ("um robozinho solitário que limpa o lixo de uma terra abandonada se apaixona por outro robô", "WALL", 2008),
    ("um policial infiltrado na máfia e um criminoso infiltrado na polícia tentam se desmascarar", "Os Infiltrados", 2006),
    ("uma agente do fbi consulta um canibal preso para capturar outro assassino em série", "O Silêncio dos Inocentes", 1991),
    ("uma mulher foge com dinheiro roubado e para num motel isolado de um rapaz perturbado", "Psicose", 1960),
    ("um jovem violento é submetido a um tratamento que o condiciona a passar mal com a violência", "Laranja Mecânica", 1971),
    ("uma garçonete tímida decide secretamente transformar a vida das pessoas ao seu redor", "Amélie", 2001),
    ("um repórter investiga o sentido da última palavra dita por um magnata antes de morrer", "Cidadão Kane", 1941),
    ("um caçador de andróides persegue replicantes fugitivos numa metrópole chuvosa e sombria", "Blade Runner", 1982),
    ("um filhote de leão foge culpado pela morte do pai e mais tarde volta para reclamar o trono", "O Rei Leão", 1994),
    ("um motorista de táxi insone e solitário enlouquece na cidade e planeja um ato violento", "Taxi Driver", 1976),
    ("a ascensão e queda de um rapaz que sonha a vida toda em ser um gângster", "Os Bons Companheiros", 1990),
    ("um rapaz negro visita a família branca da namorada e descobre um plano sinistro", "Corra", 2017),
    ("um detetive investiga um sumiço numa ilha-presídio psiquiátrica e duvida da própria sanidade", "Ilha do Medo", 2010),
    ("um empresário alemão salva centenas de judeus empregando-os na fábrica durante o holocausto", "A Lista de Schindler", 1993),
    ("numa terra desértica pós-apocalíptica uma rebelde foge num caminhão com esposas escravizadas", "Mad Max", 2015),
    ("um idoso amarra milhares de balões na casa para voar até uma cachoeira e leva um garoto junto", "Altas Aventuras", 2009),
    ("uma nave com um computador de inteligência artificial viaja ao espaço e a máquina se rebela", "2001", 1968),
    ("uma bailarina obcecada pela perfeição enlouquece ao assumir um papel duplo de cisne", "Cisne Negro", 2010),
    ("um menino que enxerga pessoas mortas é ajudado por um psicólogo infantil", "O Sexto Sentido", 1999),
    ("um boxeador desconhecido de bairro pobre ganha a chance de lutar pelo título mundial", "Rocky", 1976),
    ("dois amigos planejam uma fuga ousada de uma prisão onde um banqueiro foi condenado injustamente", "Um Sonho de Liberdade", 1994),
    ("um arqueólogo aventureiro corre contra nazistas para achar uma relíquia bíblica poderosa", "Indiana Jones", 1981),
]

EVAL_SET: list[tuple[str, str, int]] = CORE_SET + EXTENDED_SET


def resolve_target(title_hint: str, year: int) -> Optional[int]:
    """Resolve (dica de título, ano) -> tmdb_id. As dicas são substrings limpas
    do título PT real, então casa por substring + ano (determinístico);
    `token_set_ratio` é só fallback (robusto a títulos com sufixo/subtítulo)."""
    from rapidfuzz import fuzz

    cat = catalog.get_catalog()
    hint = title_hint.lower()
    candidates = [(tid, mv) for tid, mv in cat.items() if mv.get("release_year") == year]

    # 1) substring exata (case-insensitive)
    subs = [tid for tid, mv in candidates if hint in (mv.get("title") or "").lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:  # desempata pelo título mais curto (mais específico ao hint)
        return min(subs, key=lambda t: len(cat[t]["title"] or ""))

    # 2) fallback fuzzy robusto a tokens extras
    best_id, best_score = None, -1.0
    for tid, mv in candidates:
        score = fuzz.token_set_ratio(hint, (mv.get("title") or "").lower())
        if score > best_score:
            best_score, best_id = score, tid
    return best_id if best_score >= 80 else None


def rank_of_target(engine: SearchEngine, query: str, target_id: int,
                   weights: Optional[dict] = None) -> tuple[int, float]:
    """Posição 1-based do alvo no ranking de sinopse (e seu score bruto)."""
    weights = weights or {}
    scores = engine._synopsis_scores(query, **weights)  # alinhado a _movie_ids
    order = np.argsort(scores)[::-1]
    row = engine._row_index.get(int(target_id))
    if row is None:
        return -1, 0.0
    pos = int(np.where(order == row)[0][0]) + 1
    return pos, float(scores[row])


def run(engine: Optional[SearchEngine] = None, weights: Optional[dict] = None,
        eval_set: Optional[list] = None, verbose: bool = False,
        rank_fn=None) -> dict:
    """Roda o harness e devolve um dict de métricas. Imprime um resumo.

    `rank_fn(engine, query, target_id) -> (pos, score)` permite avaliar um
    pipeline alternativo (ex.: com re-ranker); o default usa `rank_of_target`.
    """
    engine = engine or SearchEngine()
    eval_set = eval_set if eval_set is not None else EVAL_SET
    rank_fn = rank_fn or (lambda e, q, t: rank_of_target(e, q, t, weights))

    rows = []
    for query, hint, year in eval_set:
        target = resolve_target(hint, year)
        if target is None:
            rows.append({"hint": hint, "year": year, "pos": None, "title": "(não resolvido)"})
            continue
        pos, _score = rank_fn(engine, query, target)
        rows.append({"hint": hint, "year": year, "pos": pos,
                     "title": engine.catalog[target]["title"], "tmdb_id": target})

    ranks = [r["pos"] for r in rows if r["pos"] and r["pos"] > 0]
    n = len(eval_set)
    n_res = len(ranks)
    mrr = round(sum(1.0 / p for p in ranks) / n, 4) if n else 0.0
    summary = {
        "n": n, "resolved": n_res,
        "mrr": mrr,
        "hits@1": sum(p <= 1 for p in ranks),
        "hits@3": sum(p <= 3 for p in ranks),
        "hits@10": sum(p <= 10 for p in ranks),
        "median": int(statistics.median(ranks)) if ranks else None,
        "mean": round(statistics.mean(ranks), 1) if ranks else None,
        "rows": rows,
    }

    if verbose:
        print(f"\n{'filme esperado':<42} {'ano':>4}  {'pos':>5}")
        print("-" * 60)
        for r in sorted(rows, key=lambda r: (r["pos"] is None, r["pos"] or 0), reverse=True):
            pos = r["pos"]
            mark = "" if pos is None else (" ✓" if pos <= 10 else "  ")
            posstr = "—" if pos is None else f"#{pos}"
            print(f"{r['title'][:42]:<42} {r['year']:>4}  {posstr:>5}{mark}")
        print("-" * 60)
    else:
        worst = sorted((r for r in rows if r["pos"] and r["pos"] > 10),
                       key=lambda r: -r["pos"])[:8]
        if worst:
            print("piores (>#10): " + " · ".join(f"{r['title'][:22]}(#{r['pos']})" for r in worst))

    print(f"N={n}  MRR={summary['mrr']}  hits@1={summary['hits@1']}/{n}  "
          f"hits@3={summary['hits@3']}/{n}  hits@10={summary['hits@10']}/{n}  "
          f"mediana=#{summary['median']}  média=#{summary['mean']}")
    return summary


def _parse_weights(s: Optional[str]) -> Optional[dict]:
    if not s:
        return None
    out: dict[str, float] = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[f"{k.strip()}_weight"] = float(v)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Harness de avaliação da busca por sinopse.")
    p.add_argument("--weights", help="ex.: lexical=0.25,embed=0.5,keyword=0.45")
    p.add_argument("--core", action="store_true", help="Só os 12 casos curados.")
    p.add_argument("--verbose", action="store_true", help="Tabela completa.")
    args = p.parse_args()
    run(weights=_parse_weights(args.weights),
        eval_set=CORE_SET if args.core else None,
        verbose=args.verbose)
