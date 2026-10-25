# 🤡 Buggy, o Palhaço Estrela — Bot de Discord para RPG One Piece

## 📋 Pré-requisitos
- Python 3.10 ou superior
- Conta no Discord Developer Portal
- Conta gratuita no Groq (para a IA)

---

## 🔧 PASSO 1 — Criar o Bot no Discord

1. Acesse: https://discord.com/developers/applications
2. Clique em **"New Application"** → dê o nome **Buggy** (ou "Buggy o Palhaço Estrela")
3. Vá na aba **Bot** → clique em **"Add Bot"**
4. Em **"Privileged Gateway Intents"**, ative:
   - ✅ **SERVER MEMBERS INTENT**
   - ✅ **MESSAGE CONTENT INTENT**
5. Copie o **TOKEN** do bot (você vai precisar desse token!)
6. Para convidar para o servidor:
   - Vá em **OAuth2 → URL Generator**
   - Marque: `bot` e `applications.commands`
   - Permissões: `Send Messages`, `Read Messages/View Channels`, `Manage Messages`, `Read Message History`
   - Copie e acesse a URL gerada

---

## 🤖 PASSO 2 — Obter a API Key GRATUITA do Groq

1. Acesse: https://console.groq.com
2. Crie uma conta gratuita (pode usar Google/GitHub)
3. Vá em **API Keys** → **"Create API Key"**
4. Copie a chave (começa com `gsk_...`)

> **Por que Groq?** É 100% gratuito, extremamente rápido e usa modelos LLaMA de alta qualidade. Limite generoso no plano free.

---

## ⚙️ PASSO 3 — Configurar o Bot

Abra o arquivo `bot.py` e edite as linhas no topo:

```python
BOT_TOKEN = "SEU_TOKEN_DO_BOT_AQUI"     # Token copiado no passo 1
GROQ_API_KEY = "SUA_CHAVE_GROQ_AQUI"    # Chave copiada no passo 2
STAFF_ROLES = ["Developer", "Moderador"] # Nomes EXATOS dos cargos no seu servidor
```

> ⚠️ Os nomes em `STAFF_ROLES` precisam ser **idênticos** aos nomes dos cargos no Discord (respeitando maiúsculas/minúsculas).

---

## 📦 PASSO 4 — Instalar e Rodar

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar o bot
python bot.py
```

---

## 🎮 Como Usar

### Qualquer membro do servidor:
- Mencione `@Buggy` em qualquer mensagem → ele responde com IA como personagem
- Escreva a palavra **buggy** em qualquer mensagem → ele responde

### Staff (Developer / Moderação) — Modo Admin:

Para entrar no **modo admin**, use o prefixo `admin:` após a menção:

```
@Buggy admin: <ordem em linguagem natural>
```

**Sem o prefixo `admin:`, o Buggy responde normalmente como personagem de RP.**

| Exemplo de uso | O que faz |
|----------------|-----------|
| `@Buggy admin: fala no #geral que vai ter evento hoje` | Envia mensagem no canal |
| `@Buggy admin: bloqueia a categoria RPG` | Silencia a IA na categoria |
| `@Buggy admin: apaga as últimas 10 mensagens suas aqui` | Limpa msgs do Buggy |
| `@Buggy admin: apaga as últimas 5 msgs do @fulano` | Limpa msgs de um usuário |
| `@Buggy admin: ativa slowmode de 30s no #geral` | Ativa slowmode |
| `@Buggy admin: quais canais estão bloqueados?` | Lista canais silenciados |
| `@Buggy admin: ajuda` | Lista todos os exemplos |

### Slash Commands de Staff (forma alternativa):

| Comando | Descrição |
|---------|-----------|
| `/buggy_falar [mensagem]` | Buggy fala no canal atual |
| `/buggy_falar_canal [#canal] [mensagem]` | Buggy fala em canal específico |
| `/buggy_editar [id_da_mensagem] [novo_texto]` | Edita uma mensagem do Buggy |
| `/buggy_apagar [id_da_mensagem]` | Apaga uma mensagem específica |
| `/buggy_limpar [N]` | Apaga as últimas N mensagens do Buggy (máx. 100) |
| `/buggy_limpar_tudo` | Apaga TODAS as mensagens do Buggy no canal |
| `/buggy_ajuda` | Lista todos os comandos |

> **Como pegar o ID de uma mensagem:** Ative o Modo Desenvolvedor no Discord (Configurações → Avançado → Modo Desenvolvedor), depois clique com botão direito na mensagem → "Copiar ID".

---

## 🛒 Loja Virtual

1. Staff usa `buggy!loja_painel` (ou `buggy!loja_painel #canal`) para postar o painel com o botão **Abrir loja**.
2. O jogador clica, escolhe os itens e as quantidades (carrinho com total) e finaliza.
3. O bot cria um canal privado `<nick>-<id da compra>` (ex.: `percy-01`) com o jogador, o bot e a staff, com o resumo e o total.
4. No canal vão o resumo, o total e o **PIX** (QR Code + copia e cola) já com o valor da compra. O jogador paga e envia o comprovante ali mesmo.
5. A staff usa os botões do canal: **Marcar como pago**, **Marcar como entregue** e **Fechar compra** (fechar sem pagar marca como cancelada e apaga o canal).
6. Tudo aparece no painel de compras (site externo, abaixo).

### Registro público das compras e gachas

Toda compra aprovada ("Marcar como pago") é anunciada pelo bot, num embed, no canal de registro (`1563931955207077889`) para os jogadores conferirem (o painel web só a staff vê): número da compra, quem comprou, **quem aprovou**, quando, itens, total, gachas e o estado da entrega. Quando a staff marca como entregue, o mesmo post é **editado** (não duplica). Nunca aparecem o PIX, o link do canal privado nem dados da staff além do nome de quem aprovou.

- **Gachas:** 1 a cada R$ 20,00 em **cada compra** (R$ 40 = 2, R$ 60 = 3...), sempre arredondando para baixo. O aviso no canal da compra já informa.
- Se o Discord falhar na hora de postar, o anúncio fica pendente e o bot tenta de novo sozinho (a cada 2 minutos e ao ligar), sem repetir posts.
- O bot precisa de *Ver Canal*, *Enviar Mensagens*, *Inserir Links* e *Ver Histórico* nesse canal. `buggy!loja_status` mostra se está tudo certo.

### Benefícios mensais das assinaturas

O botão **🎁 Benefícios do mês** do painel libera, **uma vez por mês**, para quem tem o cargo de assinante:

| Cargo | Itens fixos (automáticos) | VIPs à escolha (até R$ 15,00) |
|---|---|---|
| `1558206515784188115` (nível 1) | 3 Escolhido dos Mares + 1 Sorte Grande | 1 |
| `1558206661779267665` (nível 2) | 6 Escolhido dos Mares + 2 Sorte Grande | 2 |

Só a escolha dos VIPs é feita no botão; os itens fixos já entram sozinhos. O resgate vira um pedido normal (tipo benefício, valor R$ 0,00, não gera gacha) com um canal privado de entrega para a staff, e também é registrado no canal público.

Como a regra de "1 por mês" é protegida:
- A trava é um documento no Firebase (`loja_beneficios/AAAA-MM-<id do jogador>`), criado de forma **atômica** pelo servidor: com 2 cliques, 2 bots ou 30 pedidos simultâneos, só um passa. O navegador não tem acesso a essa coleção.
- Vale para o mês de Brasília (vira à 00:00 do dia 1), contado pela hora do Discord, não pelo relógio do servidor. Reiniciar o bot, apagar `store_data.json` ou zerar o banco de pedidos **não** devolve o direito.
- Sem Firebase não há resgate (falha fechada): não dá para garantir a regra.
- Se o bot cair no meio, o resgate é concluído ao ligar (e a cada 2 minutos): reaproveita o canal e o pedido que já existem, sem duplicar nada.
- O cargo e as escolhas são conferidos de novo na hora de confirmar. Se algo der errado, o jogador recebe uma mensagem com um **código** pedindo para acionar o suporte (o detalhe técnico fica só no log do bot).

Depois de atualizar o bot, rode `buggy!loja_painel` de novo para o painel ganhar o botão novo (e apague a mensagem do painel antigo).

### Painel de compras (site externo, ao vivo)

Um site que mostra todas as compras em tempo real, **fora do Discord**: https://buggy-loja.web.app

- Resumo (faturamento confirmado, aguardando pagamento, pagas, entregues, canceladas), gráfico dos últimos 14 dias e a lista completa com data, nome, usuário, **ID do usuário**, itens, total, status, datas de pagamento/entrega e responsável.
- Busca, filtros por status e período, ordenação, botão de copiar ID e **Exportar CSV** (abre no Excel). Funciona no celular.
- Atualiza sozinho quando entra uma compra ou muda um status (sem recarregar a página).
- Para ver como fica sem login, abra o endereço com `#demo` no final (dados fictícios).

**Quem pode entrar:** só contas Google autorizadas, o que se controla pelo Discord (staff):
- `buggy!loja_acesso adicionar fulano@gmail.com` — autoriza (use o e-mail da conta Google do login)
- `buggy!loja_acesso remover fulano@gmail.com` — tira o acesso
- `buggy!loja_acesso listar` — mostra quem tem acesso

A proteção é dupla: o login do Google e as regras do Firestore (`firestore.rules`) só liberam a leitura para e-mails autorizados e nunca permitem escrita pelo navegador.

**Publicar/atualizar o site** (depois de mexer em `dashboard/` ou `firestore.rules`), na pasta do bot:

```bash
python tools/deploy_firebase.py             # app web + regras + site
python tools/deploy_firebase.py --so-site   # só a página
python tools/deploy_firebase.py --so-regras # só as regras de segurança
```

O comando usa o mesmo `firebase-credentials.json` do bot; não precisa instalar o firebase-tools.

### Editar a loja pelo painel (aba "🛠️ Loja")

A aba **Loja** do painel (https://buggy-loja.web.app) edita o catálogo da loja do Discord: **preço, nome, descrição, adicionar, excluir, ocultar, reordenar e o texto de cada página**. O bot acompanha o Firebase ao vivo, então a mudança aparece na loja do Discord em poucos segundos, sem reiniciar nada.

- **Itens:** nome (até 60 caracteres), preço (R$ 0,01 a R$ 10.000,00; aceita `5`, `12,50`, `1.234,56`), descrição (até 1500 caracteres, com a formatação do Discord) e página. O título de cada item (`## 🔷 Nome - R$ 5,00`) é montado sozinho a partir do nome e do preço: **não repita na descrição**. A janela mostra uma prévia do texto exato que vai para o Discord.
- **Ocultar** tira da loja sem apagar (dá para trazer de volta); **excluir** apaga de vez (as compras antigas continuam no histórico, com o nome e o preço da época).
- **Páginas:** até 5, com um texto de introdução opcional. Só dá para excluir página vazia.
- **Limites do Discord, avisados antes de salvar:** 25 itens visíveis no total e 4000 caracteres por página.
- **Histórico:** toda alteração fica registrada (quem, quando, o que mudou) e ninguém consegue editar nem apagar esse histórico.

**Quem pode editar** é decidido no Discord, pela staff, em dois níveis:

| Comando | Efeito |
|---|---|
| `buggy!loja_acesso adicionar fulano@gmail.com` | entra no painel, **só visualiza** |
| `buggy!loja_acesso editor fulano@gmail.com` | entra e **edita o catálogo** |
| `buggy!loja_acesso visualizar fulano@gmail.com` | volta a só visualizar |
| `buggy!loja_acesso remover fulano@gmail.com` | tira o acesso |
| `buggy!loja_acesso listar` | mostra quem tem acesso e o nível (✏️ edita, 👁️ só vê) |

**Segurança:** as regras do banco (`firestore.rules`) validam cada gravação (tipos, tamanhos, preço, página) e carimbam o e-mail de quem editou e a hora do servidor, então nem uma página adulterada consegue gravar lixo ou se passar por outra pessoa. Quem não é editor não grava nada, mesmo burlando a interface.

**Compras e preços:** se a staff muda um preço enquanto alguém está com o carrinho aberto, a compra é **pausada** e a pessoa vê o novo valor antes de finalizar: nunca se cobra um preço que ela não viu. Cada pedido guarda o nome e o preço do momento da compra.

**Primeira vez:** o catálogo atual (12 itens em 3 páginas) fica em `catalogo_padrao.py`. O bot o envia ao Firebase sozinho na primeira inicialização (ou rode `python tools/semear_catalogo.py`); depois disso quem manda é o Firebase. Se o Firebase estiver fora do ar na partida, o bot usa a última cópia local (`catalogo_cache.json`) e, sem ela, o catálogo padrão.

### Onde os dados ficam (Firebase)

Os pedidos são salvos no **Firebase Firestore** (plano gratuito Spark), com uma cópia em `store_data.json`. Assim o histórico sobrevive a redeploys da hospedagem. Se o Firebase falhar, a compra continua normalmente: o bot guarda no arquivo local e reenvia sozinho (a cada 5 minutos) quando o Firebase voltar. Ao iniciar, ele junta as duas cópias e vale o registro mais recente de cada pedido.

Como configurar:
1. Em [console.firebase.google.com](https://console.firebase.google.com), crie um projeto e ative o **Firestore Database** (modo produção).
2. Em *Configurações do projeto → Contas de serviço*, clique em **Gerar nova chave privada** e salve o `.json` como `firebase-credentials.json` na pasta do bot (já está no `.gitignore`).
   - Alternativa: coloque o conteúdo do arquivo em base64 na variável `FIREBASE_CREDENTIALS_BASE64` do `.env`.
3. Reinicie o bot e use `buggy!loja_armazenamento` (staff) para confirmar que aparece **Firebase conectado**.

⚠️ O arquivo de credenciais dá acesso total ao banco: **nunca** o coloque no GitHub.

Configuração (no `.env`):
- `FIREBASE_CREDENTIALS_BASE64` / `FIREBASE_CREDENTIALS_FILE` — credenciais do Firebase (veja acima). Sem elas o bot usa só o arquivo local.
- `PIX_COPIA_COLA` — código PIX "copia e cola" da conta que recebe. O bot embute o valor de cada compra e gera o QR Code. **Nunca coloque no código nem no GitHub.** Se faltar ou for inválido, o ticket abre sem PIX.
- `LOJA_CATEGORIA_ID` — categoria onde os canais de compra são criados.
- `LOJA_CARGO_ALERTA_ID` — cargo avisado em toda compra (padrão: o cargo citado no texto da loja).
- `LOJA_CANAL_REGISTRO_ID`, `LOJA_CARGO_ASSINANTE_1_ID`, `LOJA_CARGO_ASSINANTE_2_ID` — canal do registro público e cargos de assinante (opcionais: já vêm com os valores do servidor).

O bot precisa da permissão **Gerenciar Canais**. Os itens e preços ficam em `loja.py` (`PAGINAS`). O contador de compras fica em `store_data.json`.

## 🧪 Testes

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests                         # tudo que roda offline: unitários, segurança, carga e painel no Chrome
RUN_INTEGRATION=1 python -m pytest tests       # inclui os testes contra o Firebase REAL
python -m pytest tests -m "not browser"        # sem abrir o Chrome
python -m pytest tests/test_carga.py -s        # pico de uso, mostrando os números medidos
python -m bandit -r bot.py loja.py loja_dados.py beneficios.py pix.py tools    # análise estática de segurança
python -m pip_audit -r requirements.txt                          # vulnerabilidades conhecidas nas dependências
```

| Arquivo | O que cobre |
|---|---|
| `tests/test_pix.py` | CRC, valor embutido, estrutura do código e leitura do QR Code |
| `tests/test_loja_unit.py` | preços, catálogo, carrinho, janela de quantidades e limites do Discord |
| `tests/test_dados.py` | armazenamento, Firebase (falso), conflitos, falhas, pedidos antigos e "época" |
| `tests/test_fluxo.py` | compra, canal privado, botões da staff, limite por pessoa e comandos |
| `tests/test_seguranca.py` | permissões, e-mails maliciosos, vazamento de segredos, regras e painel |
| `tests/test_carga.py` | pico de uso: 200 compras simultâneas, cliques repetidos, volume de dados |
| `tests/test_painel_web.py` | o painel no Chrome: injeção de código, CSV, filtros, celular e 20 mil compras |
| `tests/test_catalogo.py` | catálogo: validação de documentos, montagem das páginas, semeadura e sincronização ao vivo |
| `tests/test_catalogo_loja.py` | a loja do Discord com o catálogo mudando: telas, checkout seguro, concorrência, níveis de acesso |
| `tests/test_editor_catalogo.py` | a aba de edição no Chrome (demonstração): criar, editar, mover, excluir, limites, só-visualização |
| `tests/test_registro_publico.py` | o registro público: conteúdo do embed, sem vazar dados, edição na entrega, sem duplicar, falhas e retomada |
| `tests/test_beneficios.py` | benefícios mensais: 1 por mês, concorrência, reinício do bot, escolhas forjadas, recuperação e mensagens de erro |
| `tests/test_integracao.py` | Firebase real: ciclo completo, carga, regras com usuários reais |
| `tests/test_editor_e2e_real.py` | o painel no Chrome logado como editor, contra o Firebase real |

Os testes normais **nunca** falam com o Firebase real nem leem o seu `.env` (o Firebase é simulado). Os de integração só rodam com `RUN_INTEGRATION=1`, usam coleções de teste (`teste_*`) e apagam tudo o que criam.

## 🧹 Limpar o banco (tirar compras de teste)

```bash
python tools/limpar_banco.py              # só MOSTRA o que seria apagado
python tools/limpar_banco.py --confirmar  # faz backup em backups/ e apaga
```

Apaga os pedidos e zera o contador (a próxima compra volta a ser a `01`). Não mexe nos e-mails autorizados do painel nem nos usuários do login. O banco ganha uma nova "época": quando o bot reiniciar, ele descarta o `store_data.json` antigo do servidor e passa a valer só o Firebase, então os pedidos apagados **não voltam**. Apague também, no Discord, os canais das compras de teste.

---

## 🌐 Rodar 24/7 (Opcional)

Para manter o bot sempre online, você pode usar:
- **Railway** (https://railway.app) — plano gratuito disponível
- **Render** (https://render.com) — plano gratuito disponível
- **VPS/servidor próprio**

---

## ❓ Dúvidas Comuns

**O bot não responde quando escrevo "buggy":**
Verifique se o `MESSAGE CONTENT INTENT` está ativado no painel do bot.

**Staff menciona o Buggy mas ele entra em modo admin sem querer:**
Agora só entra em modo admin com `@Buggy admin: <instrução>`. Sem o prefixo, responde normalmente.

**Os slash commands não aparecem:**
Aguarde até 1 hora após convidar o bot. Ou expulse e re-convide o bot.

**Erro de permissão nos comandos de staff:**
Verifique se o nome do cargo em `STAFF_ROLES` no `bot.py` está **exatamente igual** ao do Discord.