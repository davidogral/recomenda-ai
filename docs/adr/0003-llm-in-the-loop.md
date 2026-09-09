# ADR 0003 — LLM (Groq) no caminho da busca: entendimento de consulta e reranking

- **Status:** aceito, implementado
- **Data:** 2026-09-09
- **Decisão de:** Davi

## Contexto

Depois do teto de z-score (`RECOMENDAI_ZSCORE_CLIP=8`) e dos canais de
personagem (fuzzy) e enredo da Wikipédia (léxico + MaxSim), o retrieval subiu
nos 5 splits, mas duas classes de consulta real (lidas do painel admin)
continuavam com teto baixo:

- **`object`** (objeto/veículo específico citado, ex. "dodge charger preto"):
  nDCG@10 = 0,325 mesmo com todos os canais de similaridade ligados. A
  sinopse, tanto da TMDB quanto da Wikipédia, às vezes simplesmente não cita
  o objeto.
- **`hard`** (consulta deliberadamente oblíqua, sem termo que defina o filme):
  nDCG@10 = 0,473. O candidato certo cai num "bairro semântico" lotado, com
  vizinhos mais citados (ex. *Blade Runner*, *Matrix*).

Todos os canais de fusão (BM25, embedding, temático, personagem, enredo) são
*scores de similaridade*: distância vetorial ou sobreposição de termo. Nenhum
lê o candidato e confere se o fato citado está mesmo ali. É uma categoria de
erro que nenhum ajuste de peso ou canal a mais resolve; precisa de algo que
**leia conteúdo e julgue**, não que compare vetores.

Restrição de custo: sem orçamento para API paga. A **[Groq](https://groq.com)**
oferece camada grátis (`openai/gpt-oss-20b`, 1000 requisições/dia, 8000
tokens/minuto), suficiente pro volume atual do site.

## Decisão

Introduzir dois passos com LLM no pipeline de busca, ambos **opcionais e com
degradação graciosa**: sem `GROQ_API_KEY` no ambiente, a busca funciona
exatamente como antes, só no retrieval por similaridade.

1. **Entendimento de consulta** (`core/query_llm.py::understand`). Roda
   *antes* da busca. Classifica a consulta em `objeto` / `pessoa` / `genérica`
   e extrai pistas (nome em inglês para trivia de pessoa, termos literais para
   objeto). Ativo sempre que há chave (`RECOMENDAI_RERANK_LLM` não afeta este
   passo).
2. **Reranking via LLM** (`core/query_llm.py::rerank_pick`). Roda *depois* da
   fusão. Manda o top-30 pro Groq (candidatos + sinopse **completa**, não a
   versão truncada de 240 caracteres exibida no card), que promove um
   candidato pro #1 só quando confirma o fato citado com confiança. **Nunca**
   reordena o resto da lista. Controlado por `RECOMENDAI_RERANK_LLM` (default
   ligado).

### Regras de design (cada uma nasceu de uma regressão real no protótipo)

| Regra | Por quê |
|---|---|
| Reescrita de consulta é **só de acrescentar termo, nunca substitui** o texto original | Substituir/encolher a consulta quebrou casos que já funcionavam: "Mcquen" perdeu pra "Alexandre McQueen" depois de correção ortográfica, e uma consulta de 16 palavras reduzida a "bar" passou a casar qualquer título que contivesse a substring |
| Override de peso condicional à consulta (`plot_lexical_weight` maior, `entity_weight=0`) só ativa quando `tipo == "objeto"` | Acrescentar termo tipo "Dodge Charger" virava falso-positivo no canal de personagem, tunado pra nome curto e sem filtro pra "isso não é nome de gente" |
| Reranking **recusa** em vez de "chutar" (`"confianca": "baixa"` não promove) | Uma promoção errada é pior que nenhuma promoção; testado em ~40 consultas onde a sinopse genuinamente não tem o fato |
| Toda chamada é cacheada por consulta, ou por consulta + IDs ordenados dos candidatos (`data/tmdb_cache/query_llm.json` e `rerank_llm.json`) | A Groq **não é perfeitamente determinística mesmo com `temperature=0`** (efeito de lote/roteamento na infraestrutura compartilhada); a mesma consulta repetida 8 vezes deu 3 respostas diferentes antes do cache |
| Só resposta **bem-sucedida** é cacheada, nunca falha de rede/timeout/parse | Uma falha da Groq pode ser transitória. Cachear isso travaria um "sem resposta" permanente |
| `eval/` continua determinístico e sem chamada de rede, de propósito | Precisa ser reproduzível e rodar em CI sem custo/latência de API. O impacto do LLM é medido à parte, com script ad hoc contra a Groq real, nunca dentro de `eval/pipelines.py` ou `eval/run.py`. README e aba Engenharia citam os dois números separados: o **piso do retrieval puro** (`eval/results/`) e o que o LLM soma em cima (medição ad hoc) |

### Bugs reais encontrados no caminho

| Bug | Causa | Correção |
|---|---|---|
| Groq retornava conteúdo vazio | Modelo "reasoning" estourava `max_tokens` no pensamento oculto antes de escrever a resposta (`finish_reason: "length"`) | `reasoning_effort: "low"` |
| Modelo tentava adivinhar a identidade da pessoa e entrava em loop na saída de "raciocínio" | Prompt não proibia isso explicitamente | Instrução explícita: "NUNCA escreva esse nome, mesmo reconhecendo quem é" |

## Fora de escopo (não fechado)

- **Reranking com o texto completo da Wikipédia** (em vez da sinopse TMDB).
  Esbarrou no teto de 8000 TPM da Groq mesmo com pool pequeno (5 candidatos ×
  todos os trechos, ~5500 a 9000+ tokens). Caminho futuro: resumo
  pré-computado offline por filme, em vez de uma chamada de tokens grandes
  por busca.
- `consulta_ingles` (campo já decomposto pelo LLM, pensado pra casar contra o
  texto em inglês da Wikipédia) existe no schema mas não está encanado em
  nenhum canal ainda.

## Consequências

- **Positivas:** o reranking é a única técnica medida que sobe o split `hard`
  de forma real (nDCG@10 0,473 → 0,506); nenhuma alavanca de similaridade
  (cross-encoder, PRF/Rocchio) conseguiu isso. Entendimento de consulta reduz
  falso-positivo em consulta de objeto sem arriscar regressão em consulta
  normal, já que só acrescenta termo. Sem `GROQ_API_KEY`, zero risco de
  deploy: a busca volta a ser exatamente o pipeline anterior.
- **Custo/risco:** dependência de uma API grátis de terceiro. 1000
  requisições/dia e 8000 tokens/minuto são um teto real; tráfego de produção
  precisa ficar de olho em *throttling*. O não-determinismo da Groq é
  mitigado pelo cache, mas não eliminado: a resposta cacheada é a primeira
  que veio, que pode não ser a "melhor" entre respostas plausíveis.
- **Latência:** sem impacto no caminho sem LLM (idêntico ao pipeline
  anterior). Com LLM, soma 1 a 2 chamadas de rede por busca, compensado na
  prática pelo cache por consulta/candidatos (a maioria das consultas
  repete).
