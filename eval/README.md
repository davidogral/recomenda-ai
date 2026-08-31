# `eval/` — Avaliação do SRI

Avaliação **executável e versionada** da busca por sinopse. Substitui o
`research/evaluate_sri.ipynb` como fonte de verdade — o notebook fica só para
exploração manual.

```bash
.venv/bin/python -m eval.run                 # split de teste, 5 pipelines, grava JSON
.venv/bin/python -m eval.run --split dev      # calibração (não reportar como resultado)
.venv/bin/python -m eval.run --fast           # sem o cross-encoder (segundos, não minutos)
.venv/bin/python -m eval.run --pipelines fusion,fusion_rerank
.venv/bin/python -m eval.run --sweep-rerank   # curva qualidade × latência do pool do cross-encoder
```

> **Produção roda sem o cross-encoder** (`RECOMENDAI_RERANK=0`, o default em
> `retrieval/search_engine.py`). `eval.run` mede o re-ranker mesmo assim
> (constrói o motor com `rerank=True`) — é uma variante da ablação, não o
> pipeline shipado. A varredura (`--sweep-rerank`, ver
> `results/latest__sweep-rerank-test.json`) é o que embasa essa decisão: ganho
> dentro do ruído (teste +0,02 nDCG@10, dev −0,01) a ~250× de latência.

## O que é medido

Recuperação **known-item**: cada consulta é uma paráfrase de enredo estilo
usuário e existe **um único** filme relevante (o que a pessoa tenta lembrar). A
avaliação roda o ranking sobre os ~22 mil filmes do catálogo e anota a
**posição** desse filme.

| Métrica | Lê como | Por que importa aqui |
|---|---|---|
| **nDCG@10** | qualidade do top-10 com desconto de posição | métrica-resumo principal |
| **MRR** | "o filme certo *subiu*?" | sensível às primeiras posições |
| **Recall@50** | "o candidato certo *chega* ao re-ranker?" | o pool do cross-encoder é 300; 50 já mostra se a 1ª etapa entregou |
| **Recall@10** | cobertura na primeira tela de resultados | — |
| **Precision@10** | `#relevantes / 10` | teto de `0.1` (só há 1 relevante); reportada por continuidade com o notebook antigo |
| mediana / média do rank, hits@{1,3,10} | diagnóstico | inspeção de casos ruins |

Definições em [`metrics.py`](metrics.py).

## Conjunto de dados — `datasets/queries.jsonl`

142 consultas, cada linha com o `tmdb_id` relevante **congelado** (rótulo
estável). Fonte editável: [`datasets/build_queries.py`](datasets/build_queries.py)
(`.venv/bin/python -m eval.datasets.build_queries` regenera o `.jsonl` e **falha**
se alguma dica de título não resolver ou colidir).

### Split dev / teste

| grupo | origem | dev | teste | observação |
|---|---|---:|---:|---|
| **v1** | 52 casos originais do `retrieval/eval_harness.py` | 35 | 17 | usados na calibração dos pesos → os 17 de teste são "vistos"; leia como continuidade histórica |
| **v2** | 90 casos novos, nunca usados em calibração | 60 | 30 | **held-out de verdade** — é aqui que o número de teste vale como generalização |
| **total** | | 95 | 47 | |

Divisão determinística (`SPLIT_SEED = 20260831`). **Só o split de teste é
reportado** no README principal e na METODOLOGIA. A calibração de pesos/limiares
(`w_emb`, `w_kw`, `w_lex`, `blend`, limiares de intenção `0.92`/`0.85`) deve
olhar **apenas o dev**; quando isso acontecer, o teste inteiro (v1 + v2) passa a
ser held-out limpo.

## Pipelines da ablação — [`pipelines.py`](pipelines.py)

| chave | sinal |
|---|---|
| `bm25` | só lexical (BM25 Okapi cru) |
| `embedding` | só semântico (cosseno com embedding da sinopse) |
| `thematic` | só temático (cosseno com embedding de keywords/gêneros) |
| `fusion` | fusão z-score dos 3 sinais + prior de popularidade — **pipeline de produção** |
| `fusion_rerank` | a fusão + cross-encoder no top-`RERANK_POOL` (50) — variante experimental, off em produção |

## Saída — `results/`

Cada rodada grava:

- `AAAA-MM-DDTHH-MM-SSZ__<split>.json` — registro imutável (config, `git_commit`,
  `dataset_sha1`, métricas por pipeline, **posição por consulta**);
- `latest__<split>.json` — ponteiro para a última rodada daquele split;
- `history.jsonl` — 1 linha-resumo por rodada, para acompanhar a evolução.

Os JSON são versionados no git — é o histórico de qualidade do sistema.

## Determinismo

`eval.run` desliga o fallback de nome via TMDB (`RECOMENDAI_TMDB_NAMES=0`) para a
rodada não depender de rede. Embeddings e cross-encoder são determinísticos em
CPU/MPS. Pesos e limiares vêm de `retrieval/search_engine.py` e ficam registrados
em `run.config` no JSON.
