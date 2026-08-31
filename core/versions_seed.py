"""Semente da tabela de versões — casos famosos de consenso cinéfilo.

Carga única (controlada por marcador em `meta`): depois disso a tabela é 100%
curadoria do usuário — tudo aqui pode ser editado ou apagado pela ficha.
Cada filme: (tmdb_id, [(nome, duração_min, é_a_melhor, notas), ...]),
em ordem cronológica de lançamento da versão.
"""

SEED: list[tuple[int, list[tuple[str, int, bool, str]]]] = [
    (78, [  # Blade Runner
        ("Versão de Cinema (1982)", 117, False,
         "Narração em off e final feliz impostos pelo estúdio."),
        ("Director's Cut (1992)", 116, False,
         "Sem narração, final ambíguo, sonho do unicórnio — mas montada sem supervisão total de Ridley Scott."),
        ("Final Cut (2007)", 117, True,
         "A única versão com controle criativo total de Ridley Scott. Imagem e som restaurados — a definitiva."),
    ]),
    (28, [  # Apocalypse Now
        ("Versão de Cinema (1979)", 147, False, "A montagem original de Cannes/estreia."),
        ("Redux (2001)", 202, False,
         "49 min extras (plantação francesa, playmates) — para muitos, longa demais."),
        ("Final Cut (2019)", 183, True,
         "O corte que Coppola declarou definitivo: mais completo que o de 1979, mais enxuto que o Redux."),
    ]),
    (1495, [  # Cruzada / Kingdom of Heaven
        ("Versão de Cinema (2005)", 144, False,
         "Mutilada pelo estúdio — a subtrama do filho de Sibylla foi cortada e a história perde a lógica."),
        ("Director's Cut (2005)", 194, True,
         "Caso clássico de corte que transforma o filme: de fracasso a épico respeitado. Ver sempre esta."),
    ]),
    (141052, [  # Liga da Justiça
        ("Versão de Cinema — Whedon (2017)", 120, False,
         "Refilmagens e tom trocado após a saída de Zack Snyder."),
        ("Snyder Cut (2021)", 242, True,
         "Zack Snyder's Justice League (na TMDB é um filme à parte). Visão completa do diretor, em 4:3."),
    ]),
    (141, [  # Donnie Darko
        ("Versão de Cinema (2001)", 113, True,
         "Consenso raro a favor do corte de cinema: preserva o mistério."),
        ("Director's Cut (2004)", 133, False,
         "Explica demais a mitologia (páginas do livro na tela) e muda músicas."),
    ]),
    (8077, [  # Alien³
        ("Versão de Cinema (1992)", 114, False,
         "Montagem conturbada; Fincher renegou o filme."),
        ("Assembly Cut (2003)", 145, True,
         "Reconstrução do corte de trabalho: prólogo diferente (o boi), ritmo e lógica bem melhores."),
    ]),
    (679, [  # Aliens
        ("Versão de Cinema (1986)", 137, False, "Mais enxuta e tensa."),
        ("Edição Especial (1991)", 154, True,
         "Sentinelas, a filha de Ripley — contexto que enriquece sem estragar o ritmo. Preferida de Cameron."),
    ]),
    (120, [  # LOTR: A Sociedade do Anel
        ("Versão de Cinema (2001)", 178, False, "Completa por si só."),
        ("Versão Estendida (2002)", 228, True,
         "Mais Condado, presentes de Galadriel, Gilraen — o padrão dos fãs para a maratona."),
    ]),
    (311, [  # Era Uma Vez na América
        ("Corte Americano (1984)", 139, False,
         "Remontado em ordem cronológica pelo estúdio, sem Leone — evitar."),
        ("Corte Europeu (1984)", 229, True,
         "A estrutura de memórias de Sergio Leone. A versão a ver."),
        ("Extended Director's Cut (2012)", 251, False,
         "Cenas restauradas de qualidade variável — para completistas."),
    ]),
    (68, [  # Brazil
        ("Corte do Diretor (1985)", 142, True,
         "A visão de Terry Gilliam, com o final sombrio original."),
        ("“Love Conquers All” — corte do estúdio", 94, False,
         "Final feliz imposto pela Universal para a TV — curiosidade histórica."),
    ]),
    (13183, [  # Watchmen
        ("Versão de Cinema (2009)", 162, False, "Funciona, mas apressada."),
        ("Director's Cut (2009)", 186, True,
         "O equilíbrio certo: mais Rorschach e Hollis Mason sem quebrar o ritmo."),
        ("Ultimate Cut (2009)", 215, False,
         "Intercala a animação Tales of the Black Freighter — para completistas."),
    ]),
    (1480, [  # A Marca da Maldade / Touch of Evil
        ("Versão de Cinema (1958)", 95, False,
         "Remontada pela Universal contra a vontade de Orson Welles."),
        ("Reconstrução (1998)", 111, True,
         "Segue o memorando de 58 páginas de Welles ao estúdio — o mais perto da visão dele."),
    ]),
]
