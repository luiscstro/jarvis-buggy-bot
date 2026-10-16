"""Catálogo PADRÃO da loja (o que existia antes de o catálogo poder ser editado pelo painel).

Serve para duas coisas:
  - semear o Firebase na primeira vez (coleções loja_paginas e loja_itens);
  - ser o plano B do bot se o Firebase estiver fora do ar e não houver cópia local.

Depois de semeado, quem manda é o Firebase: edite pelo painel web, não aqui.

Cada item mostra, na loja, um cabeçalho gerado a partir de `nome` e `preco_centavos`,
seguido de `descricao`. Por isso a descrição NÃO repete o nome nem o preço.
"""

PAGINAS_PADRAO = [
    {
        "id": "p1",
        "ordem": 1,
        "introducao": """# __APOIO AO SERVIDOR__ <:berry:1222990525372563539>
Caso o jogador quiser, ele pode apoiar nosso servidor e nos ajudar a crescer! Ao apoiar, nos ajudando em sua manutenção, poderá ter acesso a recompensas e benefícios exclusivos para vocês e **intransferíveis**! Por enquanto, essas são nossas opções... Fiquem atentos que mais virão!
**Qualquer aquisição e utilização de algum VIP deverá alertar a <@&1222232432527413389> previamente no ticket criado em <#1463579296130928650>**""",
    },
    {
        "id": "p2",
        "ordem": 2,
        "introducao": "",
    },
    {
        "id": "p3",
        "ordem": 3,
        "introducao": "",
    },
]

ITENS_PADRAO = [
    {
        "key": "escolhidos",
        "nome": "Escolhidos dos Mares",
        "preco_centavos": 500,
        "pagina": "p1",
        "ordem": 1,
        "ativo": True,
        "descricao": """Pode, no início da missão, escolher receber uma benção dos mares. Essa benção irá conceder o dobro das recompensas da missão, ou seja, dobro de XP e ouro.

⚠️ **NÃO poderá ser usada em missões com multiplicadores de recompensa! Caso uma missão vire longa duração, seu Escolhido não será gasto.**
⚠️**² Pode ser guardado apenas no <#1223463532948095016>.**""",
    },
    {
        "key": "mudanca",
        "nome": "Mudança Pequena",
        "preco_centavos": 500,
        "pagina": "p1",
        "ordem": 2,
        "ativo": True,
        "descricao": """Para aqueles que desejam ajustar __um pequeno detalhe__ na ficha do personagem. Como por exemplo:

🔸Ajustar um Defeito ou Individualidade;
🔸Trocar um Atributo por outro;
🔸Trocar uma Perícia;
🔸Trocar a profissão de um contratado;
🔸Trocar o tipo de arma meito escolhida de evento ou de ruína (mantendo suas limitações).

⚠️ **Pode ser guardado apenas no <#1223463532948095016>.**""",
    },
    {
        "key": "renascimento",
        "nome": "Renascimento",
        "preco_centavos": 1500,
        "pagina": "p1",
        "ordem": 3,
        "ativo": True,
        "descricao": """Para aqueles que já utilizaram seu renascimento grátis e desejam realizar mudanças enormes na ficha, podendo até criar outro personagem.

⚠️ **Pode ser guardado apenas no <#1223463532948095016>.**
⚠️²** RB que mudarem a __Raça do Personagem__ resultará em algumas características da troca de personagem como __Morte completa__, sendo perdido Akuma no Mi e Recompensa pela cabeça novamente zerada.**
⚠️³**O usuário que perder a akuma no mi com este recurso só poderá ingerir outra após o período de __2 meses__.**""",
    },
    {
        "key": "manobra",
        "nome": "Manobra de Emergência",
        "preco_centavos": 500,
        "pagina": "p2",
        "ordem": 4,
        "ativo": True,
        "descricao": """Pode ser usado após falhar no teste de navegação para garantir um sucesso automático.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**""",
    },
    {
        "key": "aviso",
        "nome": "Aviso de Amigo",
        "preco_centavos": 500,
        "pagina": "p2",
        "ordem": 5,
        "ativo": True,
        "descricao": """Usado para __evitar encontros marítimos__ e prosseguir com a exploração/caçada na ilha.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**""",
    },
    {
        "key": "cacador",
        "nome": "Caçador de Tesouros",
        "preco_centavos": 1000,
        "pagina": "p2",
        "ordem": 6,
        "ativo": True,
        "descricao": """Usado na exploração, garante que ao adquirir um tesouro, receberá a recompensa em Berry referente ao nível do arqueólogo além de qualquer outra coisa rolada no tesouro.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**""",
    },
    {
        "key": "sorte",
        "nome": "Sorte Grande",
        "preco_centavos": 1500,
        "pagina": "p2",
        "ordem": 7,
        "ativo": True,
        "descricao": """Pode rolar novamente __**o dado de tesouro do arqueólogo depois de acharem a ruína**__ e pode escolher o primeiro ou segundo resultado. __Também pode ser usada para rolar novamente a Akuma que encontraram ou que foi comida.__

**⚠️Em caso de rerolagem do Arqueólogo Especialista, só poderá ser usado após o segundo dado!**
⚠️**² Pode ser guardado no <#1223463532948095016> ou no baú do bando.**
⚠️**³ Poder ser usado somente uma vez por dado rerolado**""",
    },
    {
        "key": "token",
        "nome": "Token de Segunda Chance",
        "preco_centavos": 500,
        "pagina": "p3",
        "ordem": 8,
        "ativo": True,
        "descricao": """Permite que, quando realizar um teste de ofício seja possível repetir o teste imediatamente ao invés de esperar o cooldown.
⚠️ **Um uso por teste.**""",
    },
    {
        "key": "aprendiz",
        "nome": "Aprendiz do Mestre",
        "preco_centavos": 1000,
        "pagina": "p3",
        "ordem": 9,
        "ativo": True,
        "descricao": """Um NPC mestre de ofício (ex: carpinteiro) visita o navio por 1 dia, podendo realizar um único teste com o bônus dele.
-# ⚠️ Este VIP pode ser usado no inicio de uma sessão como arqueólogo para __encontrar ruínas__, porém deve ser retirado do baú e e avisado ao mestre e ao Arkros __no inicio__ da sessão.
-# ⚠️² O NPC terá o maior bônus possível de nível acordo com o mar em que a tripulação se encontra.
-# ⚠️³ O VIP permite realizar apenas um único teste instantâneo e nada mais, ou seja, resultados posteriores como recompensa de Ouro e Joias do arqueólogo usará como base o nível mais alto da tripulação, nem teste que peçam dias""",
    },
    {
        "key": "lunariano",
        "nome": "Re-roll de Lunariano",
        "preco_centavos": 2000,
        "pagina": "p3",
        "ordem": 10,
        "ativo": True,
        "descricao": """Permite refazer a rolagem de Lunariano
⚠️ **Ao comprar este VIP, vá no seu ticket criado em https://discord.com/channels/1220345032079572993/1463579296130928650 e marque a MODERAÇÃO OU SUPERVISÃO para que removam seu cargo de ROLOU LUNARIANO 1 e então faça novamente a rolagem em https://discord.com/channels/1220345032079572993/1285265253957107733**""",
    },
    {
        "key": "rumores",
        "nome": "Rumores!",
        "preco_centavos": 500,
        "pagina": "p3",
        "ordem": 11,
        "ativo": True,
        "descricao": """A reputação recebida em exploração recebe +/- 5 de acordo com o feito da tripulação.
-# ⚠️ Em caso de reputação mista, a tripulação escolhe qual tipo de feito recebe o bônus de +/- 5.""",
    },
    {
        "key": "supertesouro",
        "nome": "Super Tesouro",
        "preco_centavos": 2000,
        "pagina": "p3",
        "ordem": 12,
        "ativo": True,
        "descricao": """Aplica um multiplicador nos bellys do tesouro encontrado pelo arqueólogo.
> - Blues e Paradise: x2.
> - New Word: x5.
> - Laugh Tale: x10.
> -# ⚠️ Pode ser usado com o caçador de tesouros, entrando após a adição do caçador de tesouros.
> -# ⚠️² Só pode ser usado quando a recompensa de ruína for "Dinheiro e Joias"
-# Proposto por Roku""",
    },
]
