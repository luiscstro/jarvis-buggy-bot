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

O bot precisa da permissão **Gerenciar Canais**. Os itens e preços ficam em `loja.py` (`PAGINAS`). O contador de compras fica em `store_data.json`.

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