# 🎬 RecomendAI — Inteligência Artificial e Recuperação de Informação

![Status](https://img.shields.io/badge/status-funcional-success)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Machine Learning](https://img.shields.io/badge/ML-Híbrido-orange)
![SRI](https://img.shields.io/badge/SRI-BM25%20%2B%20Embeddings-blueviolet)

O **RecomendAI** é um ecossistema completo de recomendação de filmes que combina técnicas de **Recuperação de Informação (SRI)** e **Machine Learning (ML)** para entregar sugestões personalizadas — e para ajudar você a achar aquele filme que está na ponta da língua.

> 📄 **Quer entender os algoritmos a fundo?** Veja [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md) — cada técnica do SRI e da recomendação explicada (o que faz e o que resolve), com diagramas e fórmulas.

---

## 🧠 Arquitetura Tecnológica

O sistema opera com um motor híbrido de duas camadas que trabalham de forma independente e complementar:

### 1. Sistema de Recuperação de Informação (SRI) — *achar um filme*
A camada de **busca** recupera filmes a partir do que o usuário descreve, mesmo sem lembrar o nome:
*   **Sinal lexical (BM25):** indexamos as sinopses com um `CountVectorizer` (stopwords em PT) e ranqueamos por BM25 — ótimo para casar termos exatos.
*   **Sinal semântico (Embeddings multilíngues):** as sinopses, gêneros e *keywords* são convertidos em vetores densos com `sentence-transformers`. A busca usa **similaridade de cosseno**, entendendo o tema mesmo quando as palavras não batem.
*   **Fusão de sinais:** os três scores são padronizados (z-score) e somados com um prior de popularidade; o resultado é explicado no card (tema, sinopse, termos, nome). A [tabela de ablação](#-tabela-de-ablação-por-sinal) mostra o ganho da fusão sobre cada sinal isolado.
*   **Re-ranker cross-encoder (2º estágio):** existe, mas fica **desligado em produção** — a [ablação](#cross-encoder-2º-estágio-desligado-em-produção) mostra ganho dentro do ruído a ~250× de latência (`RECOMENDAI_RERANK=1` para experimentar).
*   **Busca facetada:** título *fuzzy* (`rapidfuzz`), filtros de diretor/ator, gênero, idioma e faixa de anos.

### 2. Machine Learning (ML) — *o que assistir a seguir*
A camada de **recomendação** prevê o que o usuário vai gostar:
*   **Filtragem Colaborativa (SVD):** o *Singular Value Decomposition* é treinado em **ratings reais** (≈2,9M de avaliações, ≈19,8k usuários, ≈15k filmes) e atinge **RMSE ≈ 0,80** em holdout. Os fatores latentes ficam serializados em `.npy` para inferência em milissegundos.
*   **Perfil de gosto:** a partir dos filmes que o usuário curte, montamos um vetor de conteúdo (embeddings) e cruzamos com o sinal colaborativo para re-rankear as sugestões.
*   **Fallback item-item:** quando não há sinal colaborativo suficiente, usamos os vizinhos mais próximos pré-computados.

---

## ⚙️ Funcionalidades Principais

*   **Três modos de uso:** achar um filme pela busca, escolher filmes favoritos (quantos quiser) ou importar suas notas do **Letterboxd** (`ratings.csv`).
*   **Explicabilidade:** cada recomendação mostra *por que* apareceu — sinais de match, confiança e o seu perfil de gosto (diretores, gêneros, atores, temas, décadas).
*   **Autocomplete inteligente:** diretor, ator e títulos sugeridos diretamente do catálogo.
*   **Pôsteres via TMDB:** imagens carregadas da TMDB com cache local (degradam graciosamente para placeholder).
*   **Persistência de Dados:** catálogo e ratings em SQLite via SQLAlchemy.
*   **Motor pronto para rodar:** índices de busca e pesos do recomendador vêm por `dvc pull` (remote em B2) — a aplicação sobe sem treino prévio.

---

## 📂 Estrutura do Projeto

```
RecomendaAI/
├── app.py                  # Backend Flask (rotas de busca e recomendação)
├── core/                   # Catálogo, conexão com dados, pôsteres, TMDB
│   ├── catalog.py
│   ├── db.py
│   └── posters.py
├── retrieval/              # SRI — busca por sinopse/nome/pessoa
│   ├── search_engine.py    # Motor de busca (BM25 + embeddings + fuzzy)
│   ├── reranker.py         # 2º estágio: cross-encoder sobre o top-300
│   ├── index_builder.py    # Constrói o índice → retrieval/index/
│   └── index/              # Índices serializados (BM25, embeddings, meta)
├── recommender/            # ML — recomendação personalizada
│   ├── profile.py          # Perfil de gosto (conteúdo + colaborativo)
│   ├── collaborative.py    # Inferência do SVD
│   ├── letterboxd.py       # Importação do ratings.csv
│   ├── train.py            # Treina o SVD → recommender/weights/
│   └── weights/            # Fatores latentes (.npy) + meta.json
├── eval/                   # Avaliação executável do SRI (fonte de verdade)
│   ├── run.py              # `python -m eval.run` → métricas + JSON versionado
│   ├── metrics.py          # nDCG@10, MRR, Recall@k, Precision@k
│   ├── pipelines.py        # variantes da ablação por sinal
│   ├── datasets/           # queries.jsonl (142 consultas, split dev/teste)
│   └── results/            # JSON por rodada + history.jsonl
├── database/
│   └── models.py           # Esquema do banco (SQLAlchemy)
├── research/
│   ├── train_model.ipynb   # Notebook didático de treino/experimentos
│   └── evaluate_sri.ipynb  # Exploração manual do SRI (não é a avaliação oficial)
├── data/
│   └── tmdb_movies_large.json  # Dataset de metadados da TMDB
└── frontend/               # UI/UX (index.html + style.css)
```

---

## 🚀 Como Executar

### 1. Instalação
```bash
pip install -r requirements.txt
```

### 2. Artefatos de modelo (DVC)
Os índices de busca (`retrieval/index/`, `retrieval/index_e5small/`) e os pesos do recomendador (`recommender/weights/`) **não ficam no git** — são geridos por **[DVC](https://dvc.org)**, com *remote* em **Backblaze B2**. Os ponteiros `*.dvc` estão versionados; os binários vêm de:

```bash
pip install "dvc-s3"
# credenciais do B2 (ficam em .dvc/config.local, fora do git):
dvc remote modify --local b2 access_key_id     <B2_KEY_ID>
dvc remote modify --local b2 secret_access_key <B2_APP_KEY>
dvc pull                                   # baixa index/ + weights/
```

> `.dvc/config` tem um `url`/`endpointurl` **placeholder** (`s3://CHANGE-ME-bucket/...`) — ajuste para o seu bucket/região B2. Sem o `dvc pull`, reconstrua os artefatos localmente (passo 5).

### 3. (Opcional) Credenciais da TMDB para pôsteres
Copie `.env.example` para `.env` e preencha com seu token da TMDB. Sem isso, o sistema funciona normalmente, exibindo placeholders no lugar das imagens.

### 4. Execução do Servidor
```bash
python app.py
```
Acesse: `http://localhost:5001` — o motor de busca é **pré-carregado no boot** (`RECOMENDAI_NO_WARMUP=1` desliga). Na 1ª vez, o modelo de embeddings do `sentence-transformers` é baixado uma vez.

### 6. (Opcional) Reconstruir os modelos
Alternativa ao `dvc pull` — regenerar do zero:
```bash
python -m retrieval.index_builder   # reconstrói retrieval/index/
python -m recommender.train         # retreina o SVD em recommender/weights/
```

### 7. Avaliação do SRI
A avaliação é **executável** e a saída é **JSON versionado** em `eval/results/` — não vive mais num notebook.

```bash
python -m eval.run                # split de teste, 5 pipelines, grava o JSON
python -m eval.run --fast         # sem o cross-encoder (segundos, não minutos)
python -m eval.run --split dev    # conjunto de calibração
```

Cada consulta é uma **paráfrase de enredo** e existe **um único** filme relevante (recuperação *known-item* sobre os ≈22 mil títulos). Conjunto: **142 consultas** — as 52 originais (35 dev / 17 teste) + **90 novas nunca usadas na calibração** (60 dev / 30 teste). Só o **split de teste (47)** é reportado. Detalhes e protocolo em [`eval/README.md`](eval/README.md).

---

## 📊 Tabela de ablação por sinal

Cada sinal **isolado** vs. a **fusão** — split de teste, 47 consultas *held-out*:

| Pipeline | nDCG@10 | MRR | Recall@10 | Recall@50 | Precision@10 | mediana | latência p50 |
|---|---|---|---|---|---|---|---|
| BM25 puro *(lexical)* | 0,360 | 0,316 | 0,53 | 0,66 | 0,053 | #7 | 2 ms |
| Só embedding *(semântico)* | 0,299 | 0,260 | 0,45 | 0,53 | 0,045 | #25 | 5 ms |
| Só temático *(keywords)* | 0,310 | 0,256 | 0,51 | 0,64 | 0,051 | #10 | 5 ms |
| **Fusão** *(produção)* | **0,733** | **0,686** | **0,89** | **0,94** | 0,089 | **#1** | 7 ms |

> `Precision@10` tem teto de `0,1` — só há um relevante por consulta; está aqui por continuidade com a métrica antiga.

**Os sinais são complementares.** Nenhum sozinho passa de nDCG@10 ≈ 0,36; a fusão z-score dos três + prior de popularidade salta para **0,73** e leva a mediana da posição para **#1**. O lexical resgata enredos com termo próprio, o semântico entende paráfrase, o temático pega conceito ("time loop", "memory loss") — juntos cobrem os buracos uns dos outros.

### Cross-encoder (2º estágio): **desligado em produção**

O re-ranker cross-encoder era o default. A varredura do tamanho do pool (`python -m eval.run --sweep-rerank`) mostra que ele **não compensa** — split de teste:

| pool cross-encoder | nDCG@10 | MRR | Recall@10 | Recall@50 | latência p50 | p90 |
|---|---|---|---|---|---|---|
| **0 — desligado** *(produção)* | 0,733 | 0,686 | 0,89 | 0,94 | **7 ms** | 8 ms |
| 10 | 0,743 | 0,698 | 0,89 | 0,94 | 76 ms | 94 ms |
| 20 | 0,750 | 0,699 | 0,92 | 0,94 | 124 ms | 156 ms |
| 50 | 0,754 | 0,705 | 0,92 | 0,94 | 304 ms | 378 ms |
| 100 | 0,754 | 0,705 | 0,92 | 0,94 | 588 ms | 702 ms |
| 300 *(default antigo)* | 0,740 | 0,694 | 0,89 | 0,92 | 2 423 ms | 3 109 ms |

<sub>Latência medida em Apple Silicon com MPS; num servidor CPU o cross-encoder é ainda mais lento — a fusão (BLAS) muda pouco.</sub>

- **Ganho não confiável.** No teste, o melhor pool (50) sobe o nDCG@10 em +0,02; no split de calibração (dev) o mesmo pool **cai** 0,823 → 0,814. Para n = 47–95 consultas, isso é ruído.
- **O pool 300 era o pior dos mundos:** pior qualidade que o pool 50 *e* Recall@50 mais baixo (reordena candidatos distantes e erra), a **~2 s por busca** — ~300× a latência da fusão sozinha (~7 ms).

**Modelo menor (MiniLM-L6) também não salva** (`python -m eval.bench rerank`, split de teste):

| modelo | pool | nDCG@10 | MRR | Recall@50 | rerank p99 |
|---|---|---|---|---|---|
| mMiniLMv2-**L12** (atual) | 50 | 0,754 | 0,705 | 0,94 | 498 ms |
| mMiniLM-**L6** (mmarco) | 50 | 0,705 | 0,656 | 0,94 | 616 ms |
| mMiniLM-**L6** (mmarco) | 300 | 0,671 | 0,626 | 0,94 | 1 845 ms |

O L6 fica **abaixo da fusão sem re-ranker** (0,705 < 0,733) e nem é mais rápido. Metade das camadas, reranker pior.

- **Decisão (produção):** `RECOMENDAI_RERANK=0` por padrão. A fusão sem 2º estágio já entrega mediana da posição **#1** e tira o modelo cross-encoder (~120 MB) do cold-start. Para experimentar, `RECOMENDAI_RERANK=1` com `RECOMENDAI_RERANK_POOL=50` e o L12 (`RECOMENDAI_RERANKER_MODEL`).

Regerar: `python -m eval.run --sweep-rerank` (varredura de pool) · `python -m eval.bench rerank` (L12 × L6).

---

## ⚡ Escolha do encoder — nDCG@10 × latência × RSS

Medido em **CPU** (o servidor de produção não tem GPU/MPS), split de teste, `python -m eval.bench embed`. `encode` = *forward* do transformer numa consulta fora do cache (a etapa que domina a busca); `search` = pipeline inteiro com o cache LRU quente; RSS = memória residente do processo depois de carregar o modelo.

| config | dim | nDCG@10 | MRR | Recall@10 | Recall@50 | `encode` p50 / p99 | `search` p99 | RSS |
|---|---|---|---|---|---|---|---|---|
| **e5-large fp32** *(padrão)* | 1024 | **0,733** | **0,686** | **0,89** | 0,94 | 169 / 352 ms | 36 ms | 2 318 MB |
| e5-large **INT8** (ONNX) | 1024 | 0,729 | 0,680 | 0,89 | 0,94 | 212 / 369 ms | 38 ms | **1 676 MB** |
| **e5-small** fp32 | 384 | 0,693 | 0,654 | 0,83 | 0,94 | **22 / 105 ms** | 30 ms | **1 026 MB** |

**Leitura:**
- **INT8 dinâmico (ONNX) corta RAM, não latência — nesta máquina.** Qualidade intacta (nDCG@10 −0,004, ruído) e RSS −28 %, mas o `encode` **não** ficou mais rápido: o kernel INT8 do ONNX Runtime em CPU ARM não tem VNNI/AVX-512, então não bate o GEMM fp32 do Accelerate. Num servidor x86 com VNNI a conta muda — vale re-medir lá.
- **e5-small é o único que mexe a latência:** `encode` p50 **169 → 22 ms** (7,7×), p99 352 → 105 ms; RSS **2,3 → 1,0 GB**. O custo é **nDCG@10 −0,04** (−5,5 %) e **Recall@10 −6 pp** (o filme certo cai do top-10 em ~1 de cada 15 consultas a mais). **Recall@50 não muda** — o candidato certo continua chegando; só ranqueia um pouco pior.
- Com o **cache LRU** a maioria das consultas nem paga o `encode` (`encode_warm` ≈ 0 ms), então o `encode_cold` só pesa na cauda de consultas inéditas.

**Escolhido: `e5-large fp32` continua o padrão** (sem regressão de qualidade). `e5-small` fica a **um env var** de distância para quando o alvo de deploy for uma caixa pequena (< 2 GB de RAM) ou a latência de cauda `encode` p99 precisar ficar abaixo de ~150 ms:

```bash
RECOMENDAI_INDEX_DIR=retrieval/index_e5small python app.py      # modo baixa-latência/RAM
RECOMENDAI_EMBED_BACKEND=onnx-int8 python app.py                 # e5-large INT8 (só RAM)
```

Regerar: `python -m eval.bench embed` → `eval/results/latest__bench-embed.json` (métricas + p50/p95/p99 por etapa + RSS por config).

---

## 📈 Pipeline de Dados

1.  **Coleta:** metadados dos filmes (sinopses, gêneros, elenco) vêm da TMDB; os ratings reais alimentam o modelo colaborativo.
2.  **Indexação:** `retrieval/index_builder.py` gera os índices de busca (BM25 + embeddings).
3.  **Treino:** `recommender/train.py` ajusta o SVD nos ratings reais e serializa os fatores latentes.
4.  **Avaliação:** `python -m eval.run` mede o SRI no split de teste e versiona o resultado em `eval/results/`.
5.  **Entrega:** o motor híbrido combina busca e recomendação para responder em tempo real na interface.

---

## 📚 Proveniência dos Dados

| dado | fonte | licença / termos | onde entra |
|---|---|---|---|
| Metadados de filmes (sinopse, gêneros, elenco, *keywords*, pôster) | **API da TMDB** | [Termos de uso da API TMDB](https://www.themoviedb.org/api-terms-of-use) — uso permitido com atribuição; **este produto não é endossado nem certificado pela TMDB** | catálogo, busca, ficha |
| Nota e nº de votos de filmes (`imdb_rating`, `imdb_votes`) | **[IMDb Non-Commercial Datasets](https://developer.imdb.com/non-commercial-datasets/)** (`title.ratings.tsv.gz`) | Uso **pessoal e não-comercial** apenas; sem redistribuição. Baixado por `core/enrich.py` | ranking de "Essenciais" |
| Crítica (`metascore`, `rt_score`) | **[OMDb API](https://www.omdbapi.com/)** (agrega Metacritic / Rotten Tomatoes) | Termos do OMDb; requer `OMDB_API_KEY` | ranking de "Essenciais" |
| Cânone (`canon_rank`) | Lista **curada** no repositório (`core/enrich.py::CANON`) — base Sight & Sound 2022 + clássicos de consenso | Curadoria própria | ranking de "Essenciais" |
| **~2,9 M de avaliações** que treinam o SVD colaborativo | **MovieLens / GroupLens** — *release exato a confirmar* (ver ⚠️ abaixo) | **[Termos do GroupLens](https://files.grouplens.org/datasets/movielens/ml-25m-README.html)** | recomendação colaborativa |
| Notas do usuário (aba Letterboxd / Assistidos) | *Upload do próprio usuário* | Dado do usuário; fica só no `data/user.db` local, não é redistribuído | recomendação a partir do perfil |

### ⚠️ Bloqueador de produção — avaliações do modelo colaborativo

Os ~2,9 M de ratings (`movies.db → ratings`, escala meia-estrela 0,5–5,0, *timestamps* de 1996–2018) **têm forte cara de MovieLens** (escala, formato, reconciliação `movieId → tmdb_id`), mas:

- **o *release* exato não está documentado** (ml-25m? ml-latest? um subconjunto filtrado?) e o script de reconciliação **não está no repositório** — o dado veio pronto no `movies.db`;
- os **termos do GroupLens proíbem uso comercial ou "revenue-bearing" sem permissão** de um pesquisador do GroupLens, e exigem citar Harper & Konstan (2015), *The MovieLens Datasets: History and Context*, ACM TiiS.

**Antes de qualquer deploy público:** (1) confirmar a origem exata com quem montou o `movies.db`; (2) versionar o script de ingestão/reconciliação; (3) decidir conformidade — se houver monetização, obter permissão do GroupLens **ou** trocar por um dataset com licença compatível (ex.: MovieLens com permissão, ou ratings próprios). Rastreado em `data/quality_report.json` (`ratings_provenance: "unconfirmed"`).

### Validação de schema (Pandera)

```bash
python -m core.validate            # checa e escreve data/quality_report.json
python -m core.validate --strict   # exit != 0 se algum schema falhar (para CI)
```

Schemas em [`core/schemas.py`](core/schemas.py): **catálogo** (tipos, faixas — `vote_average` 0–10, `release_year` 1870–2100, `tmdb_id` único…), **dump IMDb** (`tconst` = `^tt\d+$`, `averageRating` 1–10) e as **linhas reconciliadas** catálogo × IMDb. `core/enrich.py` roda a validação do dump durante a ingestão e grava o relatório ao final.

**Taxa de match atual** (`data/quality_report.json`): `imdb_id` resolvido em **99,9 %** dos filmes, nota IMDb em **99,7 %**, cânone 100/100. Os poucos casos que falham são curtas/filmes de TV/raridades fora do `title.ratings` do IMDb — listados no relatório.
