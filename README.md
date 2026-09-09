# 🎬 RecomendAI — Inteligência Artificial e Recuperação de Informação

![CI](https://github.com/davidogral/recomenda-ai/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Machine Learning](https://img.shields.io/badge/ML-Híbrido-orange)
![SRI](https://img.shields.io/badge/SRI-8%20sinais%20%2B%20LLM-blueviolet)

O **RecomendAI** é um ecossistema completo de recomendação de filmes que combina técnicas de **Recuperação de Informação (SRI)** e **Machine Learning (ML)** para entregar sugestões personalizadas — e para ajudar você a achar aquele filme que está na ponta da língua.

> 📄 **Quer entender os algoritmos a fundo?** Veja [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md) — cada técnica do SRI e da recomendação explicada (o que faz e o que resolve), com diagramas e fórmulas.

---

## 🧠 Arquitetura Tecnológica

O sistema opera com um motor híbrido de duas camadas que trabalham de forma independente e complementar:

### 1. Sistema de Recuperação de Informação (SRI) — *achar um filme*
A camada de **busca** recupera filmes a partir do que o usuário descreve, mesmo sem lembrar o nome. Oito sinais entram na fusão, cada um resolvendo um jeito diferente de a memória do usuário falhar:
*   **Sinal lexical (BM25):** indexamos as sinopses com um `CountVectorizer` (stopwords em PT) e ranqueamos por BM25 — ótimo para casar termos exatos.
*   **Sinal semântico (Embeddings multilíngues):** sinopse e *keywords*/gêneros viram vetores densos com `sentence-transformers` (dois espaços separados: sentido da sinopse e tema). Entende o filme mesmo quando as palavras não batem.
*   **Sinal de personagem (fuzzy, Jaro-Winkler):** casa nome de personagem/ator citado na consulta contra o elenco de topo do filme, tolerando erro de grafia ("Jonh wick", "Toreto"). Nasceu de consulta real do painel admin.
*   **Sinal de enredo via Wikipédia (2 formas):** a sinopse da TMDB é curta demais pra citar um detalhe do 3º ato; o enredo completo da Wikipédia entra por dois canais: léxico (BM25) e semântico em **trechos com MaxSim** (fatia o texto em janelas de ~380 palavras e usa o **máximo** de similaridade entre elas, não a média do documento inteiro).
*   **Entendimento e reranking de consulta via LLM (Groq, grátis):** um modelo pequeno classifica a consulta (objeto/pessoa/genérica) e ajusta pesos *só* para esse tipo; depois de fundir os sinais, o top-30 candidato vai pro mesmo LLM **ler a sinopse de verdade** e promover quem bate o fato citado, a única etapa do pipeline que julga conteúdo em vez de calcular similaridade. Detalhes: [seção abaixo](#-como-o-uso-real-evoluiu-o-algoritmo).
*   **Fusão com teto de outlier:** os sinais são padronizados (z-score, capado em ±8 desvios-padrão; sem isso, um termo raríssimo citado uma vez em documento errado dominava a soma) e somados com ReLU + prior de popularidade; o resultado é explicado no card (tema, sinopse, termos, nome). A [tabela de ablação](#-tabela-de-ablação-por-sinal) mostra o ganho da fusão sobre cada sinal isolado.
*   **Re-ranker cross-encoder (2º estágio):** existe, mas fica **desligado em produção**: a [ablação](#cross-encoder-2º-estágio-desligado-em-produção) mostra ganho dentro do ruído a ~40× de latência (`RECOMENDAI_RERANK=1` para experimentar).
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
├── app.py                  # API (Flask) — rotas, validação, rate limit, /metrics
├── inference/              # Serviço de inferência (FastAPI) — camada de ML
│   └── main.py             # /v1/search_combined, /v1/similar, /v1/recommend_*
├── core/                   # Catálogo, dados, pôsteres, TMDB, métricas, schemas
│   ├── catalog.py · db.py · posters.py · tmdb.py
│   ├── metrics.py          # métricas Prometheus (Flask + FastAPI)
│   ├── inference_client.py # costura api ↔ inference (HTTP ou in-process)
│   ├── schemas.py          # schemas Pandera (catálogo / dump IMDb)
│   └── validate.py         # `python -m core.validate` → quality_report.json
├── retrieval/              # SRI — busca por sinopse/nome/pessoa
│   ├── search_engine.py    # Motor de busca (BM25 + embeddings + fuzzy)
│   ├── reranker.py         # 2º estágio: cross-encoder (off em produção)
│   ├── onnx_embed.py       # export ONNX + quantização INT8 do encoder
│   ├── index_builder.py    # Constrói o índice → retrieval/index/ (DVC)
│   └── index*/             # índices serializados — geridos por DVC
├── recommender/            # ML — recomendação personalizada (SVD + conteúdo)
│   └── weights/            # Fatores latentes (.npy) — geridos por DVC
├── eval/                   # Avaliação executável do SRI (fonte de verdade)
│   ├── run.py              # `python -m eval.run` → métricas + JSON versionado
│   ├── bench.py · latency.py  # matriz encoder × latência × RSS, perfil por etapa
│   ├── datasets/           # queries.jsonl (142 consultas, split dev/teste)
│   └── results/            # JSON por rodada + history.jsonl
├── monitoring/             # prometheus.yml + dashboard Grafana (provisionado)
├── tests/ + .github/workflows/   # smoke tests + CI (ruff/mypy/pytest + eval-gate)
├── docs/                   # metodologia, ADRs + contrato de UX (Nielsen/mobile)
├── Dockerfile · docker-compose.yml
├── research/               # notebooks (exploração, não é a avaliação oficial)
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

> `.dvc/config` tem um `url`/`endpointurl` **placeholder** (`s3://CHANGE-ME-bucket/...`) — ajuste para o seu bucket/região B2. Sem o `dvc pull`, reconstrua os artefatos localmente (passo 6).
>
> O `data/raw/movies.db` (catálogo + ratings) **não** é gerido por DVC — é reconstruído por `python -m recommender.ingest_movielens` (ratings) + `python -m core.enrich` (sinais IMDb/crítica). Mantenha uma cópia à parte ou coloque no seu bucket manualmente.

### 3. (Opcional) Credenciais da TMDB para pôsteres
Copie `.env.example` para `.env` e preencha com seu token da TMDB. Sem isso, o sistema funciona normalmente, exibindo placeholders no lugar das imagens.

### 4. Execução do Servidor
```bash
python app.py
```
Acesse: `http://localhost:5001` — o motor de busca é **pré-carregado no boot** (`RECOMENDAI_NO_WARMUP=1` desliga). Na 1ª vez, o modelo de embeddings do `sentence-transformers` é baixado uma vez.

### 5. (Opcional) Chave da Groq: entendimento e reranking de consulta via LLM
`core/query_llm.py` usa a **[Groq](https://console.groq.com)** (camada grátis, `openai/gpt-oss-20b`) para classificar a consulta e, depois, reler o top-30 da fusão e promover quem bate o fato citado. **Sem `GROQ_API_KEY` no ambiente, o sistema funciona normalmente**: a busca cai só no retrieval por similaridade, sem os dois passos de LLM (a mesma degradação graciosa dos pôsteres sem token TMDB). Nunca cole a chave em código versionado; ela vai em `.env`/`.dvc/config.local`-style, fora do git:
```bash
export GROQ_API_KEY=...                     # ativa entendimento + reranking de consulta
export RECOMENDAI_RERANK_LLM=0              # desliga só o reranking (entendimento continua)
```

### 6. (Opcional) Reconstruir os modelos
Alternativa ao `dvc pull` — regenerar do zero:
```bash
python -m core.enrich --wikipedia   # (opcional) baixa o enredo da Wikipédia p/ o catálogo — crawl longo, retomável
python -m retrieval.index_builder   # reconstrói retrieval/index/ (BM25 + espaços de embedding: sinopse, tema, enredo em trechos/MaxSim, bio de pessoa)
python -m recommender.train         # retreina o SVD em recommender/weights/
```
> Os canais **enredo (Wikipédia, léxico + MaxSim)**, **personagem** (fuzzy no elenco) e **trivia de pessoa** só valem depois de reconstruir o índice. `core.enrich --wikipedia` resolve o artigo pelo `imdb_id` (via Wikidata `P345`), extrai a seção "Plot/Enredo" e grava em `movies.wikipedia_plot`; `--wiki-min-votes` limita aos filmes com relevância; `--wikipedia-people` faz o mesmo para bios do elenco/direção. Pesos default: `RECOMENDAI_ENTITY_WEIGHT` (personagem, **0,45**, ligado), `RECOMENDAI_PLOT_BM25_WEIGHT` (enredo léxico, **0,1**, ligado), `RECOMENDAI_PLOT_CHUNK_WEIGHT` (enredo MaxSim, **0,5**, ligado), `RECOMENDAI_PERSON_MATCH_WEIGHT` (trivia de pessoa, **0**, desligado/experimental), `RECOMENDAI_ZSCORE_CLIP` (teto do z-score na fusão, **8**).

### 7. Avaliação do SRI
A avaliação é **executável** e a saída é **JSON versionado** em `eval/results/` — não vive mais num notebook.

```bash
python -m eval.run                # split de teste, 5 pipelines, grava o JSON
python -m eval.run --fast         # sem o cross-encoder (segundos, não minutos)
python -m eval.run --split dev    # conjunto de calibração
```

Cada consulta é uma **paráfrase de enredo** e existe **um único** filme relevante (recuperação *known-item* sobre os ≈22 mil títulos). Protocolo de calibração/reporte: **142 consultas**, as 52 originais (35 dev / 17 teste) + **90 novas nunca usadas na calibração** (60 dev / 30 teste). Só o **split de teste (47)** é reportado. Além desse par dev/teste, o dataset tem **3 splits diagnósticos** (não usados para reportar, usados para achar buraco): `hard` (30, consulta deliberadamente oblíqua), `entity` (20, nome de personagem com erro de grafia) e `object` (42, objeto/veículo específico), nascidos das consultas reais do painel admin (ver [seção abaixo](#-como-o-uso-real-evoluiu-o-algoritmo)). Detalhes e protocolo em [`eval/README.md`](eval/README.md).

> **Por que o LLM (Groq) não entra nesses números.** O `eval.run` é determinístico e não faz chamada de rede de propósito: precisa ser reproduzível e rodar em CI sem custo/latência de API. O entendimento e o reranking via LLM são medidos à parte, com scripts ad hoc contra o Groq real, e vivem só na produção. As tabelas abaixo são o **piso** do retrieval puro; a seção seguinte mostra o que o LLM soma em cima disso.

---

## 🌱 Como o uso real evoluiu o algoritmo

Nada abaixo saiu de intuição: o [painel admin](#contas--segurança) loga toda busca (sem dado pessoal), e ler esse log é o que decidiu o que construir. Ordem em que aconteceu:

1. **Consulta real revelou o padrão.** Lendo o log em 2026-09, ~2/3 das consultas difíceis citavam **objeto/veículo específico** ("skyline azul e prata", "dodge charger preto") ou **personagem com erro de grafia** ("Jonh wick", "Toreto"); nenhum dos dois aparece na sinopse curta da TMDB.
2. **Teto no outlier de termo raro (`RECOMENDAI_ZSCORE_CLIP=8`).** "filme que chove hambúrguer" não achava *Tá Chovendo Hambúrguer*: um filme não relacionado citava a palavra rara "hambúrguer" uma vez e o z-score dele explodia (75, sobre 22 mil documentos quase todos zero) e dominava a soma sozinho. Capar o z-score em 8 desvios-padrão subiu **os 5 splits ao mesmo tempo** (object nDCG@10 +0,017, entity +0,026, hard +0,013) sem piorar nenhum. É o tipo de correção que só aparece testando contra consulta real, não sintética.
3. **Canal de personagem, fuzzy (Jaro-Winkler).** Nome de personagem quase sempre vem com erro de digitação numa busca real. Casar o nome mesmo torto contra o elenco de topo do filme subiu o split `entity` de nDCG@10 **0,49 → 0,78**.
4. **Enredo da Wikipédia, léxico + trechos (MaxSim).** A sinopse da TMDB some antes de citar o objeto do 3º ato. O enredo da Wikipédia entra fatiado em janelas de ~380 palavras, com o score do filme sendo o **máximo** entre os trechos (MaxSim): objeto citado uma vez ainda casa. O canal léxico (BM25) sobre o mesmo texto somou **+0,049** de nDCG@10 no split `object` (0,276 → 0,325), depois que o teto de z-score parou de deixá-lo instável.
5. **Entendimento de consulta via LLM (Groq, grátis).** Um modelo pequeno (`openai/gpt-oss-20b`, via Groq) classifica a consulta (objeto, pessoa ou genérica) antes da busca. Consulta de objeto ganha peso maior no canal léxico de enredo (só nela) e desliga o canal de personagem (achado: um termo acrescentado tipo "Dodge Charger" virava falso-positivo lá). **Nunca substitui** o texto da busca, só acrescenta termo. Uma tentativa de encolher a consulta chegou a quebrar um caso que já funcionava, revertida no mesmo dia.
6. **Reranking via LLM: lê a sinopse e julga.** Todo canal acima é *similaridade* (vetor ou termo); nenhum lê o candidato e confere se o fato citado está mesmo ali. O reranking manda o top-30 da fusão pro Groq, que promove pro #1 só quando confirma o fato com confiança, nunca reordena o resto. Subiu o split `hard` de nDCG@10 **0,473 → 0,506**; quando a sinopse genuinamente não tem o fato, recusa em vez de inventar. Achado no caminho: a mesma consulta repetida dava resposta diferente, porque a Groq **não é perfeitamente determinística mesmo com `temperature=0`**; corrigido com cache por consulta (`data/tmdb_cache/rerank_llm.json`).

Essa mesma narrativa (com os números ao vivo, lidos de `eval/results/` a cada deploy) está na aba **Engenharia** do site: [`GET /engineering`](https://cinerd.davispecia.com.br/engineering). Decisão e trade-offs de colocar um LLM externo no caminho crítico da busca: [`docs/adr/0003-llm-in-the-loop.md`](docs/adr/0003-llm-in-the-loop.md).

---

## 📊 Tabela de ablação por sinal

> 🌐 **Ao vivo no site:** a aba **Engenharia** (`GET /engineering`) renderiza esta tabela, o orçamento de latência do encoder, o protocolo de avaliação e a **latência por etapa do tráfego real** — lendo os JSON de `eval/results/` a cada deploy, sem número copiado à mão.

Cada sinal **isolado** vs. a **fusão** (8 sinais: lexical, sinopse, tema, personagem, enredo em 2 formas, trivia de pessoa, nome + prior), split de teste, 47 consultas *held-out*:

| Pipeline | nDCG@10 | MRR | Recall@10 | Recall@50 | mediana |
|---|---|---|---|---|---|
| BM25 puro *(lexical)* | 0,360 | 0,316 | 0,53 | 0,66 | #7 |
| Só embedding *(semântico)* | 0,299 | 0,260 | 0,45 | 0,53 | #25 |
| Só temático *(keywords)* | 0,282 | 0,246 | 0,43 | 0,58 | #32 |
| **Fusão** *(produção)* | **0,829** | **0,790** | **0,96** | **1,00** | **#1** |

**Os sinais são complementares.** Nenhum sozinho passa de nDCG@10 ≈ 0,36; a fusão z-score (com teto de outlier) + prior de popularidade salta para **0,83** e leva a mediana da posição para **#1**. O lexical resgata enredos com termo próprio, o semântico entende paráfrase, o temático pega conceito ("time loop", "memory loss"), o personagem tolera erro de grafia, o enredo da Wikipédia resgata detalhe fora da sinopse curta: juntos cobrem os buracos uns dos outros. O reranking via LLM (Groq) entra **depois** dessa fusão, só em produção. Não está nesta tabela porque o `eval.run` é determinístico/sem rede de propósito; ver [seção anterior](#-como-o-uso-real-evoluiu-o-algoritmo).

### Cross-encoder (2º estágio): **desligado em produção**

O re-ranker cross-encoder era o default. A varredura do tamanho do pool (`python -m eval.run --sweep-rerank`) mostra que ele **não compensa** — split de teste:

> Tabela abaixo medida **antes** do teto de z-score e dos canais de enredo/personagem (baseline "0, desligado" era 0,733 nDCG@10; hoje 0,829 com a fusão atual). A conclusão relativa não mudou: medição fresca contra a fusão atual dá **0,829 → 0,830** a 694 ms vs 18 ms (~40×), a mesma margem dentro do ruído. Regenerar com `python -m eval.run --sweep-rerank` fica pendente para revalidar os números absolutos.

| pool cross-encoder | nDCG@10 | MRR | Recall@10 | Recall@50 | latência p50 | p90 |
|---|---|---|---|---|---|---|
| **0 (desligado)** *(produção, na época)* | 0,733 | 0,686 | 0,89 | 0,94 | **7 ms** | 8 ms |
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

> Medido **antes** do teto de z-score e dos canais de enredo/personagem (a coluna `nDCG@10` usa o baseline antigo, 0,733; hoje a fusão está em 0,829). A leitura relativa entre `e5-large`/INT8/`e5-small` (latência, RSS, custo de qualidade) não depende disso e segue valendo. Regenerar para números absolutos atualizados.

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

## 🧩 Consultas difíceis — onde o retrieval tem teto

### `hard`: descrição deliberadamente oblíqua

O split **`hard`** (`eval/datasets/queries.jsonl`, `source = v3-hard`) tem **30 descrições deliberadamente oblíquas** de filmes famosos: sem título, sem nome próprio, **sem o termo que define o filme** (nada de "anel", "hobbit", "dinossauro", "replicante"). É o pior caso — consulta genérica cujo vocabulário não bate com a sinopse.

| Pipeline | nDCG@10 | MRR | Recall@10 | Recall@50 | mediana |
|---|---|---|---|---|---|
| BM25 puro | 0,165 | 0,144 | 0,27 | 0,40 | #246 |
| **Fusão** *(produção)* | **0,473** | **0,454** | **0,57** | **0,77** | **#3** |
| Fusão + PRF (Rocchio) | 0,447 | 0,413 | 0,57 | 0,63 | #3 |

**Leitura honesta:**
- A fusão **não é ruim mesmo aqui**: o filme certo fica no **top-3 em 16/30**, no top-10 em 17/30, no top-50 em **23/30**. O caso do usuário ("grupo destrói objeto poderoso, guardião minúsculo" → *Senhor dos Anéis*, #18) é real, mas **pontual**: um punhado de consultas onde a descrição é toda de palavras genéricas que caem num bairro semântico lotado, como *Matrix* (#891), *Toy Story* (#476) e *Coringa* (#173). As outras 27 a fusão acha bem.
- **Nenhuma alavanca de similaridade "mais esperta" ajuda**: o cross-encoder ([acima](#cross-encoder-2º-estágio-desligado-em-produção)) não melhora de forma confiável; o **PRF/Rocchio** (`fusion_prf`, realimenta o centroide do top-K na consulta, só numpy sem LLM) **piora** um pouco: em consulta genérica o top-K inicial já está errado, e realimentá-lo puxa a consulta pro cluster errado (*query drift*).
- **O que ajuda é ler, não comparar vetor.** O reranking via LLM (Groq lê a sinopse do top-30 e confere o fato) é a única técnica que sobe esse split de forma real: 0,473 → 0,506. Não está na tabela como linha completa porque é medido fora do `eval.run` (chamada real de rede); ver [seção de evolução](#-como-o-uso-real-evoluiu-o-algoritmo).
- **A régua também mostra o caminho barato de produto**: adicionar **um** detalhe à consulta resolve. *"...joga um anel num vulcão"* → LOTR #3; *"...hobbit... terra-média"* → #1. Nas consultas de fraseado normal (`test`/`dev`) a fusão faz nDCG@10 **0,83–0,88**.

Rodar: `python -m eval.run --split hard --pipelines bm25,fusion,fusion_prf`.

### `object`: objeto/veículo específico citado

O split **`object`** (42 consultas, `source = v3-object`) veio direto do log do admin: descrição que cita **objeto ou veículo específico** ("dodge charger preto", "skyline azul e prata") em vez de tema/enredo. É o pior tipo de consulta pro retrieval baseado em sinopse, porque a sinopse da TMDB quase nunca menciona objeto.

| Pipeline | nDCG@10 | MRR | Recall@10 | Recall@50 | mediana |
|---|---|---|---|---|---|
| BM25 puro | 0,132 | 0,114 | 0,21 | 0,33 | #5761 |
| Só embedding | 0,108 | 0,085 | 0,19 | 0,21 | #1954 |
| **Fusão** *(produção, canal de enredo Wikipédia ligado)* | **0,325** | **0,287** | **0,48** | **0,62** | **#13** |
| Fusão + PRF (Rocchio) | 0,223 | 0,189 | 0,36 | 0,48 | #51 |

- **Sem o enredo da Wikipédia isso era pior ainda**: antes do canal léxico sobre o texto da Wikipédia, a fusão neste split ficava em nDCG@10 0,276; o canal somou **+0,049**. É o split que mais empurrou a construção dos canais de enredo.
- **Ainda é o teto mais baixo de todos os splits.** Mediana **#13** (contra #1 no `test`/`dev`) e top-3 em só **14/42**. A sinopse curta simplesmente não cita objeto na maioria dos filmes; não tem sinal pra recuperar, por mais que se fundam canais.
- **É aqui que o LLM entra como paliativo, não solução**: classificar a consulta como "objeto" e dar peso maior ao canal léxico de enredo (e desligar o canal de personagem, que gerava falso-positivo com o termo do objeto) ajuda pontualmente, mas não fecha a lacuna. O gargalo é a **cobertura de dado** (a Wikipédia às vezes também não cita o objeto), não o algoritmo de fusão.

Rodar: `python -m eval.run --split object --pipelines bm25,embedding,fusion,fusion_prf`.

---

## 📈 Pipeline de Dados

1.  **Coleta:** metadados dos filmes (sinopses, gêneros, elenco) vêm da TMDB; os ratings reais alimentam o modelo colaborativo.
2.  **Indexação:** `retrieval/index_builder.py` gera os índices de busca (BM25 + embeddings).
3.  **Treino:** `recommender/train.py` ajusta o SVD nos ratings reais e serializa os fatores latentes.
4.  **Avaliação:** `python -m eval.run` mede o SRI no split de teste e versiona o resultado em `eval/results/`.
5.  **Entrega:** o motor híbrido combina busca e recomendação para responder em tempo real na interface.

---

## 🏗️ Produção

### Dois serviços (Docker Compose)

```bash
dvc pull                 # baixa o índice + pesos (ver seção 2)
docker compose up --build
```

| serviço | o quê | porta |
|---|---|---|
| **api** | Flask — rotas HTTP, validação, pôsteres, rate limit | `8000` |
| **inference** | FastAPI — a camada de ML (busca por sinopse + recomendação), contrato tipado Pydantic + OpenAPI | `9000` → `/docs` |
| **prometheus** | coleta as métricas dos dois | `9090` |
| **grafana** | dashboard "RecomendAI" (4 painéis), login anônimo | `3000` |

A `api` chama a `inference` por HTTP quando `RECOMENDAI_INFERENCE_URL` está setado (`core/inference_client.py`); sem isso, roda tudo no mesmo processo (modo dev). **Essa costura é o ponto de corte** para reimplementar a `inference` em outra linguagem (ex.: Rust) medindo só a diferença — o contrato JSON não muda. Migração da `api` para FastAPI: [`docs/adr/0001-migracao-fastapi.md`](docs/adr/0001-migracao-fastapi.md) (adiada, com plano).

### Observabilidade (`/metrics`, Prometheus, Grafana)

`core/metrics.py` expõe, nos dois serviços:

| métrica | o quê |
|---|---|
| `recomendaai_stage_seconds{stage}` | latência por etapa — `retrieval`, `rerank`, `total` |
| `recomendaai_request_seconds{endpoint}` · `recomendaai_requests_total{status}` · `recomendaai_errors_total` | latência ponta-a-ponta, throughput, taxa de erro |
| `recomendaai_query_cache_events_total{result}` | hit/miss do cache de embedding da consulta |
| `recomendaai_tmdb_calls_total{result}` | `ok` / `error` / `capped` |
| `recomendaai_process_rss_bytes` | memória residente |

Além do histograma, `metrics.stage_percentiles()` mantém um reservatório dos últimos ~1000 tempos por etapa → p50/p95/p99 exatos do tráfego real, servidos em `GET /engineering` e renderizados na aba **Engenharia** do site (não precisa de scrape do Prometheus para a vitrine).

### Contas & segurança

Login por **e-mail + senha** com **Argon2id** (rehash automático quando os parâmetros mudam), sessão em cookie assinado (Flask-Login) e **CSRF** em toda requisição que altera estado (header `X-CSRFToken`; o frontend busca em `GET /auth/csrf`). Fluxo completo: cadastro, verificação de e-mail, reset de senha e exclusão de conta (`core/users.py`, `core/auth_routes.py`, `core/security.py`). Decisões e trade-offs: [`docs/adr/0002-auth-hardening.md`](docs/adr/0002-auth-hardening.md).

| superfície | tratamento |
|---|---|
| senha em repouso | **Argon2id**; nunca hash rápido |
| cookies (sessão **e** *remember*) | `HttpOnly` + `SameSite=Lax` sempre; `Secure` automático em produção (`RECOMENDAI_COOKIE_SECURE` força/desliga) |
| segredo de assinatura | fonte única (`core/security.py`); **fatal no boot** se `RECOMENDAI_ENV=production` sem `SECRET_KEY`; em dev, persistido em `data/.secret_key` |
| recuperação de senha | `/auth/forgot` responde igual exista ou não o e-mail (sem enumeração); link amarrado ao hash da senha → **uso único** |
| verificação de e-mail | link de uso único; grava dado pessoal só com e-mail confirmado (quando há SMTP) |
| login | resposta em tempo ~constante (verify contra hash-dummy quando o e-mail não existe) |
| rate limit `/auth/*` | login 10/min · cadastro 5/h · forgot 5/h · reset 10/h |
| exclusão (LGPD) | `POST /auth/delete` re-autentica e apaga avaliações + listas da conta |
| acesso / portabilidade (LGPD) | `GET /auth/export` baixa conta + diário + listas em JSON |
| consentimento | cadastro exige aceite da [política de privacidade](/privacidade) (`accepted_privacy`) |

- **E-mail**: SMTP por env (`SMTP_HOST`…); sem provedor, o app **imprime o link no stdout** — dá para testar tudo sem configurar nada.
- **Multi-usuário**: diário e listas são por conta (`data/user.db`, migração automática de bancos single-user → `LEGACY_USER_ID=1`; reassocie com `python -m core.users claim <email>`). As *versões* de filme são curadoria compartilhada.
- **CLI**: `python -m core.users create|verify|passwd|claim`.
- **Política de privacidade**: `GET /privacidade` (página server-rendered, LGPD).
- **Painel admin**: `GET /admin` — allowlist `RECOMENDAI_ADMIN_EMAILS` (vazio = ninguém; não-admin recebe **404**). Consulta contas, diário/listas de cada usuário, agregados; ações: desativar / reativar / apagar conta / reenviar verificação. Nunca expõe hash de senha.

### Limites

- **Rate limit** (Flask-Limiter): `/search` a **30/min** por IP, endpoints pesados (`/similar`, `/recommend*`, `/explore/essentials`) a **12/min**. Configurável (`RECOMENDAI_RATE_SEARCH`, `RECOMENDAI_RATE_HEAVY`). Storage `memory://` vale **por processo** — a API roda `gunicorn -w 1 --threads 8` (ML pesado fica no serviço `inference`); para escalar, aponte `RATELIMIT_STORAGE_URI` para Redis e volte a subir os workers.
- **Teto de chamadas à TMDB**: `RECOMENDAI_TMDB_MAX_RPM` (240, janela deslizante de 60 s) no chokepoint `core/tmdb.py::_get` — ao estourar, degrada para placeholder/fuzzy, como já faz sem rede.

### CI — portão de qualidade

`.github/workflows/ci.yml` (a cada PR): **ruff** (`check` + `format`), **mypy** (checagem gradual — módulos novos), **pytest** (smoke hermético, sem modelo/rede).

`.github/workflows/eval-gate.yml` (**segunda 06:00 UTC + sob demanda** via "Run workflow"): `dvc pull` do índice **e5-large** + catálogo e `python -m eval.run --split dev --gate-ndcg 0.78` — **falha o build se o nDCG@10 da fusão cair abaixo do limiar**. Requer os secrets `DVC_ACCESS_KEY_ID` / `DVC_SECRET_ACCESS_KEY`.

> **Antes de um PR que mexa em `retrieval/` ou `eval/`**, rode o portão local (~1 min, índice e5-small):
> ```bash
> RECOMENDAI_INDEX_DIR=retrieval/index_e5small python -m eval.run --split dev --fast --gate-ndcg 0.65
> ```
> Um portão automático **por PR** exigiria `dvc pull` do índice + catálogo (~1,3 GB) a cada run — não cabe no free tier do B2 (1 GB de download/dia). O gate semanal cobre a `main`; a mini-fixture (catálogo reduzido commitado) é o caminho para um gate por-PR sem billing, se um dia for necessário.

---

## 📚 Proveniência dos Dados

| dado | fonte | licença / termos | onde entra |
|---|---|---|---|
| Metadados de filmes (sinopse, gêneros, elenco, *keywords*, pôster) | **API da TMDB** | [Termos de uso da API TMDB](https://www.themoviedb.org/api-terms-of-use) — uso permitido com atribuição; **este produto não é endossado nem certificado pela TMDB** | catálogo, busca, ficha |
| Nota e nº de votos de filmes (`imdb_rating`, `imdb_votes`) | **[IMDb Non-Commercial Datasets](https://developer.imdb.com/non-commercial-datasets/)** (`title.ratings.tsv.gz`) | Uso **pessoal e não-comercial** apenas; sem redistribuição. Baixado por `core/enrich.py` | ranking de "Essenciais" |
| Crítica (`metascore`, `rt_score`) | **[OMDb API](https://www.omdbapi.com/)** (agrega Metacritic / Rotten Tomatoes) | Termos do OMDb; requer `OMDB_API_KEY` | ranking de "Essenciais" |
| Cânone (`canon_rank`) | Lista **curada** no repositório (`core/enrich.py::CANON`) — base Sight & Sound 2022 + clássicos de consenso | Curadoria própria | ranking de "Essenciais" |
| **~31 M de avaliações** que treinam o SVD colaborativo | **MovieLens ml-32m** (GroupLens, out/2023) — reconstruído por [`recommender/ingest_movielens.py`](recommender/ingest_movielens.py), reconciliação `movieId → tmdbId` pelo **`links.csv` oficial** | **GroupLens: uso NÃO-comercial sem permissão** — [termos](https://files.grouplens.org/datasets/movielens/ml-25m-README.html). Cite Harper & Konstan (2015). | recomendação colaborativa |
| Notas do usuário (diário, listas) | *Do próprio usuário* (por conta, `data/user.db`) | Dado do usuário; não é redistribuído | recomendação a partir do perfil |

### Ratings do recomendador — reconstruídos e documentados

Os ~2,9 M de ratings originais **vieram sem proveniência** no `movies.db`. Foram **substituídos** por uma base rastreável:

```bash
python -m recommender.ingest_movielens   # baixa o ml-32m, reconcilia pelo links.csv, grava em movies.db
python -m recommender.train               # retreina o SVD
```

`data/ratings_provenance.json` registra fonte, versão, MD5 conferido, contagens e licença. Resultado: **30,9 M avaliações · 200,9 k usuários · 19,7 k filmes** (vs. 2,9 M / 19,8 k / 15,2 k antes) — ~10× mais dado e cobertura maior.

> ⚠️ **Licença**: ml-32m é do **GroupLens** e **proíbe uso comercial/"revenue-bearing" sem permissão** (basta um e-mail a um docente do GroupLens/UMN — costumam liberar para projetos sem receita). O `files.grouplens.org` está com **certificado TLS expirado**; o script baixa com `verify=False` e **valida o MD5** publicado. **Antes de monetizar**: obter a permissão do GroupLens **ou** migrar para um dataset com licença compatível.

### Validação de schema (Pandera)

```bash
python -m core.validate            # checa e escreve data/quality_report.json
python -m core.validate --strict   # exit != 0 se algum schema falhar (para CI)
```

Schemas em [`core/schemas.py`](core/schemas.py): **catálogo** (tipos, faixas — `vote_average` 0–10, `release_year` 1870–2100, `tmdb_id` único…), **dump IMDb** (`tconst` = `^tt\d+$`, `averageRating` 1–10) e as **linhas reconciliadas** catálogo × IMDb. `core/enrich.py` roda a validação do dump durante a ingestão e grava o relatório ao final.

**Taxa de match atual** (`data/quality_report.json`): `imdb_id` resolvido em **99,9 %** dos filmes, nota IMDb em **99,7 %**, cânone 100/100. Os poucos casos que falham são curtas/filmes de TV/raridades fora do `title.ratings` do IMDb — listados no relatório.
