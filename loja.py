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
from beneficios import (BeneficiosMixin, GACHA_A_CADA_CENTAVOS, REGISTRO_CANAL_ID, NIVEIS, gachas)
from catalogo import (CHAVE_INDICACAO, ITEM_INDICACAO, CatalogoSync, assinatura_precos, brl, catalogo_padrao,
                      com_item_fixo, preco_txt)
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
# Quantas compras aguardando pagamento cada pessoa pode ter ao mesmo tempo (0 = sem limite).
# Evita que alguém encha o servidor de canais abrindo compras em sequência.
LOJA_MAX_ABERTAS = int(os.getenv("LOJA_MAX_ABERTAS", "3") or 0)
# E-mail "normal" e estrito: o endereço vira o nome de um documento no banco, então nada de "/", ".." ou aspas.
EMAIL_RE = re.compile(
    r"^(?=.{3,254}$)(?!.*\.\.)[a-z0-9][a-z0-9._%+'\-]{0,63}@"
    r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)

# PIX copia e cola da conta que recebe os pagamentos (guarde no .env, nunca no código).
# O bot embute o valor de cada compra e gera o QR Code a partir dele.
PIX_COPIA_COLA = (os.getenv("PIX_COPIA_COLA") or "").strip()
PIX_MOTIVO = ""  # por que o PIX está desativado ("ausente" ou "invalido"); vazio = ativo
if not PIX_COPIA_COLA:
    PIX_MOTIVO = "ausente"
    print("[Loja] ATENÇÃO: PIX_COPIA_COLA não chegou ao bot. Os tickets vão abrir SEM o PIX. "
          "Confira se o .env enviado no deploy tem essa linha.")
elif not pix.payload_valido(PIX_COPIA_COLA):
    PIX_MOTIVO = "invalido"
    print("[Loja] ATENÇÃO: PIX_COPIA_COLA inválido (o CRC não confere; copie o código inteiro). PIX desativado.")
    PIX_COPIA_COLA = ""
else:
    print(f"[Loja] PIX ativo (beneficiário: {pix.beneficiario(PIX_COPIA_COLA) or '?'}).")

STATUS = {
    "aguardando_pagamento": "🟡 Aguardando pagamento",
    "paga": "💰 Paga — a entregar",
    "entregue": "📦 Entregue",
    "cancelada": "❌ Cancelada",
}

# =============================================
#  CATÁLOGO (editável pelo painel web; veja catalogo.py)
# =============================================
# PAGINAS e ITENS são trocados por inteiro, de uma vez, quando a staff edita o catálogo. As telas conferem
# o catálogo a cada clique e nunca confiam numa cópia antiga (veja LojaView._sincronizar e criar_compra).
PAGINAS, ITENS = catalogo_padrao()
ITENS = com_item_fixo(ITENS)
ASSINATURA = assinatura_precos(ITENS)    # muda quando um preço muda ou um item entra/sai
CATALOGO_FONTE = "padrao"                # "firebase" | "cache" | "padrao"


def aplicar_catalogo(paginas: list, itens: dict, fonte: str = "padrao"):
    """Troca o catálogo em uso. Chamada pela sincronização ao vivo, que roda em outra thread."""
    global PAGINAS, ITENS, ASSINATURA, CATALOGO_FONTE
    ITENS, PAGINAS = com_item_fixo(itens), paginas
    ASSINATURA, CATALOGO_FONTE = assinatura_precos(ITENS), fonte


# =============================================
#  UTILITÁRIOS
# =============================================
def slug_nick(nome: str) -> str:
    """Converte um nick em algo válido para nome de canal (ex.: 'Percy Jackson' -> 'percy-jackson')."""
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    nome = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    return nome[:80] or "cliente"


def total_carrinho(carrinho: dict[str, int]) -> int:
    return sum(ITENS[k]["preco"] * q for k, q in carrinho.items() if k in ITENS)


def linhas_carrinho(carrinho: dict[str, int]) -> str:
    return "\n".join(
        f"**{q}x** {ITENS[k]['nome']} — {preco_txt(ITENS[k]['preco'] * q)}" for k, q in carrinho.items() if k in ITENS
    )


def linhas_detalhes(detalhes: list[dict]) -> str:
    """Linhas do pedido a partir da fotografia tirada na compra (nome e preço daquele momento)."""
    return "\n".join(f"**{d['qtd']}x** {d['nome']} — {preco_txt(d['preco_centavos'] * d['qtd'])}" for d in detalhes)


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

    @ui.button(label="Benefícios do mês", emoji="🎁", style=discord.ButtonStyle.success, custom_id="loja:beneficios")
    async def beneficios(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.abrir_beneficios(interaction)


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
AVISO_SAIU_DA_LOJA = "⚠️ Esses itens saíram da loja enquanto você escolhia. Confira a lista atualizada."


class AdicionarSelect(ui.Select):
    """Menu com TODOS os itens da loja, qualquer que seja a página aberta (sem limite de itens)."""

    def __init__(self):
        opcoes = [discord.SelectOption(label=ITEM_INDICACAO["nome"], value=CHAVE_INDICACAO, description="Grátis — opção fixa, para indicações")]
        opcoes += [
            discord.SelectOption(
                label=item["nome"][:100], value=item["key"], description=f"{brl(item['preco'])} — Página {n}"
            )
            for n, pagina in enumerate(PAGINAS, start=1)
            for item in pagina["itens"]
            if item["key"] != CHAVE_INDICACAO
        ][:24]
        super().__init__(
            placeholder="Escolha os itens que quiser comprar",
            min_values=1,
            max_values=len(opcoes),
            options=opcoes,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: LojaView = self.view
        chaves = [k for k in self.values if k in ITENS]          # o catálogo pode ter mudado desde que o menu abriu
        if not chaves:
            await view.atualizar(interaction, aviso=AVISO_SAIU_DA_LOJA)
            return
        if len(chaves) <= MAX_ITENS_POR_VEZ:
            await interaction.response.send_modal(QuantidadesModal(view, chaves))
            return
        # Mais de 5 itens não cabem numa janela do Discord: entram com 1 unidade
        # e as quantidades são ajustadas depois pelo menu do carrinho.
        for key in chaves:
            view.carrinho.setdefault(key, 1)
        await view.atualizar(
            interaction,
            aviso=(
                f"ℹ️ {len(chaves)} itens adicionados com **1 unidade** cada. "
                "Para mudar as quantidades, use o menu do carrinho (até 5 itens por vez)."
            ),
        )


class QuantidadeSelect(ui.Select):
    """Menu do carrinho: reabre a janela de quantidades do item escolhido."""

    def __init__(self, carrinho: dict[str, int]):
        opcoes = [
            discord.SelectOption(label=f"{q}x {ITENS[k]['nome']}"[:100], value=k, description="Alterar quantidade ou remover")
            for k, q in carrinho.items() if k in ITENS
        ][:25]
        super().__init__(
            placeholder="Alterar quantidade / remover do carrinho (até 5 por vez)",
            min_values=1,
            max_values=min(MAX_ITENS_POR_VEZ, len(opcoes)),
            options=opcoes,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        chaves = [k for k in self.values if k in ITENS]
        if not chaves:
            await self.view.atualizar(interaction, aviso=AVISO_SAIU_DA_LOJA)
            return
        await interaction.response.send_modal(QuantidadesModal(self.view, chaves))


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
                nome = ITENS.get(key, {}).get("nome", key)
                await interaction.response.send_message(
                    f"⚠️ Quantidade inválida em **{nome}**. Digite apenas números.", ephemeral=True
                )
                return
        for key, qtd in novas.items():
            if key not in ITENS:                       # saiu da loja enquanto a janela estava aberta
                continue
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
        self.assinatura = ASSINATURA        # os preços que a pessoa viu pela última vez
        self.montar()

    def _sincronizar(self) -> tuple[list[str], bool]:
        """Alinha esta tela ao catálogo atual: tira do carrinho o que saiu da loja e confere se algo mudou.

        Devolve (itens removidos do carrinho, se preços/itens mudaram desde a última vez que a pessoa viu).
        """
        removidos = [k for k in self.carrinho if k not in ITENS]
        for k in removidos:
            del self.carrinho[k]
        self.pagina = max(0, min(self.pagina, len(PAGINAS) - 1))
        mudou = self.assinatura != ASSINATURA
        self.assinatura = ASSINATURA
        return removidos, mudou

    async def atualizar(self, interaction: discord.Interaction, aviso: Optional[str] = None):
        """Redesenha a loja na mensagem atual; `aviso` aparece acima dos embeds (None limpa)."""
        removidos, mudou = self._sincronizar()
        if aviso is None and (removidos or mudou):
            aviso = "ℹ️ O catálogo da loja foi atualizado: confira os itens e os preços." + (
                " Tirei do carrinho o que saiu da loja." if removidos else "")
        self.montar()
        await interaction.response.edit_message(content=aviso, embeds=self.embeds(), view=self)

    def montar(self):
        self.clear_items()
        for i in range(len(PAGINAS)):
            self.add_item(PaginaButton(i, ativa=i == self.pagina))
        if ITENS:
            self.add_item(AdicionarSelect())
        if self.carrinho:
            self.add_item(QuantidadeSelect(self.carrinho))
        self.add_item(FinalizarButton(desativado=not self.carrinho))
        self.add_item(LimparButton(desativado=not self.carrinho))

    def embeds(self) -> list[discord.Embed]:
        if PAGINAS:
            indice = max(0, min(self.pagina, len(PAGINAS) - 1))
            catalogo = discord.Embed(description=PAGINAS[indice]["descricao"] or "(esta página está vazia)", color=discord.Color.blue())
            catalogo.set_footer(text=f"Página {indice + 1}/{len(PAGINAS)}")
        else:
            catalogo = discord.Embed(description="🛒 A loja está sem itens no momento. Volte mais tarde!", color=discord.Color.blue())
        catalogo.add_field(name=f"🤝 {ITEM_INDICACAO['nome']} — Grátis (em todas as páginas)", value=ITEM_INDICACAO["descricao"], inline=False)

        carrinho = discord.Embed(title="🛒 Seu carrinho", color=discord.Color.green())
        if self.carrinho:
            carrinho.description = linhas_carrinho(self.carrinho)
            carrinho.set_footer(text=f"Total: {preco_txt(total_carrinho(self.carrinho))}")
        else:
            carrinho.description = "Vazio. Escolha itens no menu abaixo e informe as quantidades." if ITENS else "Vazio."
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
class LojaCog(BeneficiosMixin, commands.Cog):
    def __init__(self, bot: commands.Bot, staff_roles: list[str], armazenamento: Armazenamento):
        self.bot = bot
        self.staff_roles = staff_roles
        self.armazenamento = armazenamento
        self.catalogo_sync: Optional[CatalogoSync] = None
        self.lock = asyncio.Lock()
        self._iniciar_beneficios()

    async def cog_unload(self):
        self._parar_manutencao()
        if self.catalogo_sync:
            self.catalogo_sync.parar()

    # ---------- apoio para a lógica de benefícios e do ticket ----------
    @staticmethod
    def _itens() -> dict:
        return ITENS

    @staticmethod
    def _slug(nome: str) -> str:
        return slug_nick(nome)

    def _overwrites_ticket(self, guild: discord.Guild, membro: discord.Member):
        """Permissões do canal privado de uma compra: só o comprador, o bot e a staff. Devolve (overwrites, cargos de acesso)."""
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
        return overwrites, cargos_acesso

    @staticmethod
    def _categoria(guild: discord.Guild):
        categoria = guild.get_channel(LOJA_CATEGORIA_ID) if LOJA_CATEGORIA_ID else None
        return categoria if isinstance(categoria, discord.CategoryChannel) else None

    async def cog_load(self):
        self.bot.add_view(PainelLojaView(self))
        self.bot.add_view(CompraView(self))

    @commands.Cog.listener()
    async def on_ready(self):
        await self._remover_painel_antigo()
        self._iniciar_manutencao()      # retoma anúncios e resgates que ficaram pela metade, agora e a cada 2 minutos

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
        """[Staff] Controla quem entra no painel web (login com Google) e quem pode EDITAR o catálogo."""
        if not self.eh_staff(ctx.author):
            await ctx.send("⛔ Sem permissão, tripulante!")
            return
        remoto = self.armazenamento.remoto
        if not remoto:
            await ctx.send("⚠️ O painel web precisa do Firebase configurado (veja `buggy!loja_armazenamento`).")
            return

        acao = acao.lower()
        if acao in ("listar", "lista"):
            acessos = await asyncio.to_thread(remoto.listar_acessos)
            texto = "\n".join(f"• {'✏️ edita' if editor else '👁️ só vê'} — `{e}`" for e, editor in acessos) or "Ninguém foi autorizado ainda."
            await ctx.send(f"🔑 **Acesso ao painel web ({len(acessos)}):**\n{texto}", delete_after=60)
            return

        niveis = {"adicionar": None, "add": None, "editor": True, "editar": True, "visualizar": False, "ver": False}
        email = (email or "").strip().lower()
        if (acao not in niveis and acao not in ("remover", "remove")) or not EMAIL_RE.match(email):
            await ctx.send(
                "Use: `buggy!loja_acesso listar`, `buggy!loja_acesso adicionar <email>` (só vê), "
                "`buggy!loja_acesso editor <email>` (vê e edita o catálogo), `buggy!loja_acesso visualizar <email>` "
                "ou `buggy!loja_acesso remover <email>`. O e-mail deve ser o da conta Google usada no login.",
                delete_after=30,
            )
            return
        try:
            await ctx.message.delete()  # não deixa o e-mail exposto no canal
        except (discord.Forbidden, discord.NotFound):
            pass
        por = f"{ctx.author} ({ctx.author.id})"
        try:
            if acao in niveis:
                novo = await asyncio.to_thread(remoto.definir_acesso, email, por, niveis[acao])
                texto = ("✏️ `{}` agora pode **editar o catálogo** no painel web." if novo["editor"]
                         else "👁️ `{}` pode entrar no painel web, **só para ver** (use `editor` para permitir edição).").format(email)
                await ctx.send(texto, delete_after=20)
            else:
                existia = await asyncio.to_thread(remoto.remover_staff, email)
                await ctx.send(
                    f"🗑️ Acesso de `{email}` removido." if existia else f"ℹ️ `{email}` não tinha acesso.", delete_after=20
                )
        except Exception as e:
            print(f"[Loja] Erro ao mudar o acesso ao painel web: {type(e).__name__}: {e}")
            await ctx.send("❌ Não consegui falar com o Firebase. Tente de novo em instantes.", delete_after=20)

    # ---------- buggy!loja_armazenamento ----------
    @commands.command(name="loja_armazenamento", aliases=["loja_status", "loja_diagnostico"])
    async def loja_armazenamento(self, ctx: commands.Context):
        """[Staff] Diagnóstico da loja: armazenamento (Firebase), PIX e permissões do bot."""
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
        linhas += self._diagnostico_catalogo() + self._diagnostico_pix() + self._diagnostico_permissoes(ctx.guild) + self._diagnostico_registro(ctx.guild)
        await ctx.send("\n".join(linhas))

    def _diagnostico_registro(self, guild: Optional[discord.Guild]) -> list[str]:
        """Canal público de registro das compras e cargos dos benefícios mensais."""
        linhas = []
        canal = self.bot.get_channel(REGISTRO_CANAL_ID) if REGISTRO_CANAL_ID else None
        if canal is None:
            linhas.append(f"❌ **Canal de registro das compras não encontrado** (`{REGISTRO_CANAL_ID}`): os anúncios ficam pendentes.")
        else:
            perms = canal.permissions_for(guild.me) if guild else None
            faltam = [n for a, n in (("send_messages", "Enviar Mensagens"), ("embed_links", "Inserir Links"), ("view_channel", "Ver Canal"),
                                     ("read_message_history", "Ver Histórico")) if perms is not None and not getattr(perms, a, False)]
            linhas.append(f"✅ Registro público de compras em {canal.mention}" if not faltam
                          else f"❌ O bot não pode postar em {canal.mention}: faltam {', '.join(faltam)}.")
        if guild is not None:
            for nivel, cfg in NIVEIS.items():
                if not guild.get_role(cfg["cargo_id"]):
                    linhas.append(f"⚠️ Cargo da assinatura nível {nivel} não encontrado (`{cfg['cargo_id']}`).")
        pendentes = sum(1 for p in self.armazenamento.dados["pedidos"].values()
                        if p.get("status") in ("paga", "entregue") and not p.get("anuncio_msg_id"))
        if pendentes:
            linhas.append(f"⏳ {pendentes} compra(s) aprovada(s) ainda sem registro no canal público (o bot tenta de novo sozinho).")
        return linhas

    @staticmethod
    def _diagnostico_catalogo() -> list[str]:
        fonte = {"firebase": "✅ Firebase, ao vivo (editado pelo painel)",
                 "cache": "⚠️ cópia local (o Firebase não respondeu na partida)",
                 "padrao": "⚠️ catálogo padrão do código (sem Firebase e sem cópia local)"}.get(CATALOGO_FONTE, CATALOGO_FONTE)
        return [f"📚 Catálogo: **{sum(1 for i in ITENS.values() if not i.get('fixo'))}** item(ns) em **{len(PAGINAS)}** página(s) — {fonte}"]

    @staticmethod
    def _diagnostico_pix() -> list[str]:
        if PIX_COPIA_COLA:
            try:  # teste real: gera o QR Code do PIX configurado
                tamanho = len(pix.qr_png(pix.payload_com_valor(PIX_COPIA_COLA, 500)))
                qr = f"✅ QR Code gerando normalmente ({tamanho // 1024 or 1} KB)"
            except Exception as e:
                qr = f"❌ **QR Code NÃO está sendo gerado:** `{type(e).__name__}: {str(e)[:120]}`"
            return [f"✅ **PIX ativo** (beneficiário: **{pix.beneficiario(PIX_COPIA_COLA) or '?'}**)", qr]
        if PIX_MOTIVO == "invalido":
            return ["❌ **PIX desativado:** o código em `PIX_COPIA_COLA` não passou na conferência (CRC). Copie de novo, inteiro."]
        return ["❌ **PIX desativado:** a variável `PIX_COPIA_COLA` não chegou ao bot. Confira o `.env` enviado no deploy e reinicie."]

    @staticmethod
    def _diagnostico_permissoes(guild: Optional[discord.Guild]) -> list[str]:
        """Confere o que o bot precisa para abrir o canal da compra e enviar o QR Code."""
        if guild is None:
            return []
        necessarias = [
            ("manage_channels", "Gerenciar Canais"), ("send_messages", "Enviar Mensagens"), ("embed_links", "Inserir Links"),
            ("attach_files", "Anexar Arquivos"), ("read_message_history", "Ver Histórico de Mensagens"),
            ("manage_messages", "Gerenciar Mensagens"),
        ]
        categoria = guild.get_channel(LOJA_CATEGORIA_ID) if LOJA_CATEGORIA_ID else None
        linhas = []
        if LOJA_CATEGORIA_ID and not isinstance(categoria, discord.CategoryChannel):
            linhas.append(f"⚠️ **Categoria não encontrada** (`LOJA_CATEGORIA_ID={LOJA_CATEGORIA_ID}`): os canais ficam soltos.")
        elif categoria is None:
            linhas.append("ℹ️ Sem categoria configurada: os canais de compra ficam soltos no servidor.")
        else:
            linhas.append(f"✅ Categoria das compras: **{categoria.name}**")
        permissoes = categoria.permissions_for(guild.me) if categoria is not None else guild.me.guild_permissions
        faltando = [nome for attr, nome in necessarias if not getattr(permissoes, attr, False)]
        onde = f"na categoria **{categoria.name}**" if categoria is not None else "no servidor"
        if faltando:
            linhas.append(f"❌ **Faltam permissões ao bot {onde}:** {', '.join(faltando)}. "
                          "Sem **Anexar Arquivos** o QR Code não é enviado; sem **Gerenciar Canais** a compra nem abre.")
        else:
            linhas.append(f"✅ Permissões do bot {onde}: tudo certo para abrir o canal e enviar o QR Code.")
        return linhas

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
            agora = agora_iso()
            campos = {"status": "paga", "pago_em": agora, "atualizado_por": autor, "aprovado_em": agora,
                      "aprovado_por_id": interaction.user.id, "aprovado_por_nome": interaction.user.display_name}
            aviso = (f"✅ Aprovado por {interaction.user.mention}. A entrega será feita aqui neste canal." if not pedido.get("total_centavos")
                     else f"💰 Pagamento confirmado por {interaction.user.mention}. A entrega será feita aqui neste canal.")
            n_gachas = gachas(pedido.get("total_centavos", 0))
            if n_gachas:
                aviso += f"\n🎰 Você ganhou **{n_gachas} gacha(s)** com esta compra (1 a cada {brl(GACHA_A_CADA_CENTAVOS)})!"
        else:
            if atual == "aguardando_pagamento":
                await interaction.response.send_message("⚠️ Marque a compra como **paga** antes de entregar.", ephemeral=True)
                return
            if atual != "paga":
                await interaction.response.send_message(f"ℹ️ Esta compra já está: **{STATUS[atual]}**.", ephemeral=True)
                return
            campos = {"status": "entregue", "entregue_em": agora_iso(), "atualizado_por": autor,
                      "entregue_por_id": interaction.user.id, "entregue_por_nome": interaction.user.display_name}
            aviso = f"📦 Compra marcada como **entregue** por {interaction.user.mention}. Obrigado por apoiar o servidor! 🤡"

        self.armazenamento.atualizar(chave, **campos)

        # Atualiza o campo "Status" do resumo; some o QR/PIX, que não é mais necessário.
        resumo = discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
        for n, campo in enumerate(resumo.fields):
            if campo.name == "Status":
                resumo.set_field_at(n, name="Status", value=STATUS[campos["status"]], inline=False)
        await interaction.response.edit_message(embeds=[resumo], attachments=[], view=CompraView(self))
        await interaction.channel.send(aviso)
        # Registro público (para os jogadores conferirem). Nunca levanta erro: se falhar, fica pendente e é retomado depois.
        if novo == "paga":
            await self.anunciar_compra(chave)
        else:
            await self.atualizar_anuncio(chave)

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

        # A staff pode ter editado o catálogo desde a última vez que a pessoa viu o carrinho. Nunca se cobra
        # um preço que ela não viu: mostra tudo de novo e pede para finalizar outra vez.
        if view.assinatura != ASSINATURA or any(k not in ITENS for k in view.carrinho):
            await view.atualizar(interaction, aviso=(
                "⚠️ O catálogo foi atualizado desde a última vez que você viu o carrinho. "
                "Confira os itens e os preços abaixo e clique em **Finalizar compra** de novo."))
            return

        # Fotografia do pedido, tirada AGORA e sem esperas no meio: o que vale é o nome e o preço deste momento.
        carrinho = dict(view.carrinho)
        detalhes = [{"nome": ITENS[k]["nome"], "qtd": q, "preco_centavos": ITENS[k]["preco"]} for k, q in carrinho.items()]
        total = sum(d["qtd"] * d["preco_centavos"] for d in detalhes)

        view.finalizando = True
        await interaction.response.defer()
        guild = interaction.guild
        membro = interaction.user

        overwrites, cargos_acesso = self._overwrites_ticket(guild, membro)
        categoria = self._categoria(guild)

        async with self.lock:
            # A contagem precisa ficar DENTRO da trava: fora dela, vários pedidos da mesma pessoa
            # chegando juntos passariam todos pela checagem antes de qualquer um ser registrado.
            abertas = sum(
                1 for p in self.armazenamento.dados["pedidos"].values()
                if p.get("usuario_id") == membro.id and p.get("status") == "aguardando_pagamento" and not p.get("fechado_em")
            )
            if LOJA_MAX_ABERTAS and abertas >= LOJA_MAX_ABERTAS:
                view.finalizando = False
                await interaction.followup.send(
                    f"⚠️ Você já tem **{abertas}** compra(s) aguardando pagamento (o limite é {LOJA_MAX_ABERTAS}). "
                    "Conclua uma delas, ou peça à staff para fechá-la, antes de abrir outra.",
                    ephemeral=True,
                )
                return
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
                "itens_detalhe": detalhes,
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
            description=linhas_detalhes(detalhes),
            color=discord.Color.green(),
        )
        embed.add_field(name="Total a pagar", value=f"**{preco_txt(total)}**", inline=False)
        embed.add_field(name="Comprador", value=membro.mention, inline=False)
        if CHAVE_INDICACAO in carrinho:
            embed.add_field(name="🤝 Indicação", value="Escreva aqui neste canal **quem você indicou** (ou **quem indicou você**): o @ ou o ID da pessoa. A staff confere e aprova.", inline=False)
        embed.add_field(name="Status", value=STATUS["aguardando_pagamento"], inline=False)

        embeds, arquivos, embed_pix, codigo = [embed], [], None, ""
        if total == 0:
            embed.set_footer(text="Compra sem custo: não há nada a pagar. A staff confere e aprova por aqui.")
        elif PIX_COPIA_COLA:
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
                print(f"[Loja] Falha ao gerar o QR Code do PIX ({type(e).__name__}: {e}).")
                embed_pix.description += "\n\n⚠️ Não consegui gerar o QR Code agora. Use o PIX copia e cola abaixo."
            embeds.append(embed_pix)
            embed.set_footer(text="Após pagar, envie o comprovante neste canal.")
        else:
            embed.add_field(name="Pagamento", value="O PIX não está disponível no momento: a staff vai enviar os dados de pagamento aqui.", inline=False)
            embed.set_footer(text="A staff vai combinar o pagamento e a entrega aqui neste canal.")

        mencoes = " ".join([membro.mention] + [r.mention for r in cargos_acesso])
        texto_simples = f"**Compra {compra_id:02d}** — total **{preco_txt(total)}**\n{linhas_detalhes(detalhes)}"
        if codigo:
            texto_simples += f"\n\n💳 **PIX copia e cola:**\n```{codigo}```\nDepois envie o comprovante aqui."
        enviado = await self._enviar_ticket(
            canal, f"{mencoes}\nNovo pedido aberto! Aguarde a staff atendê-lo(a) por aqui. 🤡",
            embeds, arquivos, embed_pix, texto_simples,
        )

        view.stop()
        aviso = "" if enviado else "\n⚠️ Não consegui enviar os detalhes no canal. Avise a staff."
        await interaction.edit_original_response(
            content=f"✅ Compra **{compra_id:02d}** criada! Continue o atendimento em {canal.mention}.{aviso}",
            embeds=[],
            view=None,
        )

    async def _enviar_ticket(self, canal, conteudo, embeds, arquivos, embed_pix, texto_simples) -> bool:
        """Envia o resumo e o PIX no canal da compra, com plano B se o Discord recusar.

        1) completo (embeds + QR Code); 2) sem o QR Code (o bot pode estar sem "Anexar Arquivos"),
        mantendo o copia e cola; 3) só texto. A compra já foi registrada, então o canal nunca
        pode ficar vazio. Devolve False só se nenhuma das três funcionar.
        """
        try:
            await canal.send(content=conteudo, embeds=embeds, files=arquivos, view=CompraView(self))
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[Loja] Não consegui enviar o ticket completo ({type(e).__name__}: {e}). Tentando sem o QR Code.")

        if arquivos and embed_pix is not None:
            embed_pix.set_image(url=None)
            try:
                await canal.send(
                    content=conteudo + "\n⚠️ Não consegui anexar o QR Code (o bot precisa da permissão **Anexar Arquivos**). "
                                       "Use o PIX copia e cola abaixo.",
                    embeds=embeds, view=CompraView(self),
                )
                return True
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"[Loja] Também falhou sem o QR Code ({type(e).__name__}: {e}). Tentando só texto.")

        try:
            await canal.send(content=conteudo + "\n\n" + texto_simples, view=CompraView(self))
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[Loja] Não consegui enviar NADA no canal da compra ({type(e).__name__}: {e}). "
                  "Confira as permissões do bot (use buggy!loja_armazenamento).")
            return False


async def setup_loja(bot: commands.Bot, staff_roles: list[str]):
    armazenamento = await Armazenamento.criar(LOJA_ARQUIVO)
    sync = CatalogoSync(armazenamento.remoto, aplicar_catalogo)
    await asyncio.to_thread(sync.iniciar)          # semeia (1ª vez), carrega e passa a acompanhar o catálogo ao vivo
    cog = LojaCog(bot, staff_roles, armazenamento)
    cog.catalogo_sync = sync
    await bot.add_cog(cog)
