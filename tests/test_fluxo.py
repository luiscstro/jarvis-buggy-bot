"""Fluxo completo: compra, canal privado, PIX, botões da staff, limites e comandos."""
import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import loja
import pix
from conftest import (PIX_TESTE, comprar, drenar, guild_falso, interacao, interacao_staff_no_canal, membro,
                      pedido_modelo, staff)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- criar compra
def test_compra_cria_canal_privado_com_pix_e_registra(cog, bd):
    g = guild_falso()
    user = membro(111, "Percy Jackson", "percy#0")
    inter, canal = run(comprar(cog, user, {"escolhidos": 2, "renascimento": 1}, g))
    drenar(cog.armazenamento)

    nome = g.create_text_channel.call_args.args[0]
    assert nome == "percy-jackson-01"
    kw = canal.send.call_args.kwargs
    resumo, pagamento = kw["embeds"]
    assert [f.name for f in resumo.fields] == ["Total a pagar", "Comprador", "Status"]
    assert "R$ 25,00" in resumo.fields[0].value
    assert kw["files"][0].filename == "pix.png" and isinstance(kw["view"], loja.CompraView)

    codigo = pagamento.fields[1].value.strip("`")
    assert pix.payload_valido(codigo) and dict(pix._ler_campos(codigo))["54"] == "25.00"
    assert pagamento.fields[0].value == "Loja de Teste"

    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["usuario_id"] == 111 and ped["status"] == "aguardando_pagamento" and ped["total_centavos"] == 2500
    assert ped["itens_detalhe"] == [{"nome": "Escolhidos dos Mares", "qtd": 2, "preco_centavos": 500},
                                    {"nome": "Renascimento", "qtd": 1, "preco_centavos": 1500}]
    assert ped["canal_id"] == canal.id and bd.cols["loja_pedidos"]["01"]["total_centavos"] == 2500
    assert "Compra **01** criada" in inter.edit_original_response.call_args.kwargs["content"]


def test_sem_pix_configurado_o_ticket_abre_sem_pagamento(cog, monkeypatch):
    monkeypatch.setattr(loja, "PIX_COPIA_COLA", "")
    _, canal = run(comprar(cog, membro(), {"aviso": 1}))
    kw = canal.send.call_args.kwargs
    assert len(kw["embeds"]) == 1 and kw["files"] == []


def test_permissoes_do_canal_so_comprador_bot_e_staff(cog):
    g = guild_falso()
    cargo = MagicMock(); cargo.name = "Moderação"; cargo.id = 77; cargo.mention = "<@&77>"
    g.roles = [cargo]
    user = membro(5)
    run(comprar(cog, user, guild=g))
    ow = g.create_text_channel.call_args.kwargs["overwrites"]
    assert ow[g.default_role].view_channel is False
    assert ow[user].view_channel is True and ow[user].send_messages is True
    assert ow[g.me].manage_channels is True
    assert ow[cargo].view_channel is True and ow[cargo].manage_messages is True
    assert set(ow) == {g.default_role, user, g.me, cargo}                 # ninguém mais


def test_nick_malicioso_nao_vira_mencao_nem_nome_invalido(cog):
    g = guild_falso()
    user = membro(9, "@everyone <@&1> #geral", "x#0")
    _, canal = run(comprar(cog, user, guild=g))
    assert re.fullmatch(r"[a-z0-9-]+-\d{2,}", g.create_text_channel.call_args.args[0])
    conteudo = canal.send.call_args.kwargs["content"]
    assert "@everyone" not in conteudo and "@here" not in conteudo and "<@&1>" not in conteudo


def test_carrinho_vazio_e_recusado(cog):
    g = guild_falso()
    view = loja.LojaView(cog, membro()); inter = interacao(membro(), g)
    run(cog.criar_compra(inter, view))
    inter.response.send_message.assert_awaited_once()
    assert g.create_text_channel.await_count == 0 and cog.armazenamento.dados["pedidos"] == {}


def test_falha_ao_criar_canal_nao_registra_nem_gasta_o_id(cog):
    g = guild_falso()
    g.create_text_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403, reason="x"), "sem permissão"))
    inter, canal = run(comprar(cog, membro(), guild=g))
    assert canal is None and cog.armazenamento.dados["pedidos"] == {} and cog.armazenamento.proximo_id() == 1
    assert "Gerenciar Canais" in inter.followup.send.call_args.args[0]
    # a pessoa pode tentar de novo
    _, canal2 = run(comprar(cog, membro(), guild=guild_falso()))
    assert canal2 is not None and "01" in cog.armazenamento.dados["pedidos"]


def test_clique_duplo_em_finalizar_cria_uma_compra_so(cog):
    async def cenario():
        g = guild_falso(sleep=True)
        user = membro(3)
        view = loja.LojaView(cog, user); view.carrinho = {"escolhidos": 1}; view.montar()
        i1, i2 = interacao(user, g, sleep=True), interacao(user, g, sleep=True)
        await asyncio.gather(cog.criar_compra(i1, view), cog.criar_compra(i2, view))
        return g
    g = run(cenario())
    assert len(cog.armazenamento.dados["pedidos"]) == 1 and len(g.canais) == 1


# ---------------------------------------------------------------- limite de compras abertas por pessoa
def test_limite_de_compras_abertas_por_pessoa(cog):
    user = membro(50)
    g = guild_falso()
    for _ in range(3):
        _, c = run(comprar(cog, user, guild=g))
        assert c is not None
    inter, bloqueado = run(comprar(cog, user, guild=g))
    assert bloqueado is None and "limite é 3" in inter.followup.send.call_args.args[0]
    assert len(cog.armazenamento.dados["pedidos"]) == 3
    # outra pessoa não é afetada
    assert run(comprar(cog, membro(51), guild=g))[1] is not None


def test_pagar_ou_fechar_libera_vaga_no_limite(cog):
    user = membro(50); g = guild_falso()
    for _ in range(3):
        run(comprar(cog, user, guild=g))
    run(cog.mudar_status(interacao_staff_no_canal(g.canais[0].id), "paga"))
    assert run(comprar(cog, user, guild=g))[1] is not None            # paga: não conta mais como aberta
    assert run(comprar(cog, user, guild=g))[1] is None                # de novo no limite
    run(cog.fechar_compra(interacao_staff_no_canal(g.canais[1].id)))  # fechada/cancelada: libera
    assert run(comprar(cog, user, guild=g))[1] is not None


def test_limite_zero_desativa(cog, monkeypatch):
    monkeypatch.setattr(loja, "LOJA_MAX_ABERTAS", 0)
    user = membro(60); g = guild_falso()
    for _ in range(8):
        assert run(comprar(cog, user, guild=g))[1] is not None


# ---------------------------------------------------------------- botões da staff: matriz de status
def _com_status(cog, status):
    g = guild_falso()
    _, canal = run(comprar(cog, membro(70), guild=g))
    if status != "aguardando_pagamento":
        cog.armazenamento.atualizar("01", status=status)
    return canal


@pytest.mark.parametrize("inicial,acao,final", [
    ("aguardando_pagamento", "paga", "paga"),
    ("aguardando_pagamento", "entregue", "aguardando_pagamento"),   # não entrega sem pagar
    ("paga", "paga", "paga"),
    ("paga", "entregue", "entregue"),
    ("entregue", "paga", "entregue"),
    ("entregue", "entregue", "entregue"),
    ("cancelada", "paga", "cancelada"),
    ("cancelada", "entregue", "cancelada"),
])
def test_matriz_de_transicoes(cog, inicial, acao, final):
    canal = _com_status(cog, inicial)
    i = interacao_staff_no_canal(canal.id)
    run(cog.mudar_status(i, acao))
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == final
    mudou = final != inicial
    assert (i.response.edit_message.await_count == 1) is mudou        # só edita a mensagem se mudou
    assert (i.response.send_message.await_count == 1) is (not mudou)  # senão avisa a staff


def test_pagar_registra_quem_e_quando_e_remove_o_qr(cog):
    canal = _com_status(cog, "aguardando_pagamento")
    i = interacao_staff_no_canal(canal.id, staff(11, "Ana"))
    run(cog.mudar_status(i, "paga"))
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["pago_em"] and "(11)" in ped["atualizado_por"]
    kw = i.response.edit_message.call_args.kwargs
    assert kw["attachments"] == [] and len(kw["embeds"]) == 1
    assert [f.value for f in kw["embeds"][0].fields if f.name == "Status"] == [loja.STATUS["paga"]]


def test_entregar_registra_a_data(cog):
    canal = _com_status(cog, "paga")
    run(cog.mudar_status(interacao_staff_no_canal(canal.id), "entregue"))
    assert cog.armazenamento.dados["pedidos"]["01"]["entregue_em"]


def test_fechar_sem_pagar_cancela_e_fechar_pago_preserva(cog):
    g = guild_falso()
    run(comprar(cog, membro(80), guild=g)); run(comprar(cog, membro(81), guild=g))
    cog.armazenamento.atualizar("02", status="paga")
    i1, i2 = interacao_staff_no_canal(g.canais[0].id), interacao_staff_no_canal(g.canais[1].id)
    run(cog.fechar_compra(i1)); run(cog.fechar_compra(i2))
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "cancelada"
    assert cog.armazenamento.dados["pedidos"]["02"]["status"] == "paga"
    assert all(p["fechado_em"] for p in cog.armazenamento.dados["pedidos"].values())
    i1.channel.delete.assert_awaited_once()


def test_canal_sem_registro_nao_quebra(cog):
    i = interacao_staff_no_canal(424242)
    run(cog.mudar_status(i, "paga")); run(cog.fechar_compra(i))
    assert i.response.send_message.await_count == 2 and i.channel.delete.await_count == 0


def test_botoes_funcionam_em_pedido_de_versao_antiga(cog):
    cog.armazenamento.dados["pedidos"]["01"] = {"usuario_id": 1, "usuario": "u#0", "canal_id": 321, "itens": {}, "total_centavos": 500}
    ld_completar = __import__("loja_dados").completar_pedido
    ld_completar(cog.armazenamento.dados["pedidos"]["01"], "01")
    run(cog.mudar_status(interacao_staff_no_canal(321), "paga"))
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "paga"


# ---------------------------------------------------------------- comandos
def _ctx(autor):
    c = MagicMock(); c.author = autor; c.send = AsyncMock(); c.message.delete = AsyncMock()
    return c


def test_loja_painel_posta_o_botao_e_apaga_o_comando(cog):
    ctx = _ctx(staff()); destino = MagicMock(); destino.send = AsyncMock()
    run(cog.loja_painel.callback(cog, ctx, destino))
    destino.send.assert_awaited_once()
    assert isinstance(destino.send.call_args.kwargs["view"], loja.PainelLojaView)
    ctx.message.delete.assert_awaited_once()


def test_loja_acesso_adicionar_listar_remover(cog, bd):
    async def cmd(autor, *args):
        c = _ctx(autor); await cog.loja_acesso.callback(cog, c, *args); return c
    c = run(cmd(staff(), "adicionar", "  Fulano@Gmail.COM "))
    assert "fulano@gmail.com" in bd.cols["loja_staff"] and c.message.delete.await_count == 1
    assert bd.cols["loja_staff"]["fulano@gmail.com"]["adicionado_por"] == "staff#0 (1)"
    run(cmd(staff(), "adicionar", "ciclano@gmail.com"))
    assert "fulano@gmail.com" in run(cmd(staff(), "listar")).send.call_args.args[0]
    assert "removido" in run(cmd(staff(), "remover", "fulano@gmail.com")).send.call_args.args[0]
    assert list(bd.cols["loja_staff"]) == ["ciclano@gmail.com"]
    assert "não tinha acesso" in run(cmd(staff(), "remover", "ninguem@x.com")).send.call_args.args[0]


def test_loja_acesso_sem_firebase(cog_local):
    c = _ctx(staff()); run(cog_local.loja_acesso.callback(cog_local, c, "listar"))
    assert "Firebase" in c.send.call_args.args[0]


def test_loja_armazenamento_descreve_o_estado(cog, cog_local, bd):
    c = _ctx(staff()); run(cog.loja_armazenamento.callback(cog, c))
    assert "Firebase conectado" in c.send.call_args.args[0]
    c = _ctx(staff()); run(cog_local.loja_armazenamento.callback(cog_local, c))
    assert "Só arquivo local" in c.send.call_args.args[0]


def test_painel_antigo_do_discord_e_apagado_uma_vez(cog):
    msg = MagicMock(); msg.delete = AsyncMock()
    canal = MagicMock(); canal.fetch_message = AsyncMock(return_value=msg)
    cog.bot.get_channel.return_value = canal
    cog.armazenamento.definir_dashboard(1, 2)
    run(cog.on_ready()); run(cog.on_ready())
    msg.delete.assert_awaited_once()
    assert cog.armazenamento.dados["dashboard"] is None
    canal.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "sumiu"))
    cog.armazenamento.definir_dashboard(1, 3)
    run(cog.on_ready())                       # já apagada na mão: não quebra
    assert cog.armazenamento.dados["dashboard"] is None


def test_painel_do_discord_foi_removido():
    assert not hasattr(loja, "DashboardView") and not hasattr(loja, "embed_dashboard")
    assert sorted(c.name for c in loja.LojaCog.__cog_commands__) == ["loja_acesso", "loja_armazenamento", "loja_painel"]
