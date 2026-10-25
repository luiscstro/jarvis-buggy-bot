"""Registro público das compras (canal que os jogadores veem) e gachas."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import beneficios as ben
import loja
from conftest import CanalRegistro, comprar, guild_falso, interacao_staff_no_canal, ligar_registro, membro, staff


def run(coro):
    return asyncio.run(coro)


def nova_compra(cog, user=None, carrinho=None, g=None):
    """Cria uma compra e devolve (pedido_chave, canal, guild)."""
    g = g or guild_falso()
    user = user or membro(111, "Percy Jackson", "percy#0")
    run(comprar(cog, user, carrinho or {"escolhidos": 2, "renascimento": 1}, g))
    chave = f"{len(cog.armazenamento.dados['pedidos']):02d}"
    return chave, g.canais[-1], g


def aprovar(cog, canal, quem=None):
    i = interacao_staff_no_canal(canal.id, quem or staff(11, "Ana"))
    run(cog.mudar_status(i, "paga"))
    return i


def entregar(cog, canal, quem=None):
    i = interacao_staff_no_canal(canal.id, quem or staff(12, "Beto"))
    run(cog.mudar_status(i, "entregue"))
    return i


def campos(embed):
    return {f.name: f.value for f in embed.fields}


# ---------------------------------------------------------------- o post público
def test_aprovar_a_compra_posta_o_registro_com_todos_os_dados(cog, registro):
    chave, canal, _ = nova_compra(cog)
    assert registro.mensagens == []                                   # só depois da aprovação
    aprovar(cog, canal)
    assert len(registro.mensagens) == 1
    e = registro.mensagens[0].embeds[0]
    c = campos(e)
    assert e.title == "🧾 Compra #01 aprovada"
    assert "<@111>" in c["👤 Comprador"] and "Percy Jackson" in c["👤 Comprador"]
    assert c["✅ Aprovado por"] == "<@11>"
    assert c["🕒 Aprovado em"].startswith("<t:") and ":F>" in c["🕒 Aprovado em"] and ":R>" in c["🕒 Aprovado em"]
    assert "**2x** Escolhidos dos Mares — R$ 10,00" in c["🛍️ Itens"] and "**1x** Renascimento — R$ 15,00" in c["🛍️ Itens"]
    assert c["💵 Total"] == "**R$ 25,00**"
    assert "**1** gacha(s)" in c["🎰 Gachas"] and "R$ 20,00" in c["🎰 Gachas"]
    assert c["📦 Entrega"] == "⏳ A entregar"
    assert e.footer.text.startswith("Compra #01 · código ") and e.timestamp is not None


def test_registro_nao_vaza_dados_da_staff_nem_o_pix(cog, registro):
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    e = registro.mensagens[0].embeds[0]
    texto = (e.title or "") + (e.description or "") + " ".join(f"{f.name} {f.value}" for f in e.fields) + (e.footer.text or "")
    for proibido in ("copia e cola", "BR.GOV.BCB", "000201", "firebase", "@gmail", "pix.png"):
        assert proibido.lower() not in texto.lower()
    assert registro.ultimos_kwargs["allowed_mentions"].users is False and registro.ultimos_kwargs["allowed_mentions"].roles is False


def test_entregar_edita_o_mesmo_post_em_vez_de_criar_outro(cog, registro):
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    entregar(cog, canal, staff(12, "Beto"))
    assert len(registro.mensagens) == 1
    e = registro.mensagens[0].embeds[0]
    assert "✅ Entregue por <@12>" in campos(e)["📦 Entrega"] and e.color == discord.Color.green()
    assert cog.armazenamento.dados["pedidos"]["01"]["anuncio_estado"] == "entregue"


def test_aprovar_duas_vezes_nao_duplica_o_post(cog, registro):
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal); aprovar(cog, canal)
    assert len(registro.mensagens) == 1


def test_varios_pedidos_de_anuncio_ao_mesmo_tempo_geram_um_post_so(cog, registro):
    _, canal, _ = nova_compra(cog)
    cog.armazenamento.atualizar("01", status="paga", aprovado_em=ben.agora_iso(), aprovado_por_id=11)
    async def varios():
        await asyncio.gather(*[cog.anunciar_compra("01") for _ in range(25)])
    run(varios())
    assert len(registro.mensagens) == 1


@pytest.mark.parametrize("total,esperado", [(0, 0), (1, 0), (1999, 0), (2000, 1), (3999, 1), (4000, 2), (5500, 2), (6000, 3), (10000, 5), (123456, 61)])
def test_gachas_a_cada_20_reais(total, esperado):
    assert ben.gachas(total) == esperado


def test_gachas_nunca_negativos():
    assert ben.gachas(-500) == 0


@pytest.mark.parametrize("carrinho,gachas", [({"escolhidos": 3}, 0), ({"renascimento": 1, "mudanca": 1}, 1), ({"renascimento": 2, "mudanca": 2}, 2), ({"supertesouro": 1}, 1)])
def test_post_publico_mostra_os_gachas_certos(cog, registro, carrinho, gachas):
    _, canal, _ = nova_compra(cog, carrinho=carrinho)
    aprovar(cog, canal)
    valor = campos(registro.mensagens[0].embeds[0])["🎰 Gachas"]
    assert (f"**{gachas}** gacha(s)" in valor) if gachas else valor.startswith("Nenhum")


def test_aviso_no_ticket_cita_os_gachas_so_quando_ha(cog, registro):
    _, canal, g = nova_compra(cog, carrinho={"renascimento": 2, "mudanca": 2})        # R$ 40 = 2 gachas
    i = aprovar(cog, canal)
    assert "2 gacha(s)" in i.channel.send.call_args.args[0]
    _, canal2, _ = nova_compra(cog, user=membro(222, "Outro"), carrinho={"aviso": 1}, g=g)
    assert "gacha" not in aprovar(cog, canal2).channel.send.call_args.args[0]


# ---------------------------------------------------------------- falhas não atrapalham a compra e são retomadas
def test_canal_de_registro_inexistente_nao_impede_a_aprovacao_e_fica_pendente(cog):
    cog.bot.get_channel = lambda id_: None
    cog.bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "x"))
    _, canal, _ = nova_compra(cog)
    i = aprovar(cog, canal)
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["status"] == "paga" and ped["anuncio_estado"] == "pendente" and "não encontrado" in ped["anuncio_erro"]
    i.response.edit_message.assert_awaited_once()                      # o staff recebeu a confirmação normalmente


def test_sem_permissao_no_canal_fica_pendente_e_e_postado_depois(cog):
    canal_reg = ligar_registro(cog)
    canal_reg.falhar = discord.Forbidden(MagicMock(status=403, reason="x"), "Missing Permissions")
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    assert cog.armazenamento.dados["pedidos"]["01"]["anuncio_estado"] == "pendente" and canal_reg.mensagens == []
    canal_reg.falhar = None                                            # o admin deu a permissão
    assert run(cog.retomar_anuncios()) == 1
    assert len(canal_reg.mensagens) == 1 and cog.armazenamento.dados["pedidos"]["01"]["anuncio_estado"] == "enviado"
    assert run(cog.retomar_anuncios()) == 0 and len(canal_reg.mensagens) == 1      # idempotente


def test_erro_qualquer_ao_postar_nunca_quebra_a_aprovacao(cog):
    canal_reg = ligar_registro(cog)
    canal_reg.send = AsyncMock(side_effect=RuntimeError("explodiu"))
    _, canal, _ = nova_compra(cog)
    i = aprovar(cog, canal)
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "paga" and "explodiu" in cog.armazenamento.dados["pedidos"]["01"]["anuncio_erro"]
    i.channel.send.assert_awaited()


def test_bot_desligado_na_aprovacao_posta_ao_voltar(cog, registro):
    """Compra aprovada e gravada, mas o bot caiu antes de postar: a rotina de retomada posta quando ele volta."""
    _, canal, _ = nova_compra(cog)
    cog.armazenamento.atualizar("01", status="paga", pago_em=ben.agora_iso(), aprovado_em=ben.agora_iso(), aprovado_por_id=11)
    assert registro.mensagens == []
    assert run(cog.manutencao()) == (0, 1) and len(registro.mensagens) == 1


def test_queda_no_meio_do_envio_nao_duplica_o_post(cog, registro):
    """O estado 'enviando' foi gravado e o post até saiu, mas o bot caiu antes de guardar o id da mensagem."""
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    ped = cog.armazenamento.dados["pedidos"]["01"]
    cog.armazenamento.atualizar("01", anuncio_msg_id=None, anuncio_canal_id=None, anuncio_estado="enviando")   # como se o id nunca tivesse sido salvo
    assert len(registro.mensagens) == 1
    assert run(cog.anunciar_compra("01")) is True
    assert len(registro.mensagens) == 1 and cog.armazenamento.dados["pedidos"]["01"]["anuncio_msg_id"] == registro.mensagens[0].id


def test_post_antigo_de_outra_epoca_com_o_mesmo_numero_nao_confunde_o_dedupe(cog, registro):
    """Depois de zerar o banco os números recomeçam em #01: um post velho com o mesmo número não pode contar como o novo."""
    antigo = ben.embed_registro({"id": 1, "usuario_id": 111, "criado_em": "2025-01-01T00:00:00+00:00", "status": "paga", "itens_detalhe": [], "total_centavos": 0, "pago_em": "2025-01-01T00:00:00+00:00"})
    registro.mensagens.append(types.SimpleNamespace(id=1, embeds=[antigo], author=types.SimpleNamespace(id=999)))
    _, canal, _ = nova_compra(cog)
    cog.armazenamento.atualizar("01", status="paga", anuncio_estado="enviando", pago_em=ben.agora_iso())
    run(cog.anunciar_compra("01"))
    assert len(registro.mensagens) == 2 and cog.armazenamento.dados["pedidos"]["01"]["anuncio_msg_id"] == registro.mensagens[-1].id


def test_post_apagado_pela_staff_nao_e_recriado_nem_quebra_a_entrega(cog, registro):
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    registro.mensagens.clear()                                         # alguém apagou o post
    i = entregar(cog, canal)
    assert registro.mensagens == [] and cog.armazenamento.dados["pedidos"]["01"]["status"] == "entregue"
    i.response.edit_message.assert_awaited_once()


def test_entregar_compra_que_nunca_foi_anunciada_anuncia_ja_entregue(cog, registro):
    _, canal, _ = nova_compra(cog)
    cog.armazenamento.atualizar("01", status="paga", pago_em=ben.agora_iso())      # aprovada por uma versão antiga, sem anúncio
    entregar(cog, canal)
    assert len(registro.mensagens) == 1 and "Entregue por" in campos(registro.mensagens[0].embeds[0])["📦 Entrega"]


def test_atualizacao_de_entrega_que_falhou_e_refeita_na_retomada(cog, registro):
    _, canal, _ = nova_compra(cog)
    aprovar(cog, canal)
    registro.mensagens[0].falha_edicao = discord.HTTPException(MagicMock(status=500, reason="x"), "instável")
    entregar(cog, canal)
    assert cog.armazenamento.dados["pedidos"]["01"]["anuncio_estado"] == "enviado"          # a edição falhou: não marcou como entregue
    registro.mensagens[0].falha_edicao = None
    assert run(cog.retomar_anuncios()) == 1
    assert "Entregue por" in campos(registro.mensagens[0].embeds[0])["📦 Entrega"]


def test_so_compras_aprovadas_sao_anunciadas(cog, registro):
    g = guild_falso()
    for n in range(3):
        run(comprar(cog, membro(300 + n, f"J{n}"), {"aviso": 1}, g))
    cog.armazenamento.atualizar("02", status="cancelada")
    assert run(cog.retomar_anuncios()) == 0 and registro.mensagens == []
    assert run(cog.anunciar_compra("01")) is False and run(cog.anunciar_compra("99")) is False


def test_pedido_antigo_sem_os_campos_novos_tambem_e_anunciado(cog, registro):
    cog.armazenamento.dados["pedidos"]["01"] = {
        "id": 1, "usuario_id": 5, "usuario": "velho#0", "nome": "Velho", "canal_id": 1, "itens": {"aviso": 1},
        "total_centavos": 500, "criado_em": "2026-10-01T10:00:00+00:00", "status": "paga", "pago_em": "2026-10-01T10:05:00+00:00",
    }
    assert run(cog.anunciar_compra("01")) is True
    c = campos(registro.mensagens[0].embeds[0])
    assert c["✅ Aprovado por"] == "—" and c["🛍️ Itens"] == "—"            # sem inventar dados que não existem


def test_embed_cabe_nos_limites_do_discord_mesmo_com_muitos_itens(cog):
    itens = [{"nome": f"Item de nome bem comprido número {n}", "qtd": 9999, "preco_centavos": 1500} for n in range(25)]
    ped = {"id": 7, "usuario_id": 1, "nome": "N" * 60, "status": "entregue", "itens_detalhe": itens, "total_centavos": 123456789,
           "criado_em": "2026-10-01T00:00:00+00:00", "pago_em": "2026-10-01T00:00:00+00:00", "entregue_em": "2026-10-02T00:00:00+00:00", "entregue_por_id": 3}
    e = ben.embed_registro(ped)
    assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in e.fields) and len(e) <= 6000
    assert "e mais itens" in campos(e)["🛍️ Itens"]


def test_diagnostico_mostra_o_canal_de_registro_e_pendencias(cog):
    canal = ligar_registro(cog)
    canal.mention = "<#123>"
    guild = MagicMock(); guild.get_role.return_value = MagicMock()
    canal.permissions_for = lambda me: types.SimpleNamespace(send_messages=True, embed_links=False, view_channel=True, read_message_history=True)
    texto = "\n".join(cog._diagnostico_registro(guild))
    assert "não pode postar" in texto and "Inserir Links" in texto
    cog.bot.get_channel = lambda id_: None
    assert "não encontrado" in "\n".join(cog._diagnostico_registro(guild))
