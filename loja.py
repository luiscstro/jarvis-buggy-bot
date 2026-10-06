"""Loja virtual do servidor de RPG.

Fluxo:
  1. Staff posta o painel com `buggy!loja_painel` (botão "Abrir loja").
  2. O jogador clica no botão e vê a loja (mensagem só dele): escolhe os itens
     e as quantidades, vendo o carrinho e o total.
  3. Ao finalizar, o bot cria um canal privado "<nick>-<id da compra>"
     (ex.: percy-01) visível só para o jogador, o bot e a staff, com o resumo
     do pedido, o total a pagar e o PIX (copia e cola + QR Code) já com o valor.
  4. A staff marca a compra como paga/entregue no próprio canal e acompanha tudo
     no painel web ao vivo (pasta dashboard/); quem pode entrar é definido com
     `buggy!loja_acesso`.

Os pedidos são guardados no Firebase Firestore (com cópia local); veja loja_dados.py.
"""
import asyncio
import io
import os
import re
import unicodedata
from datetime import datetime
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

import pix
from loja_dados import Armazenamento, agora_iso

# =============================================
#  CONFIGURAÇÕES
# =============================================
# Categoria onde os canais de compra são criados (opcional).
# Defina LOJA_CATEGORIA_ID no .env; sem ela os canais ficam fora de categoria.
LOJA_CATEGORIA_ID = int(os.getenv("LOJA_CATEGORIA_ID", "0") or 0)

# Cargo que precisa ser avisado em qualquer compra de VIP (vem do texto da loja).
LOJA_CARGO_ALERTA_ID = int(os.getenv("LOJA_CARGO_ALERTA_ID", "1222232432527413389") or 0)

LOJA_ARQUIVO = "store_data.json"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# PIX copia e cola da conta que recebe os pagamentos (guarde no .env, nunca no código).
# O bot embute o valor de cada compra e gera o QR Code a partir dele.
PIX_COPIA_COLA = (os.getenv("PIX_COPIA_COLA") or "").strip()
if PIX_COPIA_COLA and not pix.payload_valido(PIX_COPIA_COLA):
    print("[Loja] PIX_COPIA_COLA inválido (o CRC não confere). PIX desativado.")
    PIX_COPIA_COLA = ""

STATUS = {
    "aguardando_pagamento": "🟡 Aguardando pagamento",
    "paga": "💰 Paga — a entregar",
    "entregue": "📦 Entregue",
    "cancelada": "❌ Cancelada",
}

# =============================================
#  CATÁLOGO
# =============================================
# preco em centavos. Os textos aparecem exatamente como serão exibidos no Discord.
PAGINAS = [
    {
        "descricao": """# __APOIO AO SERVIDOR__ <:berry:1222990525372563539>
Caso o jogador quiser, ele pode apoiar nosso servidor e nos ajudar a crescer! Ao apoiar, nos ajudando em sua manutenção, poderá ter acesso a recompensas e benefícios exclusivos para vocês e **intransferíveis**! Por enquanto, essas são nossas opções... Fiquem atentos que mais virão!
**Qualquer aquisição e utilização de algum VIP deverá alertar a <@&1222232432527413389> previamente no ticket criado em <#1463579296130928650>**

## 🔷 ESCOLHIDOS DOS MARES - R$ 5.00
Pode, no início da missão, escolher receber uma benção dos mares. Essa benção irá conceder o dobro das recompensas da missão, ou seja, dobro de XP e ouro.

⚠️ **NÃO poderá ser usada em missões com multiplicadores de recompensa! Caso uma missão vire longa duração, seu Escolhido não será gasto.**
⚠️**² Pode ser guardado apenas no <#1223463532948095016>.**

## 🔷 MUDANÇA PEQUENA - R$ 5.00
Para aqueles que desejam ajustar __um pequeno detalhe__ na ficha do personagem. Como por exemplo:

🔸Ajustar um Defeito ou Individualidade;
🔸Trocar um Atributo por outro;
🔸Trocar uma Perícia;
🔸Trocar a profissão de um contratado;
🔸Trocar o tipo de arma meito escolhida de evento ou de ruína (mantendo suas limitações).

⚠️ **Pode ser guardado apenas no <#1223463532948095016>.**

## 🔷 RENASCIMENTO - R$ 15.00
Para aqueles que já utilizaram seu renascimento grátis e desejam realizar mudanças enormes na ficha, podendo até criar outro personagem.

⚠️ **Pode ser guardado apenas no <#1223463532948095016>.**
⚠️²** RB que mudarem a __Raça do Personagem__ resultará em algumas características da troca de personagem como __Morte completa__, sendo perdido Akuma no Mi e Recompensa pela cabeça novamente zerada.**
⚠️³**O usuário que perder a akuma no mi com este recurso só poderá ingerir outra após o período de __2 meses__.**""",
        "itens": [
            {"key": "escolhidos", "nome": "Escolhidos dos Mares", "preco": 500},
            {"key": "mudanca", "nome": "Mudança Pequena", "preco": 500},
            {"key": "renascimento", "nome": "Renascimento", "preco": 1500},
        ],
    },
    {
        "descricao": """## 🔷 MANOBRA DE EMERGÊNCIA - R$5.00
Pode ser usado após falhar no teste de navegação para garantir um sucesso automático.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**

## 🔷 **AVISO DE AMIGO - R$5.00**
Usado para __evitar encontros marítimos__ e prosseguir com a exploração/caçada na ilha.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**

## 🔷 **CAÇADOR DE TESOUROS - R$10.00**
Usado na exploração, garante que ao adquirir um tesouro, receberá a recompensa em Berry referente ao nível do arqueólogo além de qualquer outra coisa rolada no tesouro.

⚠️ **Pode ser guardado no <#1223463532948095016> ou no baú do bando.**

## 🔷 **SORTE GRANDE - R$15.00**
Pode rolar novamente __**o dado de tesouro do arqueólogo depois de acharem a ruína**__ e pode escolher o primeiro ou segundo resultado. __Também pode ser usada para rolar novamente a Akuma que encontraram ou que foi comida.__

**⚠️Em caso de rerolagem do Arqueólogo Especialista, só poderá ser usado após o segundo dado!**
⚠️**² Pode ser guardado no <#1223463532948095016> ou no baú do bando.**
⚠️**³ Poder ser usado somente uma vez por dado rerolado**""",
        "itens": [
            {"key": "manobra", "nome": "Manobra de Emergência", "preco": 500},
            {"key": "aviso", "nome": "Aviso de Amigo", "preco": 500},
            {"key": "cacador", "nome": "Caçador de Tesouros", "preco": 1000},
            {"key": "sorte", "nome": "Sorte Grande", "preco": 1500},
        ],
    },
    {
        "descricao": """## 🔷 Token de Segunda Chance 5 R$
Permite que, quando realizar um teste de ofício seja possível repetir o teste imediatamente ao invés de esperar o cooldown.
⚠️ **Um uso por teste.**

## 🔷 Aprendiz do Mestre 10 R$
Um NPC mestre de ofício (ex: carpinteiro) visita o navio por 1 dia, podendo realizar um único teste com o bônus dele.
-# ⚠️ Este VIP pode ser usado no inicio de uma sessão como arqueólogo para __encontrar ruínas__, porém deve ser retirado do baú e e avisado ao mestre e ao Arkros __no inicio__ da sessão.
-# ⚠️² O NPC terá o maior bônus possível de nível acordo com o mar em que a tripulação se encontra.
-# ⚠️³ O VIP permite realizar apenas um único teste instantâneo e nada mais, ou seja, resultados posteriores como recompensa de Ouro e Joias do arqueólogo usará como base o nível mais alto da tripulação, nem teste que peçam dias

## 🔷 Re-roll de Lunariano 20 R$
Permite refazer a rolagem de Lunariano
⚠️ **Ao comprar este VIP, vá no seu ticket criado em https://discord.com/channels/1220345032079572993/1463579296130928650 e marque a MODERAÇÃO OU SUPERVISÃO para que removam seu cargo de ROLOU LUNARIANO 1 e então faça novamente a rolagem em https://discord.com/channels/1220345032079572993/1285265253957107733**

## 🔷 Rumores! 5 R$
A reputação recebida em exploração recebe +/- 5 de acordo com o feito da tripulação.
-# ⚠️ Em caso de reputação mista, a tripulação escolhe qual tipo de feito recebe o bônus de +/- 5.

## 🔷 Super Tesouro 20 R$
Aplica um multiplicador nos bellys do tesouro encontrado pelo arqueólogo.
> - Blues e Paradise: x2.
> - New Word: x5.
> - Laugh Tale: x10.
> -# ⚠️ Pode ser usado com o caçador de tesouros, entrando após a adição do caçador de tesouros.
> -# ⚠️² Só pode ser usado quando a recompensa de ruína for "Dinheiro e Joias"
-# Proposto por Roku""",
        "itens": [
            {"key": "token", "nome": "Token de Segunda Chance", "preco": 500},
            {"key": "aprendiz", "nome": "Aprendiz do Mestre", "preco": 1000},
            {"key": "lunariano", "nome": "Re-roll de Lunariano", "preco": 2000},
            {"key": "rumores", "nome": "Rumores!", "preco": 500},
            {"key": "supertesouro", "nome": "Super Tesouro", "preco": 2000},
        ],
    },
]

ITENS = {item["key"]: item for pagina in PAGINAS for item in pagina["itens"]}


# =============================================
#  UTILITÁRIOS
# =============================================
def brl(centavos: int) -> str:
    return f"R$ {centavos // 100},{centavos % 100:02d}"


def slug_nick(nome: str) -> str:
    """Converte um nick em algo válido para nome de canal (ex.: 'Percy Jackson' -> 'percy-jackson')."""
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    nome = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    return nome[:80] or "cliente"


def total_carrinho(carrinho: dict[str, int]) -> int:
    return sum(ITENS[k]["preco"] * q for k, q in carrinho.items())


def linhas_carrinho(carrinho: dict[str, int]) -> str:
    return "\n".join(
        f"**{q}x** {ITENS[k]['nome']} — {brl(ITENS[k]['preco'] * q)}" for k, q in carrinho.items()
    )


def epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


# =============================================
#  PAINEL (botão fixo no canal)
# =============================================
class PainelLojaView(ui.View):
    def __init__(self, cog: "LojaCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(label="Abrir loja", emoji="🛒", style=discord.ButtonStyle.secondary, custom_id="loja:abrir")
    async def abrir(self, interaction: discord.Interaction, button: ui.Button):
        view = LojaView(self.cog, interaction.user)
        await interaction.response.send_message(embeds=view.embeds(), view=view, ephemeral=True)


# =============================================
#  LOJA (mensagem privada de cada jogador)
# =============================================
class PaginaButton(ui.Button):
    def __init__(self, indice: int, ativa: bool):
        super().__init__(
            label=f"Página {indice + 1}",
            style=discord.ButtonStyle.primary if ativa else discord.ButtonStyle.secondary,
            row=0,
        )
        self.indice = indice

    async def callback(self, interaction: discord.Interaction):
        view: LojaView = self.view
        view.pagina = self.indice
        await view.atualizar(interaction)


MAX_ITENS_POR_VEZ = 5  # limite do Discord: 5 campos por janela (modal) — só afeta a janela de quantidades


class AdicionarSelect(ui.Select):
    """Menu com TODOS os itens da loja, qualquer que seja a página aberta (sem limite de itens)."""

    def __init__(self):
        opcoes = [
            discord.SelectOption(
                label=item["nome"], value=item["key"], description=f"{brl(item['preco'])} — Página {n}"
            )
            for n, pagina in enumerate(PAGINAS, start=1)
            for item in pagina["itens"]
        ]
        super().__init__(
            placeholder="Escolha os itens que quiser comprar",
            min_values=1,
            max_values=len(opcoes),
            options=opcoes,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: LojaView = self.view
        if len(self.values) <= MAX_ITENS_POR_VEZ:
            await interaction.response.send_modal(QuantidadesModal(view, self.values))
            return
        # Mais de 5 itens não cabem numa janela do Discord: entram com 1 unidade
        # e as quantidades são ajustadas depois pelo menu do carrinho.
        for key in self.values:
            view.carrinho.setdefault(key, 1)
        await view.atualizar(
            interaction,
            aviso=(
                f"ℹ️ {len(self.values)} itens adicionados com **1 unidade** cada. "
                "Para mudar as quantidades, use o menu do carrinho (até 5 itens por vez)."
            ),
        )


class QuantidadeSelect(ui.Select):
    """Menu do carrinho: reabre a janela de quantidades do item escolhido."""

    def __init__(self, carrinho: dict[str, int]):
        opcoes = [
            discord.SelectOption(label=f"{q}x {ITENS[k]['nome']}", value=k, description="Alterar quantidade ou remover")
            for k, q in carrinho.items()
        ]
        super().__init__(
            placeholder="Alterar quantidade / remover do carrinho (até 5 por vez)",
            min_values=1,
            max_values=min(MAX_ITENS_POR_VEZ, len(opcoes)),
            options=opcoes,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(QuantidadesModal(self.view, self.values))


class QuantidadesModal(ui.Modal):
    """Janela com um campo de quantidade para cada item escolhido (0 remove do carrinho)."""

    def __init__(self, view: "LojaView", keys: list[str]):
        super().__init__(title="Quantidades (0 remove o item)")
        self.loja_view = view
        self.campos: dict[str, ui.TextInput] = {}
        for key in keys:
            campo = ui.TextInput(
                label=ITENS[key]["nome"][:45],
                default=str(view.carrinho.get(key, 1)),
                max_length=5,
            )
            self.campos[key] = campo
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction):
        novas: dict[str, int] = {}
        for key, campo in self.campos.items():
            try:
                novas[key] = max(0, int(campo.value.strip()))
            except ValueError:
                await interaction.response.send_message(
                    f"⚠️ Quantidade inválida em **{ITENS[key]['nome']}**. Digite apenas números.", ephemeral=True
                )
                return
        for key, qtd in novas.items():
            if qtd:
                self.loja_view.carrinho[key] = qtd
            else:
                self.loja_view.carrinho.pop(key, None)
        await self.loja_view.atualizar(interaction)


class FinalizarButton(ui.Button):
    def __init__(self, desativado: bool):
        super().__init__(label="Finalizar compra", emoji="✅", style=discord.ButtonStyle.success, disabled=desativado, row=3)

    async def callback(self, interaction: discord.Interaction):
        view: LojaView = self.view
        await view.cog.criar_compra(interaction, view)


class LimparButton(ui.Button):
    def __init__(self, desativado: bool):
        super().__init__(label="Limpar carrinho", emoji="🗑️", style=discord.ButtonStyle.secondary, disabled=desativado, row=3)

    async def callback(self, interaction: discord.Interaction):
        view: LojaView = self.view
        view.carrinho.clear()
        await view.atualizar(interaction)


class LojaView(ui.View):
    def __init__(self, cog: "LojaCog", usuario: discord.abc.User):
        super().__init__(timeout=900)
        self.cog = cog
        self.usuario = usuario
        self.pagina = 0
        self.carrinho: dict[str, int] = {}
        self.finalizando = False
        self.montar()

    async def atualizar(self, interaction: discord.Interaction, aviso: Optional[str] = None):
        """Redesenha a loja na mensagem atual; `aviso` aparece acima dos embeds (None limpa)."""
        self.montar()
        await interaction.response.edit_message(content=aviso, embeds=self.embeds(), view=self)

    def montar(self):
        self.clear_items()
        for i in range(len(PAGINAS)):
            self.add_item(PaginaButton(i, ativa=i == self.pagina))
        self.add_item(AdicionarSelect())
        if self.carrinho:
            self.add_item(QuantidadeSelect(self.carrinho))
        self.add_item(FinalizarButton(desativado=not self.carrinho))
        self.add_item(LimparButton(desativado=not self.carrinho))

    def embeds(self) -> list[discord.Embed]:
        pagina = PAGINAS[self.pagina]
        catalogo = discord.Embed(description=pagina["descricao"], color=discord.Color.blue())
        catalogo.set_footer(text=f"Página {self.pagina + 1}/{len(PAGINAS)}")

        carrinho = discord.Embed(title="🛒 Seu carrinho", color=discord.Color.green())
        if self.carrinho:
            carrinho.description = linhas_carrinho(self.carrinho)
            carrinho.set_footer(text=f"Total: {brl(total_carrinho(self.carrinho))}")
        else:
            carrinho.description = "Vazio. Escolha itens no menu abaixo e informe as quantidades."
        return [catalogo, carrinho]


# =============================================
#  CANAL DE COMPRA
# =============================================
class CompraView(ui.View):
    """Botões da staff dentro do canal da compra."""

    def __init__(self, cog: "LojaCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(label="Marcar como pago", emoji="💰", style=discord.ButtonStyle.success, custom_id="loja:pago", row=0)
    async def pago(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.mudar_status(interaction, "paga")

    @ui.button(label="Marcar como entregue", emoji="📦", style=discord.ButtonStyle.primary, custom_id="loja:entregue", row=0)
    async def entregue(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.mudar_status(interaction, "entregue")

    @ui.button(label="Fechar compra", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="loja:fechar", row=0)
    async def fechar(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.fechar_compra(interaction)


# =============================================
#  COG
# =============================================
class LojaCog(commands.Cog):
    def __init__(self, bot: commands.Bot, staff_roles: list[str], armazenamento: Armazenamento):
        self.bot = bot
        self.staff_roles = staff_roles
        self.armazenamento = armazenamento
        self.lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_view(PainelLojaView(self))
        self.bot.add_view(CompraView(self))

    @commands.Cog.listener()
    async def on_ready(self):
        await self._remover_painel_antigo()

    async def _remover_painel_antigo(self):
        """O painel de compras agora é web: apaga a mensagem do painel antigo do Discord, se existir."""
        config = self.armazenamento.dados.get("dashboard")
        if not config:
            return
        try:
            canal = self.bot.get_channel(config["canal_id"]) or await self.bot.fetch_channel(config["canal_id"])
            await (await canal.fetch_message(config["mensagem_id"])).delete()
        except discord.DiscordException:
            pass  # já foi apagada ou o canal sumiu
        self.armazenamento.definir_dashboard(None)

    def eh_staff(self, membro: discord.abc.User) -> bool:
        return isinstance(membro, discord.Member) and any(r.name in self.staff_roles for r in membro.roles)

    # ---------- buggy!loja_painel ----------
    @commands.command(name="loja_painel")
    async def loja_painel(self, ctx: commands.Context, canal: Optional[discord.TextChannel] = None):
        """[Staff] Posta o painel com o botão "Abrir loja" no canal atual (ou no informado)."""
        if not self.eh_staff(ctx.author):
            await ctx.send("⛔ Sem permissão, tripulante!")
            return
        destino = canal or ctx.channel
        embed = discord.Embed(
            title="Loja",
            description="Clique no botão abaixo para abrir a loja e apoiar o servidor.",
            color=discord.Color.green(),
        )
        await destino.send(embed=embed, view=PainelLojaView(self))
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

    # ---------- buggy!loja_acesso ----------
    @commands.command(name="loja_acesso")
    async def loja_acesso(self, ctx: commands.Context, acao: str = "listar", email: Optional[str] = None):
        """[Staff] Controla quem pode entrar no painel web de compras (login com Google)."""
        if not self.eh_staff(ctx.author):
            await ctx.send("⛔ Sem permissão, tripulante!")
            return
        remoto = self.armazenamento.remoto
        if not remoto:
            await ctx.send("⚠️ O painel web precisa do Firebase configurado (veja `buggy!loja_armazenamento`).")
            return

        acao = acao.lower()
        if acao in ("listar", "lista"):
            emails = await asyncio.to_thread(remoto.listar_staff)
            texto = "\n".join(f"• `{e}`" for e in emails) or "Ninguém foi autorizado ainda."
            await ctx.send(f"🔑 **Acesso ao painel web ({len(emails)}):**\n{texto}", delete_after=60)
            return

        email = (email or "").strip().lower()
        if acao not in ("adicionar", "add", "remover", "remove") or not EMAIL_RE.match(email):
            await ctx.send(
                "Use: `buggy!loja_acesso listar`, `buggy!loja_acesso adicionar <email>` "
                "ou `buggy!loja_acesso remover <email>`. O e-mail deve ser o da conta Google usada no login.",
                delete_after=30,
            )
            return
        try:
            await ctx.message.delete()  # não deixa o e-mail exposto no canal
        except (discord.Forbidden, discord.NotFound):
            pass
        try:
            if acao in ("adicionar", "add"):
                await asyncio.to_thread(remoto.adicionar_staff, email, f"{ctx.author} ({ctx.author.id})")
                await ctx.send(f"✅ `{email}` agora pode entrar no painel web.", delete_after=20)
            else:
                existia = await asyncio.to_thread(remoto.remover_staff, email)
                await ctx.send(
                    f"🗑️ Acesso de `{email}` removido." if existia else f"ℹ️ `{email}` não tinha acesso.", delete_after=20
                )
        except Exception as e:
            print(f"[Loja] Erro ao mudar o acesso ao painel web: {type(e).__name__}: {e}")
            await ctx.send("❌ Não consegui falar com o Firebase. Tente de novo em instantes.", delete_after=20)

    # ---------- buggy!loja_armazenamento ----------
    @commands.command(name="loja_armazenamento")
    async def loja_armazenamento(self, ctx: commands.Context):
        """[Staff] Mostra onde os pedidos estão sendo salvos e se o Firebase está funcionando."""
        if not self.eh_staff(ctx.author):
            await ctx.send("⛔ Sem permissão, tripulante!")
            return
        st = self.armazenamento.status()
        if not st["firebase"]:
            situacao = "⚠️ **Só arquivo local** (Firebase não configurado). Os dados somem se a hospedagem trocar os arquivos."
        elif st["firebase_ok_na_partida"] is False:
            situacao = "⚠️ **Firebase configurado, mas não consegui ler na partida.** Usando o arquivo local."
        elif st["ultimo_erro"]:
            situacao = "⚠️ **Firebase conectado, mas a última gravação falhou.** O bot tenta de novo sozinho."
        else:
            situacao = "✅ **Firebase conectado** (com cópia no arquivo local)."
        linhas = [
            situacao,
            f"📦 Pedidos: **{st['pedidos']}** · último ID: **{st['ultimo_id']:02d}**",
        ]
        if st["firebase"]:
            ultima = f"<t:{epoch(st['ultima_sync'])}:R>" if st["ultima_sync"] else "ainda nenhuma nesta sessão"
            linhas.append(f"🔄 Última gravação no Firebase: {ultima} · na fila: **{st['pendentes']}**")
            if st["ultimo_erro"]:
                linhas.append(f"❌ Último erro: `{st['ultimo_erro'][:200]}`")
        await ctx.send("\n".join(linhas))

    # ---------- status da compra (botões da staff) ----------
    async def _compra_do_canal(self, interaction: discord.Interaction):
        """Valida a staff e acha a compra do canal. Devolve (chave, pedido) ou None (já respondeu)."""
        if not self.eh_staff(interaction.user):
            await interaction.response.send_message("⛔ Só a staff pode fazer isso.", ephemeral=True)
            return None
        achado = self.armazenamento.por_canal(interaction.channel.id)
        if not achado:
            await interaction.response.send_message("⚠️ Não encontrei os dados desta compra no registro.", ephemeral=True)
        return achado

    async def mudar_status(self, interaction: discord.Interaction, novo: str):
        achado = await self._compra_do_canal(interaction)
        if not achado:
            return
        chave, pedido = achado
        atual = pedido["status"]
        autor = f"{interaction.user} ({interaction.user.id})"

        if novo == "paga":
            if atual != "aguardando_pagamento":
                await interaction.response.send_message(f"ℹ️ Esta compra já está: **{STATUS[atual]}**.", ephemeral=True)
                return
            campos = {"status": "paga", "pago_em": agora_iso(), "atualizado_por": autor}
            aviso = f"💰 Pagamento confirmado por {interaction.user.mention}. A entrega será feita aqui neste canal."
        else:
            if atual == "aguardando_pagamento":
                await interaction.response.send_message("⚠️ Marque a compra como **paga** antes de entregar.", ephemeral=True)
                return
            if atual != "paga":
                await interaction.response.send_message(f"ℹ️ Esta compra já está: **{STATUS[atual]}**.", ephemeral=True)
                return
            campos = {"status": "entregue", "entregue_em": agora_iso(), "atualizado_por": autor}
            aviso = f"📦 Compra marcada como **entregue** por {interaction.user.mention}. Obrigado por apoiar o servidor! 🤡"

        self.armazenamento.atualizar(chave, **campos)

        # Atualiza o campo "Status" do resumo; some o QR/PIX, que não é mais necessário.
        resumo = discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
        for n, campo in enumerate(resumo.fields):
            if campo.name == "Status":
                resumo.set_field_at(n, name="Status", value=STATUS[campos["status"]], inline=False)
        await interaction.response.edit_message(embeds=[resumo], attachments=[], view=CompraView(self))
        await interaction.channel.send(aviso)

    async def fechar_compra(self, interaction: discord.Interaction):
        achado = await self._compra_do_canal(interaction)
        if not achado:
            return
        chave, pedido = achado
        campos = {"fechado_em": agora_iso(), "atualizado_por": f"{interaction.user} ({interaction.user.id})"}
        if pedido["status"] == "aguardando_pagamento":
            campos["status"] = "cancelada"  # fechou sem pagar
        self.armazenamento.atualizar(chave, **campos)
        await interaction.response.send_message("🔒 Compra encerrada. Este canal será apagado em 5 segundos...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Compra encerrada por {interaction.user}")
        except discord.HTTPException:
            pass

    # ---------- finalizar compra ----------
    async def criar_compra(self, interaction: discord.Interaction, view: LojaView):
        if view.finalizando:
            await interaction.response.defer()
            return
        if not view.carrinho:
            await interaction.response.send_message("⚠️ Seu carrinho está vazio.", ephemeral=True)
            return

        view.finalizando = True
        await interaction.response.defer()
        guild = interaction.guild
        membro = interaction.user
        carrinho = dict(view.carrinho)
        total = total_carrinho(carrinho)

        cargos_staff = [r for r in (discord.utils.get(guild.roles, name=n) for n in self.staff_roles) if r]
        cargo_alerta = guild.get_role(LOJA_CARGO_ALERTA_ID) if LOJA_CARGO_ALERTA_ID else None
        cargos_acesso = list({r.id: r for r in cargos_staff + ([cargo_alerta] if cargo_alerta else [])}.values())

        def acesso(**extra) -> discord.PermissionOverwrite:
            return discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True, **extra,
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                embed_links=True, manage_channels=True,
            ),
            membro: acesso(),
            **{r: acesso(manage_messages=True) for r in cargos_acesso},
        }
        categoria = guild.get_channel(LOJA_CATEGORIA_ID) if LOJA_CATEGORIA_ID else None
        if not isinstance(categoria, discord.CategoryChannel):
            categoria = None

        async with self.lock:
            compra_id = self.armazenamento.proximo_id()
            nome_canal = f"{slug_nick(membro.display_name)}-{compra_id:02d}"
            try:
                canal = await guild.create_text_channel(
                    nome_canal,
                    category=categoria,
                    overwrites=overwrites,
                    topic=f"Compra {compra_id:02d} — comprador: {membro} ({membro.id})",
                    reason=f"Compra {compra_id:02d} de {membro}",
                )
            except discord.HTTPException as e:
                view.finalizando = False
                print(f"[Loja] Erro ao criar canal da compra: {e}")
                await interaction.followup.send(
                    "❌ Não consegui abrir o canal da compra (o bot precisa da permissão **Gerenciar Canais**). "
                    "Avise a staff.",
                    ephemeral=True,
                )
                return
            self.armazenamento.registrar(compra_id, {
                "id": compra_id,
                "usuario_id": membro.id,
                "usuario": str(membro),
                "nome": membro.display_name,
                "canal_id": canal.id,
                "itens": carrinho,
                "itens_detalhe": [
                    {"nome": ITENS[k]["nome"], "qtd": q, "preco_centavos": ITENS[k]["preco"]} for k, q in carrinho.items()
                ],
                "total_centavos": total,
                "criado_em": agora_iso(),
                "status": "aguardando_pagamento",
                "pago_em": None,
                "entregue_em": None,
                "fechado_em": None,
                "atualizado_por": None,
            })

        embed = discord.Embed(
            title=f"🛒 Compra {compra_id:02d}",
            description=linhas_carrinho(carrinho),
            color=discord.Color.green(),
        )
        embed.add_field(name="Total a pagar", value=f"**{brl(total)}**", inline=False)
        embed.add_field(name="Comprador", value=membro.mention, inline=False)
        embed.add_field(name="Status", value=STATUS["aguardando_pagamento"], inline=False)

        embeds, arquivos = [embed], []
        if PIX_COPIA_COLA:
            codigo = pix.payload_com_valor(PIX_COPIA_COLA, total)
            embed_pix = discord.Embed(
                title="💳 Pagamento via PIX",
                description=(
                    f"Pague **{brl(total)}** escaneando o QR Code ou usando o PIX copia e cola abaixo.\n"
                    "Depois **envie o comprovante aqui neste canal**: a staff confirma o pagamento e faz a entrega."
                ),
                color=discord.Color.gold(),
            )
            embed_pix.add_field(name="Beneficiário", value=pix.beneficiario(codigo) or "—", inline=False)
            embed_pix.add_field(name="PIX copia e cola", value=f"```{codigo}```", inline=False)
            embed_pix.set_footer(text="Confira o valor e o beneficiário antes de pagar.")
            try:
                png = await asyncio.to_thread(pix.qr_png, codigo)
                arquivos.append(discord.File(io.BytesIO(png), filename="pix.png"))
                embed_pix.set_image(url="attachment://pix.png")
            except Exception as e:  # sem QR ainda dá para pagar pelo copia e cola
                print(f"[Loja] Falha ao gerar o QR Code do PIX: {e}")
            embeds.append(embed_pix)
            embed.set_footer(text="Após pagar, envie o comprovante neste canal.")
        else:
            embed.set_footer(text="A staff vai combinar o pagamento e a entrega aqui neste canal.")

        mencoes = " ".join([membro.mention] + [r.mention for r in cargos_acesso])
        await canal.send(
            f"{mencoes}\nNovo pedido aberto! Aguarde a staff atendê-lo(a) por aqui. 🤡",
            embeds=embeds,
            files=arquivos,
            view=CompraView(self),
        )

        view.stop()
        await interaction.edit_original_response(
            content=f"✅ Compra **{compra_id:02d}** criada! Continue o atendimento em {canal.mention}.",
            embeds=[],
            view=None,
        )


async def setup_loja(bot: commands.Bot, staff_roles: list[str]):
    armazenamento = await Armazenamento.criar(LOJA_ARQUIVO)
    await bot.add_cog(LojaCog(bot, staff_roles, armazenamento))
