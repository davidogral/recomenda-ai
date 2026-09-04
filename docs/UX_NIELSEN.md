# UX/UI do Cinerd — contrato de qualidade

Este documento é a referência compartilhada para mudanças de interface feitas por
Davi, Claude ou outro colaborador. A intenção é impedir que correções locais
reintroduzam sobreposição, alvos pequenos ou fluxos sem feedback.

## Princípios de Nielsen aplicados

| Heurística | Regra prática no Cinerd | Implementação atual |
|---|---|---|
| Visibilidade do estado | Toda ação assíncrona deve dizer o que está acontecendo e qual foi o resultado. | Regiões `.status` usam `role="status"`, `aria-live="polite"` e loaders preservam a forma dos resultados. |
| Correspondência com o mundo real | Usar vocabulário de cinema e ações reconhecíveis, não termos internos do motor. | “Buscar”, “Parecidos”, “Diário”, “Onde assistir” e exemplos dentro do contexto. A aba Engenharia concentra os detalhes técnicos. |
| Controle e liberdade | Sobreposições sempre têm saída visível, `Escape` e retorno ao ponto anterior. | Fechar permanece visível durante a rolagem; modais restauram o foco e o scroll da página só é liberado quando a pilha termina. |
| Consistência e padrões | O mesmo tipo de ação mantém tamanho, cor, foco e comportamento em todas as telas. | Tokens em `:root`, botões com alvo mínimo e estados de foco compartilhados. |
| Prevenção de erros | Restrições devem aparecer antes do envio e ações destrutivas exigem confirmação. | Campos usam tipos/regras nativas; excluir listas, versões, itens e contas pede confirmação. |
| Reconhecimento em vez de memorização | Exemplos, rótulos e sugestões devem estar próximos da ação. | Campos têm rótulo acessível, autocomplete e microcopy contextual; placeholders são exemplos, não o único rótulo. |
| Flexibilidade e eficiência | Toque, mouse e teclado devem completar os fluxos principais. | Cartões acionáveis, listas, sugestões e nota em estrelas são alcançáveis por teclado. |
| Estética minimalista | Priorizar decisão e conteúdo; detalhes secundários não devem competir com a ação principal. | Hierarquia de superfície, CTA vermelho único e cards com texto truncado de forma previsível. |
| Recuperação de erros | Erros devem ser legíveis, próximos da ação e preservar o que a pessoa digitou. | Respostas aparecem nas regiões de status sem limpar entradas; modais permanecem abertos para correção. |
| Ajuda e documentação | Ajuda curta deve existir no contexto, com aprofundamento opcional. | Tour reabrível por `?`, dicas por tela e explicação técnica isolada em Engenharia. |

## Contrato responsivo e acessível

- Largura de referência: validar pelo menos 320, 360, 390, 768, 1024 e 1440 px.
- Nenhuma tela pode produzir rolagem horizontal no `body`. Tabelas e faixas com
  conteúdo intrinsecamente largo devem ter um contêiner de rolagem próprio.
- Em telas de até 768 px, campos usam fonte de 16 px para não acionar zoom
  automático no Safari/iOS.
- Ações frequentes e destrutivas usam alvo mínimo de 44 × 44 px. Campos usam
  altura mínima de 48 px.
- Respeitar `env(safe-area-inset-*)`, `100dvh` e `prefers-reduced-motion`.
- Um placeholder é apenas exemplo. Todo campo precisa de `<label>` visível ou,
  quando o contexto visual já funciona como rótulo, `aria-label` explícito.
- Todo controle apenas visual precisa de nome acessível. Não depender de
  `title`, cor, hover ou emoji para comunicar a ação.
- Modais devem usar `role="dialog"`, `aria-modal="true"`, nome acessível, foco
  inicial, contenção de `Tab`, fechamento por `Escape` e restauração do foco.
- Conteúdo carregado ou erro assíncrono deve ser anunciado em uma região viva
  sem roubar o foco.

## Decisões da rodada mobile v1

Problemas confirmados no código antes desta rodada:

1. `.auth-bar` era absoluta e podia cobrir a marca em telas estreitas.
2. A navegação quebrava em várias linhas dentro de uma área `sticky`.
3. Botões de fechar, apagar, ordenar e editar tinham entre 24 e 34 px.
4. Modais usavam `vh`, perdiam altura com as barras do navegador móvel e o
   botão de fechar rolava junto com o conteúdo.
5. Campos com `min-width: 240px` podiam forçar estouro em viewports pequenos.
6. Cartões e estrelas dependiam de mouse/clique e não expunham estado ao teclado.
7. Mensagens visuais de carregamento/erro não eram anunciadas por tecnologia
   assistiva.

As correções vivem em um bloco identificado no fim de `frontend/style.css`,
além dos contratos semânticos em `frontend/index.html` e `frontend/app.js`.
Mantenha os tokens e os breakpoints existentes; não crie correções isoladas com
valores menores de alvo ou novos `z-index` sem verificar a pilha de modais.

## Decisões da remodelação visual v2

A rodada v1 estabilizou interação e responsividade, mas preservou quase toda a
aparência anterior. A v2 altera deliberadamente a apresentação sem trocar a
stack nem aumentar o JavaScript crítico:

- a busca virou o foco editorial da primeira tela, com título, orientação curta
  e uma única ação primária;
- diretor, ator, gênero, idioma e período passaram para divulgação progressiva
  em “Refinar busca”, reduzindo carga visual sem remover poder;
- Engenharia saiu da navegação principal e continua acessível no contexto da
  busca e no rodapé;
- a grade ficou mais densa e cinematográfica, mostrando mais pôsteres por tela e
  reduzindo o peso de metadados secundários;
- cabeçalho, superfícies, tipografia, contraste, sombras e ritmo passaram a usar
  uma direção visual única, ainda reconhecível como Cinerd;
- o tour deixou de interromper automaticamente a primeira visita e permanece
  disponível no botão de ajuda.

Essas decisões aplicam estética minimalista, reconhecimento em vez de memória e
controle do usuário. Filtros selecionados nunca são descartados ao recolher o
painel de refinamento.

## Auditoria completa v3

A v3 substitui componentes que ainda dependiam de expansão vertical ou
reposicionamento frágil e inclui explicitamente o painel administrativo. O
contrato foi conferido em 320, 360, 390, 768, 1024 e 1440 px conforme a tela.

| Heurística de Nielsen | Evidência implementada |
|---|---|
| Visibilidade do estado | contagem e resumo de filtros/streamings, estados de busca anunciados e aba ativa com `aria-selected` no admin |
| Correspondência com o mundo real | tarefas nomeadas como Buscar, Onde assistir, Parecidos, Diário e Listas, com descrições orientadas à intenção |
| Controle e liberdade | fechar por botão, fundo ou Escape; limpar filtros; streaming opcional; guia nunca abre sozinho |
| Consistência e padrões | mesmos tokens, alvos de 44 px, cabeçalhos, cartões, diálogos e estados ativos na home e no admin |
| Prevenção de erros | filtro “só nos meus serviços” desabilitado sem serviço escolhido e ações destrutivas do admin continuam confirmadas |
| Reconhecimento em vez de memória | menus descrevem cada destino; filtros e serviços selecionados ficam resumidos no acionador |
| Flexibilidade e eficiência | Enter executa busca, teclado opera contas e modais, e a busca leva diretamente ao início dos resultados |
| Estética minimalista | filtros e catálogo de streamings saíram do fluxo vertical; botão `?` e spotlight móvel foram removidos |
| Recuperação de erros | regiões de status preservam mensagens acionáveis sem apagar silenciosamente o contexto da pessoa |
| Ajuda e documentação | guia fixo de sete etapas cobre busca, filtros, streaming, descoberta, Letterboxd, conta e transparência |

### Comportamento responsivo verificado

- em até 768 px, a navegação principal é uma barra inferior de quatro destinos,
  com menus em folha acima dela e espaço seguro no fim da página;
- filtros e streamings usam diálogos com foco contido e folha inferior no
  celular, sem empurrar ou sobrepor o formulário;
- cada aba tem título e contexto próprios; controles internos usam grades que
  colapsam sem rolagem horizontal;
- no admin, KPIs reorganizam a grade, subabas passam de quatro para duas colunas
  e cada linha da tabela vira um cartão legível e acionável por teclado;
- tabelas analíticas largas mantêm rolagem dentro da própria região, sem alargar
  o documento.

## Checklist antes de merge

### Automático

```bash
node --check frontend/app.js
python -m pytest -q
ruff check .
mypy --config-file mypy.ini
git diff --check
```

O teste `tests/test_frontend_contract.py` protege o viewport, cache busting,
regiões de status, nomes de campos, semântica dos modais e tokens responsivos.

### Manual — navegador real

Em cada largura móvel de referência:

1. Entrar/criar conta sem a área de autenticação cobrir marca ou texto.
2. Percorrer toda a navegação, abrir/fechar cada menu e chegar ao conteúdo.
3. Focar todos os campos; o layout não deve ampliar nem deslocar lateralmente.
4. Buscar, abrir uma ficha, abrir Parecidos sobre a ficha e fechar na ordem
   inversa; o foco deve voltar para a ação que abriu cada camada.
5. Avaliar por toque e por teclado; coração e nota devem expor o estado.
6. Criar lista, mover e remover itens; todos os alvos devem ser confortáveis.
7. Rotacionar retrato/paisagem e repetir com teclado virtual aberto.
8. Testar zoom de texto em 200% e “Reduzir movimento”.

Browsers mínimos da verificação manual: Safari/iOS atual, Chrome/Android atual e
um navegador desktop com teclado. Registre no PR os dispositivos/emuladores e
larguras efetivamente testados.

## Próximas rodadas recomendadas

- Medir tarefas reais no analytics: sucesso de busca, reformulação, CTR por
  posição, abertura de ficha e conclusão de cadastro/lista.
- Executar auditoria automatizada com axe/Lighthouse em CI e orçamento de
  performance móvel (LCP, INP e CLS).
- Substituir `prompt`/`confirm` por diálogos consistentes e acessíveis.
- Evoluir o autocomplete para o padrão ARIA combobox completo, com navegação
  por setas e anúncio da opção ativa.
- Fazer testes moderados com 5 pessoas nos fluxos “lembrar um filme”, “achar
  parecidos” e “montar lista”; priorizar falhas observadas, não gosto pessoal.

## Como colaborar sem conflito

- Uma branch e um objetivo de UX por PR; commits curtos no padrão semântico já
  usado no repositório (`fix(frontend): ...`, `test(frontend): ...`).
- Atualizar este documento quando uma decisão alterar o contrato.
- Não misturar redesign com mudanças de ranking/API no mesmo PR.
- Preservar alterações não relacionadas já presentes no worktree.
- Só fazer merge com CI verde e com a matriz manual relevante registrada no PR.
