"""Registro público de compras, gachas e benefícios mensais das assinaturas.

Duas funcionalidades que se apoiam no mesmo registro de pedidos:

1. REGISTRO PÚBLICO. Quando a staff aprova uma compra, o bot posta um embed no canal de registro (visível aos
   jogadores, ao contrário do painel, que é só da staff) com quem comprou, quem aprovou, quando, o nº da compra,
   os itens e os gachas (1 a cada R$ 20,00 da compra). Na entrega, o mesmo post é editado.

2. BENEFÍCIOS MENSAIS. Quem tem o cargo de assinante resgata, pelo botão do painel, os itens do mês (os fixos entram
   sozinhos; os VIPs "à escolha, de até R$ 15,00" o jogador escolhe). Só dá para resgatar 1 vez por mês.

Como a "1 vez por mês" não é burlada
  - a trava é um documento no Firebase com id `AAAA-MM-<id do jogador>`, criado com `create()`, que o servidor
    recusa de forma atômica se já existir: vale mesmo com dois cliques ao mesmo tempo, bot reiniciado, arquivo
    local apagado ou duas cópias do bot rodando;
  - o mês vem do horário do Discord (não do relógio da máquina) e do fuso de Brasília;
  - sem o Firebase o resgate é RECUSADO (nunca liberado "por engano");
  - o cargo é conferido no momento do resgate e as escolhas são revalidadas no servidor (o que veio do menu não é
    confiável).

Como ele continua de onde parou se o bot cair
  - o resgate é uma máquina de estados gravada no Firebase (reservado -> ticket -> concluído) e cada passo é idempotente;
    ao iniciar, e a cada 2 minutos, o bot retoma o que ficou pela metade (sem duplicar canal, pedido nem anúncio);
  - os anúncios do canal público têm estado próprio (pendente/enviando/enviado/entregue) e também são retomados.
"""
import asyncio
import hashlib
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import ui

import limites
from catalogo import brl
from loja_dados import agora_iso

# =============================================================== configuração
FUSO_BRASILIA = timezone(timedelta(hours=-3))
GACHA_A_CADA_CENTAVOS = 2000                 # 1 gacha a cada R$ 20,00 de compra
TETO_VIP_CENTAVOS = 1500                     # "VIP à sua escolha de até R$ 15,00"

REGISTRO_CANAL_ID = int(os.getenv("LOJA_CANAL_REGISTRO_ID", "1563931955207077889") or 0)
CARGO_ASSINANTE_1_ID = int(os.getenv("LOJA_CARGO_ASSINANTE_1_ID", "1558206515784188115") or 0)
CARGO_ASSINANTE_2_ID = int(os.getenv("LOJA_CARGO_ASSINANTE_2_ID", "1558206661779267665") or 0)

IDADE_MINIMA_RECUPERACAO = 90                # s: só retoma resgates parados há mais que isto (não atropela um em andamento)
INTERVALO_MANUTENCAO = 120                   # s entre as rodadas de retomada
MAX_TENTATIVAS_RESGATE = 10
MAX_ANUNCIOS_POR_RODADA = 5                 # anúncios tentados por rodada: o histórico antigo sai aos poucos, sem inundar o canal
ESPERA_CANAL_REGISTRO = 300                 # s sem procurar de novo o canal de registro depois de uma falha
ESPERA_MAXIMA_ANUNCIO = 3600                # s: teto do recuo (dobra a cada falha) de um anúncio que não sai

# fixos = (chave do item no catálogo, nome de reserva se o item não existir mais, quantidade)
NIVEIS = {
    1: {"cargo_id": CARGO_ASSINANTE_1_ID, "escolhas": 1,
        "fixos": (("escolhidos", "Escolhido dos Mares", 3), ("sorte", "Sorte Grande", 1))},
    2: {"cargo_id": CARGO_ASSINANTE_2_ID, "escolhas": 2,
        "fixos": (("escolhidos", "Escolhido dos Mares", 6), ("sorte", "Sorte Grande", 2))},
}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
MSG_SUPORTE = "Tente de novo em instantes e, se o erro continuar, **acione o suporte da staff** informando o código."


# =============================================================== funções puras (testáveis)
def gachas(total_centavos: int) -> int:
    """Quantos gachas uma compra dá: 1 a cada R$ 20,00 (R$ 40 = 2, R$ 55 = 2)."""
    return max(0, int(total_centavos)) // GACHA_A_CADA_CENTAVOS


def nivel_do_membro(ids_cargos) -> Optional[int]:
    """Nível de assinatura de quem tem estes cargos (o maior, se tiver os dois). None se não for assinante."""
    ids = set(ids_cargos)
    for nivel in sorted(NIVEIS, reverse=True):
        cargo = NIVEIS[nivel]["cargo_id"]
        if cargo and cargo in ids:
            return nivel
    return None


def _em_brasilia(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(FUSO_BRASILIA)


def ciclo_de(dt: datetime) -> str:
    """Mês do benefício ("AAAA-MM") no fuso de Brasília. Vem do horário do Discord, não do relógio da máquina."""
    d = _em_brasilia(dt)
    return f"{d.year}-{d.month:02d}"


def nome_do_ciclo(ciclo: str) -> str:
    ano, mes = ciclo.split("-")
    return f"{MESES[int(mes) - 1]} de {ano}"


def inicio_do_proximo_ciclo(ciclo: str) -> datetime:
    ano, mes = (int(x) for x in ciclo.split("-"))
    ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return datetime(ano, mes, 1, tzinfo=FUSO_BRASILIA)


def chave_beneficio(ciclo: str, usuario_id: int) -> str:
    return f"{ciclo}-{usuario_id}"


def codigo_registro(compra_id: int, criado_em: str, usuario_id: int) -> str:
    """Código curto e estável de uma compra. Vai no rodapé do anúncio: serve para reconhecer o post se o bot cair
    no meio e, como depende da data, não confunde compras de épocas diferentes que repetem o nº (#01)."""
    return hashlib.sha1(f"{compra_id}:{criado_em}:{usuario_id}".encode(), usedforsecurity=False).hexdigest()[:6].upper()


def opcoes_de_escolha(itens: dict) -> list[dict]:
    """Itens que podem ser escolhidos como VIP do benefício (até R$ 15,00), do mais barato ao mais caro."""
    elegiveis = [i for i in itens.values() if 0 < i["preco"] <= TETO_VIP_CENTAVOS and not i.get("fixo")]   # a Indicação (R$ 0) não é VIP
    return sorted(elegiveis, key=lambda i: (i["preco"], i["nome"]))[:25]


def validar_escolhas(nivel: int, escolhas: list, itens: dict) -> tuple[bool, str]:
    """Confere no servidor o que veio do menu (que nunca é confiável)."""
    quantas = NIVEIS[nivel]["escolhas"]
    if len(escolhas) != quantas or any(not isinstance(e, str) for e in escolhas):
        return False, f"Escolha exatamente {quantas} VIP(s)."
    validas = {i["key"] for i in opcoes_de_escolha(itens)}
    for e in escolhas:
        if e not in validas:
            return False, "Um dos itens escolhidos não está mais disponível (ou passa de " + brl(TETO_VIP_CENTAVOS) + ")."
    return True, ""


def itens_do_beneficio(nivel: int, escolhas: list[str], itens: dict) -> list[dict]:
    """Linhas do benefício: os itens fixos (entram sozinhos) e os VIPs escolhidos. Preço 0: é um benefício."""
    linhas = []
    for chave, nome_padrao, qtd in NIVEIS[nivel]["fixos"]:
        linhas.append({"nome": itens.get(chave, {}).get("nome", nome_padrao), "qtd": qtd, "preco_centavos": 0,
                       "origem": "assinatura", "chave_item": chave})
    contagem: dict[str, int] = {}
    for e in escolhas:
        contagem[e] = contagem.get(e, 0) + 1
    for chave, qtd in contagem.items():
        linhas.append({"nome": f"{itens[chave]['nome']} (à escolha)", "qtd": qtd, "preco_centavos": 0,
                       "origem": "escolha", "chave_item": chave})
    return linhas


def linhas_texto(detalhes: list[dict]) -> str:
    saida = []
    for d in detalhes:
        valor = f" — {brl(d['preco_centavos'] * d['qtd'])}" if d.get("preco_centavos") else ""
        saida.append(f"**{d['qtd']}x** {d['nome']}{valor}")
    return "\n".join(saida) or "—"


def _epoch(iso: Optional[str]) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(iso).timestamp()) if iso else None
    except (TypeError, ValueError):
        return None


def embed_registro(pedido: dict, avatar_url: Optional[str] = None) -> discord.Embed:
    """O embed do canal público: tudo que um jogador precisa para conferir uma compra."""
    beneficio = pedido.get("tipo") == "beneficio"
    entregue = pedido.get("status") == "entregue"
    cor = discord.Color.green() if entregue else (discord.Color.purple() if beneficio else discord.Color.gold())
    numero = f"#{int(pedido['id']):02d}"
    gratis = not beneficio and int(pedido.get("total_centavos", 0)) == 0      # só a opção fixa de indicação não tem preço
    titulo = (f"🎁 Benefício mensal {numero} resgatado" if beneficio
              else f"🤝 Indicação {numero} aprovada" if gratis else f"🧾 Compra {numero} aprovada")
    embed = discord.Embed(title=titulo, color=cor)
    uid = pedido.get("usuario_id")
    embed.description = (f"<@{uid}> resgatou os benefícios de **{nome_do_ciclo(pedido['ciclo'])}**!" if beneficio and pedido.get("ciclo")
                         else f"<@{uid}> registrou uma indicação! Obrigado por ajudar o servidor a crescer! 🤡" if gratis
                         else f"<@{uid}> apoiou o servidor! Obrigado! 🤡")

    embed.add_field(name="👤 Comprador" if not beneficio else "👤 Assinante", value=f"<@{uid}>\n{pedido.get('nome') or pedido.get('usuario') or '—'}", inline=True)
    if pedido.get("aprovado_por_id"):
        aprovador = f"<@{pedido['aprovado_por_id']}>"
    else:
        aprovador = pedido.get("aprovado_por_nome") or "—"
    embed.add_field(name="✅ Aprovado por", value=aprovador, inline=True)
    quando = _epoch(pedido.get("aprovado_em") or pedido.get("pago_em"))
    embed.add_field(name="🕒 Aprovado em", value=f"<t:{quando}:F>\n<t:{quando}:R>" if quando else "—", inline=True)
    itens_txt = linhas_texto(pedido.get("itens_detalhe") or [])
    if len(itens_txt) > 1000:       # um campo de embed aceita no máximo 1024 caracteres: corta em linha inteira
        itens_txt = itens_txt[:1000].rsplit("\n", 1)[0] + "\n…e mais itens (veja o painel da staff)"
    embed.add_field(name="🛍️ Itens", value=itens_txt, inline=False)
    if beneficio:
        cargo = f"<@&{pedido['cargo_id']}>" if pedido.get("cargo_id") else "—"
        embed.add_field(name="🏷️ Cargo", value=cargo, inline=True)
        embed.add_field(name="💵 Valor", value="Incluído na assinatura", inline=True)
    elif gratis:
        embed.add_field(name="💵 Total", value="**Grátis**", inline=True)
    else:
        total = int(pedido.get("total_centavos", 0))
        n = gachas(total)
        embed.add_field(name="💵 Total", value=f"**{brl(total)}**", inline=True)
        embed.add_field(
            name="🎰 Gachas",
            value=(f"**{n}** gacha(s)" if n else "Nenhum") + f"\n_1 a cada {brl(GACHA_A_CADA_CENTAVOS)} da compra_",
            inline=True,
        )
    if entregue:
        quando_e = _epoch(pedido.get("entregue_em"))
        por = f"<@{pedido['entregue_por_id']}>" if pedido.get("entregue_por_id") else (pedido.get("entregue_por_nome") or "a staff")
        embed.add_field(name="📦 Entrega", value=f"✅ Entregue por {por}" + (f"\n<t:{quando_e}:F>" if quando_e else ""), inline=False)
    else:
        embed.add_field(name="📦 Entrega", value="⏳ A entregar", inline=False)
    codigo = codigo_registro(pedido["id"], pedido.get("criado_em", ""), uid or 0)
    embed.set_footer(text=f"Compra {numero} · código {codigo}")
    if quando:
        embed.timestamp = datetime.fromtimestamp(quando, tz=timezone.utc)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    return embed


# =============================================================== telas do resgate
class EscolhaSelect(ui.Select):
    """Menu de UM VIP à escolha. O valor que chega é revalidado: nunca se confia no que veio do menu."""

    def __init__(self, indice: int, opcoes: list[dict], atual: Optional[str], quantas: int):
        super().__init__(
            placeholder=f"VIP {indice + 1} de {quantas}: escolha um item de até {brl(TETO_VIP_CENTAVOS)}",
            min_values=1, max_values=1, row=indice,
            options=[discord.SelectOption(label=o["nome"][:100], value=o["key"], description=brl(o["preco"]), default=(o["key"] == atual)) for o in opcoes],
        )
        self.indice = indice

    async def callback(self, interaction: discord.Interaction):
        view: BeneficioView = self.view
        valor = self.values[0]
        if valor in {o["key"] for o in opcoes_de_escolha(view.cog._itens())}:
            view.escolhas[self.indice] = valor
            await view.atualizar(interaction)
        else:
            view.escolhas[self.indice] = None
            await view.atualizar(interaction, aviso="⚠️ Esse item não está mais disponível para escolha. Escolha outro.")


class ConfirmarBeneficioButton(ui.Button):
    def __init__(self, desativado: bool, linha: int):
        super().__init__(label="Resgatar agora", emoji="🎁", style=discord.ButtonStyle.success, disabled=desativado, row=linha)

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.resgatar_beneficio(interaction, self.view)


class CancelarBeneficioButton(ui.Button):
    def __init__(self, linha: int):
        super().__init__(label="Cancelar", style=discord.ButtonStyle.secondary, row=linha)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Resgate cancelado. Você pode abrir de novo quando quiser.", embeds=[], view=None)
        self.view.stop()


class BeneficioView(ui.View):
    """Mensagem privada de quem vai resgatar: mostra os itens fixos e pede só a escolha dos VIPs."""

    def __init__(self, cog, usuario: discord.abc.User, nivel: int, ciclo: str):
        super().__init__(timeout=600)
        self.cog, self.usuario, self.nivel, self.ciclo = cog, usuario, nivel, ciclo
        self.escolhas: list[Optional[str]] = [None] * NIVEIS[nivel]["escolhas"]
        self.montar()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.usuario.id:
            await interaction.response.send_message("⛔ Este resgate é de outra pessoa.", ephemeral=True)
            return False
        return True

    def montar(self):
        self.clear_items()
        opcoes = opcoes_de_escolha(self.cog._itens())
        quantas = len(self.escolhas)
        if opcoes:
            for i, atual in enumerate(self.escolhas):
                self.add_item(EscolhaSelect(i, opcoes, atual, quantas))
        pronto = bool(opcoes) and all(self.escolhas)
        self.add_item(ConfirmarBeneficioButton(not pronto, quantas))
        self.add_item(CancelarBeneficioButton(quantas))

    async def atualizar(self, interaction: discord.Interaction, aviso: Optional[str] = None):
        self.montar()
        await interaction.response.edit_message(content=aviso, embeds=[self.embed()], view=self)

    def embed(self) -> discord.Embed:
        itens = self.cog._itens()
        fixos = itens_do_beneficio(self.nivel, [], itens)
        e = discord.Embed(title=f"🎁 Benefícios de {nome_do_ciclo(self.ciclo)}", color=discord.Color.purple())
        e.description = (f"Assinatura <@&{NIVEIS[self.nivel]['cargo_id']}>. Os itens abaixo entram **automaticamente**; "
                         f"você só precisa escolher {'o seu VIP' if len(self.escolhas) == 1 else 'os seus VIPs'}.")
        e.add_field(name="✅ Já incluídos", value=linhas_texto(fixos), inline=False)
        escolhas = [f"**{i + 1}.** {itens[c]['nome']} — {brl(itens[c]['preco'])}" if c in itens else f"**{i + 1}.** _escolha no menu abaixo_"
                    for i, c in enumerate(self.escolhas)]
        e.add_field(name=f"🎯 Sua escolha (VIP de até {brl(TETO_VIP_CENTAVOS)})", value="\n".join(escolhas), inline=False)
        e.set_footer(text="Só dá para resgatar 1 vez por mês. Confira antes de confirmar.")
        return e


# =============================================================== lógica do cog (misturada em LojaCog)
class BeneficiosMixin:
    """Registro público, gachas e benefícios mensais. Usa do LojaCog: bot, armazenamento, lock, _itens, _overwrites_ticket,
    _categoria, _enviar_ticket e CompraView (via _view_compra)."""

    def _iniciar_beneficios(self):
        self._lock_anuncio = asyncio.Lock()
        self._locks_resgate: dict[int, asyncio.Lock] = {}
        self._em_andamento: set[str] = set()
        self._tarefa_manutencao: Optional[asyncio.Task] = None
        self._canal_registro_falhou_ate = 0.0
        self._falhas_anuncio: dict[str, tuple[int, float]] = {}   # chave -> (nº de falhas seguidas, não tentar antes de)

    # ------------------------------------------------------------ erros
    def _erro_usuario(self, e: Exception, contexto: str) -> str:
        codigo = uuid.uuid4().hex[:6].upper()
        print(f"[Loja] Erro em '{contexto}' (código {codigo}): {type(e).__name__}: {e}")
        print("".join(traceback.format_exception(type(e), e, e.__traceback__))[-1500:])
        return f"❌ Ocorreu um erro inesperado ({contexto}). **Código: {codigo}**. {MSG_SUPORTE}"

    async def _responder_erro(self, interaction: discord.Interaction, e: Exception, contexto: str):
        texto = self._erro_usuario(e, contexto)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(texto, ephemeral=True)
            else:
                await interaction.response.send_message(texto, ephemeral=True)
        except discord.DiscordException:
            pass

    # ------------------------------------------------------------ canal público
    async def _canal_registro(self):
        if not REGISTRO_CANAL_ID:
            return None
        canal = self.bot.get_channel(REGISTRO_CANAL_ID)
        if canal is None:
            if time.monotonic() < self._canal_registro_falhou_ate:
                return None                                           # já falhou há pouco: não repete a requisição por compra
            try:
                canal = await self.bot.fetch_channel(REGISTRO_CANAL_ID)
            except discord.DiscordException as e:
                limites.registrar_se_limite(e)
                self._canal_registro_falhou_ate = time.monotonic() + ESPERA_CANAL_REGISTRO
                return None
        return canal

    async def _achar_anuncio(self, canal, codigo: str):
        """Procura, nas últimas mensagens do canal, um anúncio nosso com este código (evita duplicar após uma queda)."""
        async for msg in canal.history(limit=100):
            if msg.author.id == self.bot.user.id and any(codigo in (e.footer.text or "") for e in msg.embeds if e.footer):
                return msg
        return None

    def _avatar_de(self, canal, usuario_id: int) -> Optional[str]:
        guild = getattr(canal, "guild", None)
        membro = guild.get_member(usuario_id) if guild else None
        return membro.display_avatar.url if membro else None

    async def anunciar_compra(self, chave: str) -> bool:
        """Posta (uma única vez) o registro público de uma compra aprovada. Nunca levanta erro: não pode atrapalhar a compra."""
        async with self._lock_anuncio:
            try:
                pedido = self.armazenamento.dados["pedidos"].get(chave)
                if not pedido or pedido.get("status") not in ("paga", "entregue"):
                    return False
                if pedido.get("anuncio_msg_id"):
                    return True
                canal = await self._canal_registro()
                if canal is None:
                    self.armazenamento.atualizar(chave, anuncio_estado="pendente", anuncio_erro="canal de registro não encontrado")
                    print(f"[Loja] Canal de registro {REGISTRO_CANAL_ID} não encontrado: o anúncio da compra {chave} fica pendente.")
                    return False
                codigo = codigo_registro(pedido["id"], pedido.get("criado_em", ""), pedido.get("usuario_id") or 0)
                if pedido.get("anuncio_estado") == "enviando":      # o bot caiu no meio do envio: confere se o post já saiu
                    existente = await self._achar_anuncio(canal, codigo)
                    if existente:
                        self.armazenamento.atualizar(chave, anuncio_msg_id=existente.id, anuncio_canal_id=canal.id,
                                                     anuncio_estado="entregue" if pedido["status"] == "entregue" else "enviado", anuncio_erro=None)
                        return True
                self.armazenamento.atualizar(chave, anuncio_estado="enviando")
                embed = embed_registro(self.armazenamento.dados["pedidos"][chave], self._avatar_de(canal, pedido.get("usuario_id") or 0))
                msg = await canal.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                self.armazenamento.atualizar(chave, anuncio_msg_id=msg.id, anuncio_canal_id=canal.id, anuncio_erro=None,
                                             anuncio_estado="entregue" if pedido["status"] == "entregue" else "enviado")
                return True
            except Exception as e:
                limites.registrar_se_limite(e)
                print(f"[Loja] Não consegui registrar a compra {chave} no canal público ({type(e).__name__}: {limites.resumir_erro(e)}); tento de novo depois.")
                try:
                    self.armazenamento.atualizar(chave, anuncio_estado="pendente", anuncio_erro=f"{type(e).__name__}: {limites.resumir_erro(e)}"[:200])
                except Exception:
                    pass
                return False

    async def atualizar_anuncio(self, chave: str) -> bool:
        """Edita o registro público quando a compra é entregue. Se ainda não foi anunciada, anuncia (já entregue)."""
        pedido = self.armazenamento.dados["pedidos"].get(chave)
        if not pedido:
            return False
        if not pedido.get("anuncio_msg_id"):
            return await self.anunciar_compra(chave)
        async with self._lock_anuncio:
            try:
                canal = self.bot.get_channel(pedido.get("anuncio_canal_id")) or await self.bot.fetch_channel(pedido["anuncio_canal_id"])
                msg = await canal.fetch_message(pedido["anuncio_msg_id"])
                await msg.edit(embed=embed_registro(pedido, self._avatar_de(canal, pedido.get("usuario_id") or 0)))
                self.armazenamento.atualizar(chave, anuncio_estado="entregue" if pedido["status"] == "entregue" else "enviado")
                return True
            except discord.NotFound:
                print(f"[Loja] O post público da compra {chave} foi apagado; não vou recriá-lo.")
                self.armazenamento.atualizar(chave, anuncio_estado="entregue" if pedido["status"] == "entregue" else "enviado")
                return False
            except Exception as e:
                limites.registrar_se_limite(e)
                print(f"[Loja] Não consegui atualizar o post público da compra {chave} ({type(e).__name__}: {limites.resumir_erro(e)}).")
                return False

    async def retomar_anuncios(self) -> int:
        """Posta o que ficou pendente (bot desligado, canal sem permissão...) e atualiza entregas que não chegaram ao post."""
        feitos = tentados = 0
        for chave, p in list(self.armazenamento.dados["pedidos"].items()):
            if limites.em_pausa():
                break                                                 # o Discord limitou este IP: insistir só prolonga o bloqueio
            if p.get("status") not in ("paga", "entregue"):
                continue
            precisa_postar = not p.get("anuncio_msg_id")
            if not precisa_postar and not (p.get("status") == "entregue" and p.get("anuncio_estado") != "entregue"):
                continue
            if time.monotonic() < self._falhas_anuncio.get(chave, (0, 0.0))[1]:
                continue                                              # falhou há pouco: espera o recuo antes de insistir
            if tentados >= MAX_ANUNCIOS_POR_RODADA:
                break
            tentados += 1
            ok = await (self.anunciar_compra(chave) if precisa_postar else self.atualizar_anuncio(chave))
            if ok:
                self._falhas_anuncio.pop(chave, None)
                feitos += 1
            else:
                n = self._falhas_anuncio.get(chave, (0, 0.0))[0] + 1
                recuo = min(INTERVALO_MANUTENCAO * (2 ** n), ESPERA_MAXIMA_ANUNCIO)
                self._falhas_anuncio[chave] = (n, time.monotonic() + recuo)
        return feitos

    # ------------------------------------------------------------ benefícios mensais
    def _embed_sem_cargo(self) -> discord.Embed:
        e = discord.Embed(title="🎁 Benefícios mensais", color=discord.Color.purple())
        e.description = "Este benefício é para **assinantes** do servidor. Quem tem um destes cargos resgata uma vez por mês:"
        for nivel, cfg in NIVEIS.items():
            fixos = ", ".join(f"{q}x {n}" for _, n, q in cfg["fixos"])
            e.add_field(name=f"Assinatura nível {nivel}", value=f"<@&{cfg['cargo_id']}>\n{fixos} + {cfg['escolhas']} VIP(s) à escolha de até {brl(TETO_VIP_CENTAVOS)}", inline=False)
        return e

    def _embed_ja_resgatado(self, doc: dict, ciclo: str) -> discord.Embed:
        e = discord.Embed(title="🎁 Você já resgatou este mês", color=discord.Color.orange())
        quando = _epoch(doc.get("criado_em"))
        e.description = (f"Os benefícios de **{nome_do_ciclo(ciclo)}** já foram resgatados" + (f" em <t:{quando}:F>" if quando else "") + ".\n"
                         f"O próximo resgate libera em <t:{int(inicio_do_proximo_ciclo(ciclo).timestamp())}:F> "
                         f"(<t:{int(inicio_do_proximo_ciclo(ciclo).timestamp())}:R>).")
        if doc.get("canal_id"):
            e.add_field(name="Acompanhe a entrega", value=f"<#{doc['canal_id']}>", inline=False)
        elif doc.get("estado") != "concluido":
            e.add_field(name="Situação", value="Seu resgate está sendo finalizado. Se o canal não aparecer em alguns minutos, **acione o suporte da staff**.", inline=False)
        return e

    async def abrir_beneficios(self, interaction: discord.Interaction):
        """Botão "Benefícios do mês" do painel."""
        try:
            membro = interaction.user
            if not isinstance(membro, discord.Member):
                await interaction.response.send_message("⚠️ Use este botão dentro do servidor.", ephemeral=True)
                return
            nivel = nivel_do_membro([r.id for r in membro.roles])
            if nivel is None:
                await interaction.response.send_message(embed=self._embed_sem_cargo(), ephemeral=True)
                return
            remoto = self.armazenamento.remoto
            if remoto is None:
                await interaction.response.send_message(
                    "⚠️ Os benefícios mensais dependem do banco de dados online, que não está disponível agora. **Acione o suporte da staff.**", ephemeral=True)
                return
            ciclo = ciclo_de(interaction.created_at)
            await interaction.response.defer(ephemeral=True)
            existente = await asyncio.to_thread(remoto.ler_beneficio, chave_beneficio(ciclo, membro.id))
            if existente:
                await interaction.followup.send(embed=self._embed_ja_resgatado(existente, ciclo), ephemeral=True)
                return
            view = BeneficioView(self, membro, nivel, ciclo)
            if not opcoes_de_escolha(self._itens()):
                await interaction.followup.send("⚠️ Não há VIPs disponíveis para escolha no momento. **Acione o suporte da staff.**", ephemeral=True)
                return
            await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)
        except Exception as e:
            await self._responder_erro(interaction, e, "abrir os benefícios")

    async def resgatar_beneficio(self, interaction: discord.Interaction, view: BeneficioView):
        membro = interaction.user
        trava = self._locks_resgate.setdefault(membro.id, asyncio.Lock())
        if trava.locked():
            await interaction.response.send_message("⏳ Seu resgate já está sendo processado.", ephemeral=True)
            return
        async with trava:
            try:
                await interaction.response.edit_message(content="⏳ Processando o seu resgate...", embeds=[], view=None)
                texto = await self._executar_resgate(interaction, membro, view)
            except Exception as e:
                texto = self._erro_usuario(e, "resgatar os benefícios")
            view.stop()
            try:
                await interaction.edit_original_response(content=texto, embeds=[], view=None)
            except discord.DiscordException:
                pass

    async def _executar_resgate(self, interaction: discord.Interaction, membro: discord.Member, view: BeneficioView) -> str:
        remoto = self.armazenamento.remoto
        if remoto is None:
            return "⚠️ O banco de dados online não está disponível. Seu resgate **não** foi feito. **Acione o suporte da staff.**"
        nivel = nivel_do_membro([r.id for r in membro.roles])      # conferido AGORA, não quando a tela abriu
        if nivel is None:
            return "⛔ Você não tem mais o cargo de assinante, então não dá para resgatar."
        itens = self._itens()
        escolhas = list(view.escolhas)
        valido, erro = validar_escolhas(nivel, escolhas, itens)
        if not valido:
            return f"⚠️ {erro} Abra o painel de benefícios e escolha de novo."
        ciclo = ciclo_de(interaction.created_at)
        chave = chave_beneficio(ciclo, membro.id)
        detalhes = itens_do_beneficio(nivel, escolhas, itens)
        agora = agora_iso()
        doc = {
            "chave": chave, "usuario_id": membro.id, "usuario": str(membro), "nome": membro.display_name,
            "guild_id": interaction.guild.id, "ciclo": ciclo, "nivel": nivel, "cargo_id": NIVEIS[nivel]["cargo_id"],
            "escolhas": escolhas, "itens": detalhes, "estado": "reservado", "compra_id": None, "canal_id": None,
            "tentativas": 0, "criado_em": agora, "atualizado_em": agora,
        }
        # A TRAVA: criar o documento é atômico no servidor. Se já existir, o mês já foi resgatado.
        try:
            reservado = await asyncio.to_thread(remoto.reservar_beneficio, chave, doc)
        except Exception as e:                                       # na dúvida, NÃO libera: falha fechada
            return self._erro_usuario(e, "reservar o benefício") + "\nSeu resgate não foi feito."
        if not reservado:
            existente = await asyncio.to_thread(remoto.ler_beneficio, chave) or doc
            return f"🎁 Você já resgatou os benefícios de **{nome_do_ciclo(ciclo)}**" + (f" (acompanhe em <#{existente['canal_id']}>)." if existente.get("canal_id") else ".")

        self._em_andamento.add(chave)
        try:
            canal, compra_id = await self._concluir_beneficio(chave, doc, interaction.guild, membro)
        except Exception as e:
            nada_feito = not self._pedido_do_beneficio(chave) and self._canal_do_beneficio(interaction.guild, chave) is None
            if nada_feito:                                           # nada foi criado: devolve a vez ao jogador
                try:
                    await asyncio.to_thread(remoto.liberar_beneficio, chave)
                    return self._erro_usuario(e, "resgatar os benefícios") + "\nNada foi cobrado nem consumido: você pode tentar de novo."
                except Exception:
                    pass
            return (self._erro_usuario(e, "resgatar os benefícios")
                    + "\n⚠️ Seu resgate **foi registrado** e o bot vai concluir sozinho. **Não tente de novo**; se o canal não aparecer em alguns minutos, acione o suporte.")
        finally:
            self._em_andamento.discard(chave)
        return f"✅ Benefícios de **{nome_do_ciclo(ciclo)}** resgatados! Acompanhe a entrega em {canal.mention} (compra **#{compra_id:02d}**)."

    def _pedido_do_beneficio(self, chave: str) -> Optional[str]:
        for k, p in self.armazenamento.dados["pedidos"].items():
            if p.get("beneficio_chave") == chave:
                return k
        return None

    @staticmethod
    def _canal_do_beneficio(guild, chave: str):
        """Canal criado por uma tentativa anterior (o bot caiu antes de registrar o pedido). Identificado pelo tópico."""
        for canal in getattr(guild, "text_channels", []):
            if (canal.topic or "").startswith(f"Benefício {chave} "):
                return canal
        return None

    async def _concluir_beneficio(self, chave: str, doc: dict, guild, membro) -> tuple:
        """Leva o resgate até o fim. Cada passo é idempotente: pode ser repetido (retomada) sem duplicar nada."""
        remoto = self.armazenamento.remoto
        async with self.lock:                                       # mesma trava das compras: ids e canais não colidem
            chave_pedido = self._pedido_do_beneficio(chave)
            if chave_pedido is None:
                canal = self._canal_do_beneficio(guild, chave)
                compra_id = self.armazenamento.proximo_id()
                if canal is not None:                               # uma tentativa anterior já criou o canal
                    achado = re.search(r"compra (\d+)", canal.topic or "")
                    if achado and f"{int(achado.group(1)):02d}" not in self.armazenamento.dados["pedidos"]:
                        compra_id = int(achado.group(1))
                else:
                    overwrites, _ = self._overwrites_ticket(guild, membro)
                    canal = await guild.create_text_channel(
                        f"{self._slug(membro.display_name)}-{compra_id:02d}", category=self._categoria(guild), overwrites=overwrites,
                        topic=f"Benefício {chave} · compra {compra_id:02d} — assinante: {membro} ({membro.id})",
                        reason=f"Benefício mensal {doc['ciclo']} de {membro}",
                    )
                agora = agora_iso()
                itens_dict: dict[str, int] = {}
                for d in doc["itens"]:
                    itens_dict[d["chave_item"]] = itens_dict.get(d["chave_item"], 0) + d["qtd"]
                self.armazenamento.registrar(compra_id, {
                    "id": compra_id, "tipo": "beneficio", "beneficio_chave": chave, "ciclo": doc["ciclo"], "nivel": doc["nivel"],
                    "cargo_id": doc["cargo_id"], "usuario_id": membro.id, "usuario": str(membro), "nome": membro.display_name,
                    "canal_id": canal.id, "itens": itens_dict, "itens_detalhe": doc["itens"], "total_centavos": 0,
                    "criado_em": agora, "status": "paga", "pago_em": agora, "aprovado_em": agora, "aprovado_por_id": None,
                    "aprovado_por_nome": f"Automático (assinatura nível {doc['nivel']})", "entregue_em": None, "fechado_em": None,
                    "atualizado_por": "automático (assinatura)", "ticket_enviado": False,
                })
                chave_pedido = f"{compra_id:02d}"
            else:
                compra_id = int(chave_pedido)
                canal = guild.get_channel(self.armazenamento.dados["pedidos"][chave_pedido]["canal_id"])
                if canal is None:
                    raise RuntimeError(f"o canal do benefício {chave} não existe mais")
        await asyncio.to_thread(remoto.atualizar_beneficio, chave,
                                {"estado": "ticket", "compra_id": compra_id, "canal_id": canal.id, "atualizado_em": agora_iso()})
        pedido = self.armazenamento.dados["pedidos"][chave_pedido]
        if not pedido.get("ticket_enviado"):
            await self._enviar_ticket_beneficio(canal, doc, pedido, guild, membro)
            self.armazenamento.atualizar(chave_pedido, ticket_enviado=True)
        await self.anunciar_compra(chave_pedido)
        await asyncio.to_thread(remoto.atualizar_beneficio, chave, {"estado": "concluido", "atualizado_em": agora_iso()})
        return canal, compra_id

    async def _enviar_ticket_beneficio(self, canal, doc: dict, pedido: dict, guild, membro):
        embed = discord.Embed(title=f"🎁 Benefício mensal #{pedido['id']:02d} — {nome_do_ciclo(doc['ciclo'])}", color=discord.Color.purple())
        embed.description = linhas_texto(doc["itens"])
        embed.add_field(name="Assinante", value=membro.mention, inline=True)
        embed.add_field(name="Cargo", value=f"<@&{doc['cargo_id']}>", inline=True)
        embed.add_field(name="Aprovação", value="✅ Automática (assinatura)", inline=True)
        embed.add_field(name="Status", value="💰 Paga — a entregar", inline=False)
        embed.set_footer(text="Resgate mensal: a staff entrega os itens aqui neste canal e marca como entregue.")
        _, cargos = self._overwrites_ticket(guild, membro)
        mencoes = " ".join([membro.mention] + [r.mention for r in cargos])
        texto = f"**Benefício mensal #{pedido['id']:02d}** — {nome_do_ciclo(doc['ciclo'])}\n{linhas_texto(doc['itens'])}"
        await self._enviar_ticket(canal, f"{mencoes}\nBenefício mensal resgatado! A staff vai entregar por aqui. 🎁", [embed], [], None, texto)

    async def retomar_beneficios(self) -> int:
        """Termina resgates que ficaram pela metade (bot desligado, erro do Discord...). Seguro para repetir."""
        remoto = self.armazenamento.remoto
        if remoto is None:
            return 0
        feitos = 0
        agora = datetime.now(timezone.utc)
        for chave, doc in await asyncio.to_thread(remoto.listar_beneficios):
            if limites.em_pausa():
                break
            try:
                if doc.get("estado") in ("concluido", "membro_ausente", "falhou") or chave in self._em_andamento:
                    continue
                parado = (agora - datetime.fromisoformat(doc["atualizado_em"])).total_seconds()
                if parado < IDADE_MINIMA_RECUPERACAO:
                    continue                                          # provavelmente ainda em andamento
                guild = self.bot.get_guild(doc["guild_id"])
                if guild is None:
                    continue
                try:
                    membro = guild.get_member(doc["usuario_id"]) or await guild.fetch_member(doc["usuario_id"])
                except discord.NotFound:
                    await asyncio.to_thread(remoto.atualizar_beneficio, chave, {"estado": "membro_ausente", "atualizado_em": agora_iso()})
                    continue
                self._em_andamento.add(chave)
                try:
                    await self._concluir_beneficio(chave, doc, guild, membro)
                    feitos += 1
                    print(f"[Loja] Retomei o benefício {chave}, que tinha ficado pela metade.")
                except Exception as e:
                    if limites.registrar_se_limite(e):
                        break                                         # 429 não é culpa do resgate: não gasta tentativa
                    tentativas = int(doc.get("tentativas", 0)) + 1
                    estado = "falhou" if tentativas >= MAX_TENTATIVAS_RESGATE else doc.get("estado", "reservado")
                    print(f"[Loja] Falha ao retomar o benefício {chave} (tentativa {tentativas}): {type(e).__name__}: {limites.resumir_erro(e)}")
                    await asyncio.to_thread(remoto.atualizar_beneficio, chave, {"tentativas": tentativas, "estado": estado, "atualizado_em": agora_iso()})
                finally:
                    self._em_andamento.discard(chave)
            except Exception as e:
                if limites.registrar_se_limite(e):
                    break
                print(f"[Loja] Erro ao examinar o benefício {chave}: {type(e).__name__}: {limites.resumir_erro(e)}")
        return feitos

    # ------------------------------------------------------------ rotina de retomada
    async def manutencao(self) -> tuple:
        """Uma rodada de retomada. Não levanta erro."""
        resultado = [0, 0]
        for i, funcao in enumerate((self.retomar_beneficios, self.retomar_anuncios)):
            if limites.em_pausa():
                break
            try:
                resultado[i] = await funcao()
            except Exception as e:
                limites.registrar_se_limite(e)
                print(f"[Loja] Erro na rotina de retomada ({funcao.__name__}): {type(e).__name__}: {limites.resumir_erro(e)}")
        return tuple(resultado)

    async def _loop_manutencao(self):
        await asyncio.sleep(5)
        while True:
            if limites.em_pausa():
                espera = limites.segundos_de_pausa() + 1
                print(f"[Loja] Discord limitou as requisições (429): retomada em pausa por {espera}s.")
                await asyncio.sleep(espera)
                continue
            await self.manutencao()
            await asyncio.sleep(INTERVALO_MANUTENCAO)

    def _iniciar_manutencao(self):
        if self._tarefa_manutencao is None or self._tarefa_manutencao.done():
            self._tarefa_manutencao = asyncio.create_task(self._loop_manutencao())

    def _parar_manutencao(self):
        if self._tarefa_manutencao is not None:
            self._tarefa_manutencao.cancel()
            self._tarefa_manutencao = None
