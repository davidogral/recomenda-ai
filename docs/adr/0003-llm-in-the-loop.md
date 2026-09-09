# ADR 0003: LLM (Groq) no caminho da busca (entendimento de consulta e reranking)

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
2. **Reranking via LLM** (`core/query_llm.py::rerank_confirm`). Roda *depois*
   da fusão. Manda o top-20 pro Groq (candidatos + sinopse **completa**, não
   a versão truncada de 240 caracteres exibida no card), que devolve uma
   **lista** de candidatos confirmados (pode ser mais de um) e promove todos
   pro topo, na ordem de confiança. **Nunca** reordena o resto da lista.
   Controlado por `RECOMENDAI_RERANK_LLM` (default ligado). Revisão do
   design original (um único palpite) e troca de modelo documentadas na
   seção "Revisão" abaixo.

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
| Reranking perdia candidato certo enterrado no meio da lista (pool de 20 a 30) | `reasoning_effort="low"` fazia o modelo responder pela memória em vez de examinar cada candidato do pool | Ver seção "Revisão" abaixo: troca de modelo, não de esforço de raciocínio |
| Cota diária da Groq (200 mil tokens/dia) esgotada em ~20 chamadas de teste | `reasoning_effort="medium"` custava ~9 a 10 mil tokens por chamada (raciocínio oculto extenso) num modelo de "raciocínio" | Trocar pra um modelo sem raciocínio oculto (ver "Revisão") |

## Revisão (mesmo dia): confirmar uma lista de candidatos, não só um

Depois de medir o design acima em produção, surgiu uma sugestão razoável: já
que o reranking lê a sinopse completa de todos os candidatos do pool mesmo,
por que só promover um? A pergunta veio de um caso real: para a consulta
"sonho dentro do sonho", tanto *A Origem* quanto *O Discreto Charme da
Burguesia* citam a mesma premissa (a segunda tem a frase "sonhos dentro de
sonhos" literal na sinopse), e o design de um único palpite só conseguia
subir um dos dois candidatos por vez.

**Opção descartada: reordenar o pool inteiro.** Pedir pro LLM devolver a
ordem completa dos 20 a 30 candidatos foi cogitado e rejeitado por dois
motivos. Primeiro, é um formato de saída bem mais frágil de validar (índice
fora de ordem, duplicata, item faltando) do que uma lista curta de
confirmações. Segundo, pede pro modelo discriminar entre candidatos onde ele
não tem base real nenhuma pra isso, já que a maioria do pool não tem relação
alguma com a consulta; forçar uma ordem ali é mais ruído do que sinal.

**Decisão: `rerank_confirm` devolve uma lista.** `{"confirmados": [{"n": N,
"confianca": "alta"|"media"}, ...]}` em vez de `{"escolha": N}`. Todos os
confirmados sobem, na ordem de confiança devolvida; o resto da lista mantém
a ordem da fusão. É o mesmo julgamento binário de antes (esse candidato bate
ou não), só que aplicado a cada candidato, em vez de reordenar tudo.

**Achado 1: `reasoning_effort="low"` não escaneava a lista inteira.**
Testando "sonho dentro do sonho" ao vivo contra o pool real, o modelo (ainda
`openai/gpt-oss-20b`) devolvia `confirmados: []`; a *reasoning trace*
mostrava "Inception? None listed", ou seja, ele reconheceu o conceito pela
memória mas não achou o candidato correspondente, porque não chegou a
examinar o texto de cada um dos 20 a 30 candidatos. Subir pra
`reasoning_effort="medium"` resolvia: o modelo varria a lista inteira na
*reasoning trace* e confirmava os dois candidatos certos.

**Achado 2: "medium" custava caro demais pra cota diária.** O raciocínio
oculto de um modelo de raciocínio em "medium" ficou em torno de 9 a 10 mil
tokens por chamada. A cota grátis da Groq pra esse modelo é 200 mil tokens
**por dia**, não por minuto, e é **compartilhada** com o entendimento de
consulta (que roda em toda busca de texto). Em cerca de 20 chamadas de teste
a cota do dia acabou, o que teria quebrado as duas etapas de LLM ao mesmo
tempo em produção.

**Decisão: trocar o modelo do reranking.** `GROQ_RERANK_MODEL =
qwen/qwen3.8-27b`, um modelo sem raciocínio oculto, diferente do
`GROQ_MODEL` usado no entendimento de consulta. Testado contra o mesmo pool:
examinou a lista inteira corretamente (confirmou os dois candidatos certos)
e respondeu em cerca de 1800 a 4000 tokens por chamada, dependendo do
tamanho do pool, sem gastar token nenhum "pensando". Por ser um modelo
diferente, também tem cota diária própria, então as duas etapas de LLM
pararam de competir pelo mesmo orçamento.

**Decisão: pool reduzido de 30 para 20.** Pedido por custo (lista mais curta
é mais barata), mas verificado antes de aplicar: *O Discreto Charme da
Burguesia* estava na posição #19 da fusão pra aquela consulta, então um pool
de 10 ou 15 (cogitados primeiro) excluiria justamente o candidato que
motivou essa revisão. Pool 20 cobre esse caso com uma margem pequena, e já
era historicamente o melhor pool medido na primeira versão do reranking
(`test` nDCG@10 0,829 → 0,844, contra 0,829 sem ganho no pool de 30).

**Resultado medido** (mesmos três splits mais sensíveis a esse tipo de
ambiguidade, script ad hoc contra a Groq real):

| Split | nDCG@10 antes | nDCG@10 depois | mediana antes | mediana depois |
|---|---|---|---|---|
| `hard` | 0,473 | 0,654 | #3 | #1 |
| `object` | 0,325 | 0,468 | #13 | #5 |
| `entity` | 0,778 | 0,853 | #1 | #1 |

O ganho no `object` é bem maior que na primeira versão (que quase não
ajudava esse split): boa parte dele é objeto ou personagem de franquia (o
mesmo carro, boneco ou vilão aparece em várias continuações, ex. "carro
DeLorean" bate com os três filmes de *De Volta para o Futuro*), exatamente
o caso que confirmar uma lista resolve e um único palpite não.

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

- **Positivas:** o reranking é a única técnica medida que sobe os splits
  `hard` (nDCG@10 0,473 → 0,654), `object` (0,325 → 0,468) e `entity` (0,778
  → 0,853) de forma real; nenhuma alavanca de similaridade (cross-encoder,
  PRF/Rocchio) conseguiu isso. Entendimento de consulta reduz falso-positivo
  em consulta de objeto sem arriscar regressão em consulta normal, já que só
  acrescenta termo. Sem `GROQ_API_KEY`, zero risco de deploy: a busca volta
  a ser exatamente o pipeline anterior.
- **Custo/risco:** dependência de duas APIs grátis de terceiro (modelos
  diferentes pra entendimento e reranking, ver "Revisão"). Cada uma tem teto
  próprio de requisições/dia e tokens/dia; tráfego de produção precisa ficar
  de olho em *throttling*, e um modelo de "raciocínio" em esforço alto pode
  esgotar a cota diária em poucas dezenas de chamadas (achado real, ver
  "Revisão"). O não-determinismo da Groq é mitigado pelo cache, mas não
  eliminado: a resposta cacheada é a primeira que veio, que pode não ser a
  "melhor" entre respostas plausíveis.
- **Latência:** sem impacto no caminho sem LLM (idêntico ao pipeline
  anterior). Com LLM, soma 1 a 2 chamadas de rede por busca, compensado na
  prática pelo cache por consulta/candidatos (a maioria das consultas
  repete).
