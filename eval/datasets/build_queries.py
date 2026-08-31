# -*- coding: utf-8 -*-
"""Gera `eval/datasets/queries.jsonl` a partir das listas curadas abaixo.

Este arquivo é a **fonte editável** do conjunto de avaliação (adicione/edite
consultas aqui); o `.jsonl` gerado é o **artefato versionado** que `eval.run`
consome. Rodar depois de qualquer mudança:

    .venv/bin/python -m eval.datasets.build_queries

Cada caso é `(consulta_pt, dica_de_titulo_pt, ano)`. A dica + o ano resolvem
para um `tmdb_id` no catálogo (substring do título PT + ano; `token_set_ratio`
como desempate). O build **falha** se alguma dica não resolver ou colidir, para
o conjunto nunca entrar em avaliação com rótulo quebrado.

Split (semente fixa `SPLIT_SEED`):
- **v1** (52 casos originais do harness): 35 dev / 17 teste — divisão pedida
  pelo orientador. Esses 52 foram usados na calibração dos pesos da fusão, então
  os 17 de teste do v1 são "vistos"; leia-os como continuidade histórica.
- **v2** (novos, nunca usados em calibração): ~2/3 dev, ~1/3 teste — held-out
  de verdade. É neles que os números de teste têm valor de generalização.

Só o split de **teste** é reportado no README/METODOLOGIA.
"""

from __future__ import annotations

import json
import os
import random
import sys

SPLIT_SEED = 20260831
_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(_HERE, "queries.jsonl")

# ---------------------------------------------------------------------------
# v1 — 52 casos originais (retrieval/eval_harness.py). CORE = 12 curados
# (alguns difíceis de propósito); EXT = 40 filmes famosos com paráfrase
# estilo-usuário e baixo overlap lexical com a sinopse real.
# Correções vs. o harness: dois títulos estavam com mojibake e não resolviam
# ("Senhor dos AnÃ©is" -> "Senhor dos Anéis"; "AmÃ©lie" -> "Amélie Poulain",
# que é como o catálogo PT registra o filme).
# ---------------------------------------------------------------------------
CORE_V1 = [
    ("um homem incapaz de formar novas memórias caça o assassino da esposa usando fotos e tatuagens", "Amnésia", 2000),
    (
        "uma família pobre se infiltra trabalhando na casa de uma família rica escondendo que são parentes",
        "Parasita",
        2019,
    ),
    (
        "um escritor enlouquece cuidando de um hotel isolado e vazio no inverno com a família na neve",
        "O Iluminado",
        1980,
    ),
    ("um tubarão gigante aterroriza uma cidade praiana atacando banhistas no verão", "Tubarão", 1975),
    ("um jovem baterista é levado ao limite por um maestro tirânico numa escola de música", "Whiplash", 2014),
    ("dois ilusionistas rivais obcecados pelo segredo de um truque de teletransporte", "O Grande Truque", 2006),
    ("dois detetives caçam um assassino que mata pelos sete pecados capitais", "Seven", 1995),
    (
        "um programador testa uma robô com inteligência artificial na mansão de um bilionário recluso",
        "Ex_Machina",
        2015,
    ),
    ("um filme mudo de um homem com uma câmera filmando a cidade", "Um Homem com uma Câmera", 1929),
    (
        "um homem comum descobre que toda a sua vida é um programa de televisão e que todos ao seu redor são atores",
        "O Show de Truman",
        1998,
    ),
    (
        "após um término doloroso um casal apaga da mente as lembranças um do outro num procedimento",
        "Brilho Eterno",
        2004,
    ),
    ("um repórter fica preso revivendo o mesmo dia de inverno repetidas vezes", "Feitiço do Tempo", 1993),
]

EXT_V1 = [
    (
        "um hacker descobre que a realidade é uma simulação controlada por máquinas e aprende a dobrar suas regras",
        "Matrix",
        1999,
    ),
    ("um homem insone forma um clube secreto de brigas com um vendedor de sabão carismático", "Clube da Luta", 1999),
    ("dois meninos crescem numa favela violenta; um vira fotógrafo e o outro chefe do tráfico", "Cidade de Deus", 2002),
    (
        "um adolescente viaja ao passado num carro modificado e precisa fazer seus pais se apaixonarem",
        "De Volta para o Futuro",
        1985,
    ),
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
    (
        "um peixe-palhaço atravessa o oceano para reencontrar o filho capturado por mergulhadores",
        "Procurando Nemo",
        2003,
    ),
    ("um robozinho solitário que limpa o lixo de uma terra abandonada se apaixona por outro robô", "WALL", 2008),
    (
        "um policial infiltrado na máfia e um criminoso infiltrado na polícia tentam se desmascarar",
        "Os Infiltrados",
        2006,
    ),
    (
        "uma agente do fbi consulta um canibal preso para capturar outro assassino em série",
        "O Silêncio dos Inocentes",
        1991,
    ),
    ("uma mulher foge com dinheiro roubado e para num motel isolado de um rapaz perturbado", "Psicose", 1960),
    (
        "um jovem violento é submetido a um tratamento que o condiciona a passar mal com a violência",
        "Laranja Mecânica",
        1971,
    ),
    ("uma garçonete tímida decide secretamente transformar a vida das pessoas ao seu redor", "Amélie Poulain", 2001),
    ("um repórter investiga o sentido da última palavra dita por um magnata antes de morrer", "Cidadão Kane", 1941),
    ("um caçador de andróides persegue replicantes fugitivos numa metrópole chuvosa e sombria", "Blade Runner", 1982),
    ("um filhote de leão foge culpado pela morte do pai e mais tarde volta para reclamar o trono", "O Rei Leão", 1994),
    ("um motorista de táxi insone e solitário enlouquece na cidade e planeja um ato violento", "Taxi Driver", 1976),
    ("a ascensão e queda de um rapaz que sonha a vida toda em ser um gângster", "Os Bons Companheiros", 1990),
    ("um rapaz negro visita a família branca da namorada e descobre um plano sinistro", "Corra", 2017),
    (
        "um detetive investiga um sumiço numa ilha-presídio psiquiátrica e duvida da própria sanidade",
        "Ilha do Medo",
        2010,
    ),
    (
        "um empresário alemão salva centenas de judeus empregando-os na fábrica durante o holocausto",
        "A Lista de Schindler",
        1993,
    ),
    ("numa terra desértica pós-apocalíptica uma rebelde foge num caminhão com esposas escravizadas", "Mad Max", 2015),
    (
        "um idoso amarra milhares de balões na casa para voar até uma cachoeira e leva um garoto junto",
        "Altas Aventuras",
        2009,
    ),
    ("uma nave com um computador de inteligência artificial viaja ao espaço e a máquina se rebela", "2001", 1968),
    ("uma bailarina obcecada pela perfeição enlouquece ao assumir um papel duplo de cisne", "Cisne Negro", 2010),
    ("um menino que enxerga pessoas mortas é ajudado por um psicólogo infantil", "O Sexto Sentido", 1999),
    ("um boxeador desconhecido de bairro pobre ganha a chance de lutar pelo título mundial", "Rocky", 1976),
    (
        "dois amigos planejam uma fuga ousada de uma prisão onde um banqueiro foi condenado injustamente",
        "Um Sonho de Liberdade",
        1994,
    ),
    ("um arqueólogo aventureiro corre contra nazistas para achar uma relíquia bíblica poderosa", "Indiana Jones", 1981),
]

# ---------------------------------------------------------------------------
# v2 — novos casos (nunca usados em calibração). Mesma filosofia: filme muito
# conhecido, paráfrase de enredo com baixo overlap lexical com a sinopse real,
# resolve para um único tmdb_id via (dica de título PT, ano).
# ---------------------------------------------------------------------------
NEW_V2 = [
    (
        "histórias entrelaçadas de dois pistoleiros de aluguel, um boxeador e a mulher de um chefão numa Los Angeles decadente",
        "Pulp Fiction",
        1994,
    ),
    (
        "um folgado e seus amigos de boliche se enrolam num sequestro depois que ele é confundido com um milionário de mesmo nome",
        "O Grande Lebowski",
        1998,
    ),
    (
        "um assalto a uma joalheria dá errado e os ladrões sobreviventes se acusam de ter dedurado a polícia num galpão vazio",
        "Cães de Aluguel",
        1992,
    ),
    (
        "uma noiva sai do coma e caça um a um os integrantes do bando que a traiu e a deixou por morta no altar",
        "Kill Bill",
        2003,
    ),
    (
        "um carcereiro do corredor da morte percebe que um preso enorme e gentil tem um dom sobrenatural de cura",
        "A Espera de um Milagre",
        1999,
    ),
    (
        "um encrenqueiro finge doença mental para cumprir pena num hospício e peita a enfermeira que manda na ala",
        "Um Estranho no Ninho",
        1975,
    ),
    (
        "um detetive particular investiga uma traição conjugal e tropeça num esquema criminoso sobre o abastecimento de água da cidade",
        "Chinatown",
        1974,
    ),
    (
        "um oficial sobe um rio na selva durante a guerra com a missão de executar um coronel que criou o próprio culto",
        "Apocalypse Now",
        1979,
    ),
    (
        "um pianista de jazz e uma aspirante a atriz se apaixonam em Los Angeles enquanto os sonhos de cada um cobram seu preço",
        "La La Land",
        2016,
    ),
    (
        "um detetive aposentado com pavor de altura é pago para vigiar a esposa de um amigo e desenvolve uma obsessão doentia",
        "Um Corpo que Cai",
        1958,
    ),
    (
        "um astro do cinema mudo entra em crise quando os estúdios passam a fazer filmes falados",
        "Cantando na Chuva",
        1952,
    ),
    (
        "um garoto esconde no quarto um extraterrestre perdido e tenta ajudá-lo a chamar sua nave para voltar pra casa",
        "E.T.",
        1982,
    ),
    (
        "três cientistas que perderam o emprego na universidade montam uma empresa de captura de fantasmas em Nova York",
        "Os Caça-Fantasmas",
        1984,
    ),
    (
        "a tripulação de um cargueiro espacial responde a um chamado de socorro e passa a ser caçada por uma criatura a bordo",
        "Alien",
        1979,
    ),
    (
        "uma grávida desconfia que os vizinhos idosos e simpáticos pertencem a uma seita interessada no bebê",
        "O Bebê de Rosemary",
        1968,
    ),
    (
        "um plebeu escocês lidera uma rebelião contra a coroa inglesa depois que assassinam a mulher que ele amava em segredo",
        "Coração Valente",
        1995,
    ),
    (
        "um músico judeu se esconde faminto entre as ruínas de Varsóvia enquanto a cidade é destruída na ocupação",
        "O Pianista",
        2002,
    ),
    (
        "um ladrão que invade sonhos para roubar segredos é contratado para plantar uma ideia na cabeça de um herdeiro",
        "A Origem",
        2010,
    ),
    (
        "um justiceiro mascarado enfrenta um criminoso de rosto pintado que só quer ver a cidade mergulhar no caos",
        "Batman: O Cavaleiro das Trevas",
        2008,
    ),
    (
        "o único sobrevivente de uma chacina num navio conta a um investigador a lenda de um mafioso fantasma",
        "Os Suspeitos",
        1995,
    ),
    (
        "quatro pessoas afundam no vício ao longo de um ano e veem seus sonhos desmoronarem",
        "Requiem para um Sonho",
        2000,
    ),
    (
        "um grupo de dependentes de heroína em Edimburgo tenta largar o vício entre pequenos golpes e tragédias",
        "Trainspotting",
        1996,
    ),
    (
        "um pai de família entediado na meia-idade se obceca pela amiga adolescente da filha e resolve largar tudo",
        "Beleza Americana",
        1999,
    ),
    (
        "um roteirista em férias é transportado toda meia-noite para a Paris boêmia dos anos 1920",
        "Meia-Noite em Paris",
        2011,
    ),
    (
        "um cantor famoso e alcoólatra descobre uma garota talentosa num bar e a lança enquanto a própria carreira despenca",
        "Nasce uma Estrela",
        2018,
    ),
    (
        "um corretor da bolsa fica bilionário com fraudes, drogas e festas até o governo cercar seu esquema",
        "O Lobo de Wall Street",
        2013,
    ),
    (
        "um universitário cria um site de relacionamentos que vira febre e é processado pelos amigos que diz ter passado pra trás",
        "A Rede Social",
        2010,
    ),
    (
        "um treinador velho e ranzinza reluta em treinar uma garçonete teimosa que quer ser boxeadora profissional",
        "Menina de Ouro",
        2004,
    ),
    (
        "a queda de um boxeador corroído pelo ciúme que destrói a família com a mesma violência que usa no ringue",
        "Touro Indomável",
        1980,
    ),
    (
        "um imigrante cubano chega sem nada a Miami e sobe até o topo do tráfico de cocaína antes de ser consumido pela ganância",
        "Scarface",
        1983,
    ),
    (
        "um gângster já velho volta ao bairro décadas depois e revisita a amizade e a traição dos tempos de juventude",
        "Era Uma Vez na América",
        1984,
    ),
    ("um recruta novato no Vietnã se vê dividido entre dois sargentos com códigos morais opostos", "Platoon", 1986),
    (
        "recrutas sofrem um treinamento humilhante nos fuzileiros antes de embarcar para a guerra no Vietnã",
        "Nascido para Matar",
        1987,
    ),
    (
        "soldados presos numa praia da França tentam ser resgatados por mar, terra e ar enquanto o cerco aperta",
        "Dunkirk",
        2017,
    ),
    (
        "dois soldados britânicos cruzam o território inimigo numa corrida contra o tempo para entregar uma ordem que evita um massacre",
        "1917",
        2019,
    ),
    ("uma linguista é recrutada para se comunicar com naves ovais que pousaram em vários países", "A Chegada", 2016),
    ("uma astronauta fica à deriva no espaço depois que uma chuva de destroços despedaça a estação", "Gravidade", 2013),
    (
        "um astronauta é dado como morto e abandonado sozinho em Marte e improvisa comida e oxigênio até o resgate",
        "Perdido em Marte",
        2015,
    ),
    (
        "uma cientista capta um sinal de rádio de outra civilização com a planta de uma máquina misteriosa",
        "Contato",
        1997,
    ),
    (
        "refugiados alienígenas vivem confinados num gueto na África do Sul e um funcionário começa a se transformar num deles",
        "Distrito 9",
        2009,
    ),
    (
        "um menino-robô feito para amar atravessa um mundo hostil atrás de uma fada que o tornaria humano",
        "A.I. Inteligencia Artificial",
        2001,
    ),
    (
        "um policial que prende gente antes de o crime acontecer vira fugitivo quando o sistema aponta que ele vai matar alguém",
        "Minority Report",
        2002,
    ),
    (
        "um operário compra a memória de uma viagem de férias e descobre que já foi um agente secreto em Marte",
        "O Vingador do Futuro",
        1990,
    ),
    (
        "um policial morto em serviço é reconstruído como um ciborgue da lei numa Detroit tomada pelo crime",
        "RoboCop",
        1987,
    ),
    (
        "um tira descalço fica preso sozinho num arranha-céu tomado por assaltantes disfarçados de terroristas na véspera de Natal",
        "Duro de Matar",
        1988,
    ),
    (
        "um ônibus é armado com uma bomba que detona se a velocidade baixar de um certo limite",
        "Velocidade Maxima",
        1994,
    ),
    (
        "um grupo de jovens numa van quebra no interior do Texas e cai nas mãos de uma família canibal com uma serra elétrica",
        "O Massacre da Serra Eletrica",
        1974,
    ),
    (
        "um assassino mascarado escapa do manicômio e volta à cidade natal para perseguir babás na noite do Dia das Bruxas",
        "Halloween",
        1978,
    ),
    (
        "um homem desfigurado de chapéu e garras de metal mata os adolescentes de uma rua dentro dos sonhos deles",
        "A Hora do Pesadelo",
        1984,
    ),
    (
        "um grupo de crianças encara um palhaço demoníaco que ressurge a cada vinte e sete anos numa cidade pequena",
        "It: A Coisa",
        2017,
    ),
    ("dois padres tentam expulsar uma entidade que tomou o corpo de uma garota de doze anos", "O Exorcista", 1973),
    (
        "depois da morte da avó uma família começa a ser destruída por uma presença ligada a um culto",
        "Hereditario",
        2018,
    ),
    (
        "uma família em férias na praia é atacada por sósias sinistros idênticos a eles que sobem de túneis subterrâneos",
        "Nós",
        2019,
    ),
    (
        "numa base isolada na Antártida uma criatura que imita perfeitamente qualquer ser vivo espalha paranoia entre os pesquisadores",
        "O Enigma de Outro Mundo",
        1982,
    ),
    (
        "um pelotão de comandos na selva é caçado um a um por um alienígena quase invisível que coleciona crânios",
        "O Predador",
        1987,
    ),
    (
        "um capitão do bope treina um substituto enquanto a polícia reprime o tráfico nos morros do Rio antes da visita do papa",
        "Tropa de Elite",
        2007,
    ),
    (
        "uma ex-professora que escreve cartas para analfabetos numa estação acompanha um menino órfão pelo sertão atrás do pai",
        "Central do Brasil",
        1998,
    ),
    (
        "dois nordestinos espertos aprontam trapaças num vilarejo e acabam sendo julgados diante de Nossa Senhora no céu",
        "O Auto da Compadecida",
        2000,
    ),
    (
        "um povoado no sertão some do mapa e vira alvo de um grupo de estrangeiros armados que caça gente por esporte",
        "Bacurau",
        2019,
    ),
    (
        "a filha adulta de uma empregada doméstica chega de surpresa a São Paulo e bagunça as hierarquias da casa dos patrões",
        "Que Horas Ela Volta?",
        2015,
    ),
    (
        "um médico sanitarista atende presos num presídio superlotado até a polícia invadir e promover um massacre",
        "Carandiru",
        2003,
    ),
    (
        "um agente replicante descobre um segredo capaz de abalar a ordem e sai atrás de um detetive desaparecido há trinta anos",
        "Blade Runner 2049",
        2017,
    ),
    (
        "um homem solitário que escreve cartas de amor para estranhos se apaixona pela assistente virtual do próprio celular",
        "Ela",
        2013,
    ),
    (
        "um adolescente atormentado é avisado por um coelho gigante de que o mundo vai acabar em menos de um mês",
        "Donnie Darko",
        2001,
    ),
    (
        "o único passageiro ileso de um acidente de trem descobre que talvez seja invulnerável graças a um colecionador de quadrinhos",
        "Corpo Fechado",
        2000,
    ),
    (
        "um ator decadente conhecido por um herói de capa aposta tudo numa peça da Broadway para se provar de novo",
        "Birdman",
        2014,
    ),
    (
        "um concierge lendário e seu aprendiz são acusados de matar uma hóspede rica que lhes deixou um quadro valioso",
        "O Grande Hotel Budapeste",
        2014,
    ),
    (
        "um chefe de apostas comanda um cassino de Las Vegas para a máfia enquanto o amigo pavio-curto e a esposa golpista o afundam",
        "Cassino",
        1995,
    ),
    (
        "duas amigas numa viagem de fim de semana viram fugitivas depois de um crime num estacionamento e decidem não voltar",
        "Thelma & Louise",
        1991,
    ),
    (
        "três pistoleiros se enfrentam e se aliam atrás de um baú de ouro enterrado num cemitério durante a guerra civil",
        "Tres Homens em Conflito",
        1966,
    ),
    (
        "um caçador acha uma mala cheia de dinheiro no deserto e passa a ser perseguido por um matador frio com uma pistola de ar",
        "Onde os Fracos Nao Tem Vez",
        2007,
    ),
    (
        "um vendedor de carros endividado contrata dois criminosos trapalhões para sequestrar a própria esposa e uma policial grávida investiga",
        "Fargo",
        1996,
    ),
    (
        "um golpista carismático foge pelo país aplicando cheques falsos como piloto, médico e advogado com um agente do fbi no seu encalço",
        "Prenda-me Se For Capaz",
        2002,
    ),
    (
        "um adolescente é enviado para uma escola militar espacial onde os jogos de guerra escondem um propósito mortal",
        "O Jogo do Exterminador",
        2013,
    ),
    (
        "um dirigente esportivo falido monta um time de beisebol só com estatística, contrariando os olheiros veteranos",
        "O Homem que Mudou o Jogo",
        2011,
    ),
    (
        "um advogado do sul defende um homem negro acusado injustamente enquanto os filhos veem a cidade se voltar contra a família",
        "O Sol e Para Todos",
        1962,
    ),
    (
        "doze jurados se trancam numa sala abafada para decidir a sorte de um rapaz e um deles põe a acusação inteira em dúvida",
        "Doze Homens e uma Sentenca",
        1957,
    ),
    (
        "um preso planeja por anos uma fuga minuciosa de uma ilha-prisão da qual ninguém jamais escapou",
        "Papillon",
        1973,
    ),
    (
        "um oficial britânico se junta à revolta árabe no deserto durante a primeira guerra e se perde no próprio mito",
        "Lawrence da Arabia",
        1962,
    ),
    (
        "uma noviça vira governanta de sete crianças de um capitão viúvo e ensina todas a cantar enquanto a Áustria é anexada",
        "A Novica Rebelde",
        1965,
    ),
    (
        "um caminhoneiro comum é perseguido por uma estrada deserta por um caminhão-tanque enorme cujo motorista nunca aparece",
        "Encurralado",
        1971,
    ),
    (
        "um agente secreto britânico tenta impedir um vilão de contaminar o ouro do deposito nacional americano",
        "007 Contra Goldfinger",
        1964,
    ),
    (
        "um garoto pobre encontra um bilhete dourado numa barra de chocolate e visita a fábrica secreta de um doceiro excêntrico",
        "A Fantástica Fábrica de Chocolate",
        1971,
    ),
    (
        "um menino descobre no aniversário de onze anos que é bruxo e vai estudar num castelo-escola de magia",
        "Harry Potter e a Pedra Filosofal",
        2001,
    ),
    (
        "uma jovem é levada para um castelo e se apaixona pela fera amaldiçoada que a mantém prisioneira",
        "A Bela e a Fera",
        1991,
    ),
    (
        "uma garota cai num país de sonho atrás de um coelho apressado de colete e relógio",
        "Alice no Pais das Maravilhas",
        1951,
    ),
    (
        "um rato que sonha em ser chef controla um cozinheiro atrapalhado puxando seus cabelos num restaurante de Paris",
        "Ratatouille",
        2007,
    ),
    (
        "uma família de super-heróis aposentados por lei é obrigada a voltar à ativa para deter um inventor rancoroso",
        "Os Incriveis",
        2004,
    ),
    (
        "um casal apaixonado vira celebridade da imprensa numa onda de assassinatos pelas estradas do país",
        "Assassinos por Natureza",
        1994,
    ),
    (
        "um menino cresce numa cidadezinha do Texas ao longo de doze anos, entre a separação dos pais e a chegada da vida adulta",
        "Boyhood",
        2014,
    ),
]


# ---------------------------------------------------------------------------
# Resolução (dica de título PT, ano) -> tmdb_id — cópia autocontida da lógica
# do harness, para o pacote eval/ não depender de retrieval/eval_harness.py.
# ---------------------------------------------------------------------------
def resolve_target(title_hint, year):
    from rapidfuzz import fuzz

    from core import catalog

    cat = catalog.get_catalog()
    hint = title_hint.lower()
    candidates = [(tid, mv) for tid, mv in cat.items() if mv.get("release_year") == year]
    subs = [tid for tid, mv in candidates if hint in (mv.get("title") or "").lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        return min(subs, key=lambda t: len(cat[t]["title"] or ""))
    best_id, best_score = None, -1.0
    for tid, mv in candidates:
        score = fuzz.token_set_ratio(hint, (mv.get("title") or "").lower())
        if score > best_score:
            best_score, best_id = score, tid
    return best_id if best_score >= 80 else None


def _split_indices(n, frac_dev, seed):
    """Índices [0..n) embaralhados (semente fixa) e cortados em dev/teste."""
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    cut = round(n * frac_dev)
    dev = set(idx[:cut])
    return dev


def build():
    sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
    from core import catalog

    cat = catalog.get_catalog()

    v1 = [("v1-core", q, h, y) for (q, h, y) in CORE_V1] + [("v1-ext", q, h, y) for (q, h, y) in EXT_V1]
    v2 = [("v2", q, h, y) for (q, h, y) in NEW_V2]

    # Split: v1 com 35/52 em dev (~0.673, = a divisão 35/17 pedida); v2 com 2/3.
    dev_v1 = _split_indices(len(v1), 35 / 52, SPLIT_SEED)
    dev_v2 = _split_indices(len(v2), 2 / 3, SPLIT_SEED + 1)

    rows, errors, seen = [], [], {}
    for group, items, dev_set in (("v1", v1, dev_v1), ("v2", v2, dev_v2)):
        for i, (source, query, hint, year) in enumerate(items):
            tid = resolve_target(hint, year)
            qid = f"{source}-{i:03d}" if group == "v2" else f"{source}-{i:02d}"
            if tid is None:
                errors.append(f"{qid}: '{hint}' ({year}) não resolveu")
                continue
            if tid in seen:
                errors.append(f"{qid}: '{hint}' ({year}) colide com {seen[tid]} (tmdb {tid})")
                continue
            seen[tid] = qid
            rows.append(
                {
                    "qid": qid,
                    "split": "dev" if i in dev_set else "test",
                    "query": query,
                    "title_hint": hint,
                    "year": year,
                    "relevant_tmdb_id": int(tid),
                    "relevant_title": cat[tid]["title"],
                    "source": source,
                }
            )

    if errors:
        print("FALHOU — rótulos não resolvidos ou em colisão:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        sys.exit(1)

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_dev = sum(r["split"] == "dev" for r in rows)
    n_test = len(rows) - n_dev
    by_src = {}
    for r in rows:
        by_src.setdefault(r["source"], [0, 0])
        by_src[r["source"]][0 if r["split"] == "dev" else 1] += 1
    print(f"OK — {len(rows)} consultas -> {OUT_PATH}")
    print(f"     dev={n_dev}  test={n_test}")
    for src, (d, t) in sorted(by_src.items()):
        print(f"     {src:<8} dev={d:<3} test={t}")


if __name__ == "__main__":
    build()
