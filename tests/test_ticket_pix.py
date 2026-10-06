"""Ticket com PIX: plano B quando o Discord recusa o envio, aviso sem PIX e diagnóstico para a staff."""
import asyncio
import os
import subprocess
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import loja
from conftest import PIX_TESTE, RAIZ, comprar, guild_falso, membro, staff


def run(coro):
    return asyncio.run(coro)


def proibido():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


def guild_com_envio(comportamento):
    """Guild falso em que o canal criado se comporta como `comportamento(**kwargs)` ao enviar mensagens."""
    g = guild_falso()
    original = g.create_text_channel.side_effect

    async def criar(nome, **kw):
        canal = await original(nome, **kw)
        canal.send = AsyncMock(side_effect=comportamento)
        return canal

    g.create_text_channel = AsyncMock(side_effect=criar)
    return g


# ---------------------------------------------------------------- plano B do envio
def test_caminho_normal_envia_uma_vez_com_qr_e_chave(cog):
    g = guild_com_envio(lambda **kw: None)
    inter, canal = run(comprar(cog, membro(1), {"escolhidos": 2}, g))
    assert canal.send.await_count == 1
    kw = canal.send.call_args.kwargs
    codigo = kw["embeds"][1].fields[1].value
    assert PIX_TESTE[:20] in codigo and kw["files"][0].filename == "pix.png"
    assert "Avise a staff" not in inter.edit_original_response.call_args.kwargs["content"]


def test_sem_permissao_de_anexar_manda_sem_qr_mas_com_a_chave(cog):
    def sem_anexo(**kw):
        if kw.get("files"):
            raise proibido()
    g = guild_com_envio(sem_anexo)
    inter, canal = run(comprar(cog, membro(2), {"renascimento": 1}, g))
    assert canal.send.await_count == 2
    segunda = canal.send.call_args.kwargs
    assert not segunda.get("files")
    assert segunda["embeds"][1].image.url is None                      # a imagem quebrada saiu do embed
    assert "```" in segunda["embeds"][1].fields[1].value and "dados" not in segunda["embeds"][1].fields[1].value
    assert "Anexar Arquivos" in segunda["content"]                     # a staff fica sabendo o que ajustar
    assert "Avise a staff" not in inter.edit_original_response.call_args.kwargs["content"]
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "aguardando_pagamento"


def test_sem_permissao_de_embed_manda_so_texto_com_chave_e_total(cog):
    def sem_embed(**kw):
        if kw.get("embeds") or kw.get("files"):
            raise proibido()
    g = guild_com_envio(sem_embed)
    inter, canal = run(comprar(cog, membro(3), {"renascimento": 2}, g))
    assert canal.send.await_count == 3
    k = canal.send.call_args.kwargs
    assert "embeds" not in k and "files" not in k
    assert "R$ 30,00" in k["content"] and "PIX copia e cola" in k["content"] and "```" in k["content"]
    assert "Avise a staff" not in inter.edit_original_response.call_args.kwargs["content"]


def test_se_nada_for_possivel_a_compra_continua_registrada_e_a_pessoa_e_avisada(cog):
    def nada(**kw):
        raise proibido()
    g = guild_com_envio(nada)
    inter, canal = run(comprar(cog, membro(4), {"aviso": 1}, g))       # não pode levantar exceção
    assert canal.send.await_count == 3 and "01" in cog.armazenamento.dados["pedidos"]
    assert "Avise a staff" in inter.edit_original_response.call_args.kwargs["content"]


def test_erro_http_generico_tambem_cai_no_plano_b(cog):
    def instavel(**kw):
        if kw.get("files"):
            raise discord.HTTPException(MagicMock(status=500, reason="x"), "erro do Discord")
    g = guild_com_envio(instavel)
    _, canal = run(comprar(cog, membro(5), {"aviso": 1}, g))
    assert canal.send.await_count == 2


def test_se_o_qr_nao_puder_ser_gerado_o_ticket_avisa_e_mantem_a_chave(cog, monkeypatch):
    monkeypatch.setattr(loja.pix, "qr_png", MagicMock(side_effect=RuntimeError("sem biblioteca de imagem")))
    _, canal = run(comprar(cog, membro(8), {"aviso": 1}))
    kw = canal.send.call_args.kwargs
    assert not kw["files"] and kw["embeds"][1].image.url is None
    assert "Não consegui gerar o QR Code" in kw["embeds"][1].description and "```" in kw["embeds"][1].fields[1].value


def test_sem_pix_o_ticket_avisa_que_a_staff_envia_os_dados(cog, monkeypatch):
    monkeypatch.setattr(loja, "PIX_COPIA_COLA", "")
    _, canal = run(comprar(cog, membro(6), {"aviso": 1}))
    resumo = canal.send.call_args.kwargs["embeds"][0]
    campo = [f for f in resumo.fields if f.name == "Pagamento"]
    assert campo and "staff" in campo[0].value and not canal.send.call_args.kwargs["files"]


# ---------------------------------------------------------------- diagnóstico para a staff
def test_diagnostico_do_pix(monkeypatch):
    assert "PIX ativo" in loja.LojaCog._diagnostico_pix()[0] and "Loja de Teste" in loja.LojaCog._diagnostico_pix()[0]
    assert "QR Code gerando normalmente" in loja.LojaCog._diagnostico_pix()[1]
    monkeypatch.setattr(loja.pix, "qr_png", MagicMock(side_effect=ModuleNotFoundError("No module named 'PIL'")))
    assert "NÃO está sendo gerado" in loja.LojaCog._diagnostico_pix()[1] and "PIL" in loja.LojaCog._diagnostico_pix()[1]
    monkeypatch.undo()
    monkeypatch.setattr(loja, "PIX_COPIA_COLA", ""); monkeypatch.setattr(loja, "PIX_MOTIVO", "ausente")
    assert "não chegou ao bot" in loja.LojaCog._diagnostico_pix()[0]
    monkeypatch.setattr(loja, "PIX_MOTIVO", "invalido")
    assert "CRC" in loja.LojaCog._diagnostico_pix()[0]


def _perms(**falta):
    nomes = ["manage_channels", "send_messages", "embed_links", "attach_files", "read_message_history", "manage_messages"]
    return types.SimpleNamespace(**{n: n not in falta for n in nomes})


def _guild(perms, categoria=None):
    g = MagicMock(); g.me.guild_permissions = perms
    g.get_channel.return_value = categoria
    return g


def test_diagnostico_aponta_a_permissao_que_falta():
    linhas = loja.LojaCog._diagnostico_permissoes(_guild(_perms(attach_files=True)))
    texto = "\n".join(linhas)
    faltando = texto.split("Faltam permissões ao bot no servidor:")[1].split(".")[0]
    assert "Anexar Arquivos" in faltando                                # a que falta é listada
    assert "Gerenciar Canais" not in faltando and "Enviar Mensagens" not in faltando   # as que existem não aparecem
    assert "❌" in texto


def test_diagnostico_tudo_certo():
    texto = "\n".join(loja.LojaCog._diagnostico_permissoes(_guild(_perms())))
    assert "tudo certo" in texto and "❌" not in texto


def test_diagnostico_confere_as_permissoes_na_categoria(monkeypatch):
    monkeypatch.setattr(loja, "LOJA_CATEGORIA_ID", 123)
    cat = MagicMock(spec=discord.CategoryChannel); cat.name = "Compras"
    cat.permissions_for.return_value = _perms(attach_files=True, manage_channels=True)
    texto = "\n".join(loja.LojaCog._diagnostico_permissoes(_guild(_perms(), cat)))
    assert "Categoria das compras: **Compras**" in texto and "na categoria **Compras**" in texto
    assert "Anexar Arquivos" in texto and "Gerenciar Canais" in texto


def test_diagnostico_categoria_que_nao_existe(monkeypatch):
    monkeypatch.setattr(loja, "LOJA_CATEGORIA_ID", 999)
    texto = "\n".join(loja.LojaCog._diagnostico_permissoes(_guild(_perms(), None)))
    assert "Categoria não encontrada" in texto


def test_comando_de_diagnostico_mostra_tudo_e_tem_apelidos(cog):
    ctx = MagicMock(); ctx.author = staff(); ctx.send = AsyncMock(); ctx.guild = _guild(_perms(attach_files=True))
    run(cog.loja_armazenamento.callback(cog, ctx))
    texto = ctx.send.call_args.args[0]
    assert "Firebase conectado" in texto and "PIX ativo" in texto and "Anexar Arquivos" in texto
    assert {"loja_status", "loja_diagnostico"} <= set(cog.loja_armazenamento.aliases)


# ---------------------------------------------------------------- mensagens de inicialização (processo à parte)
def _importar_loja(pix_env):
    env = {**os.environ, "PIX_COPIA_COLA": pix_env, "FIREBASE_CREDENTIALS_BASE64": "", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", "import loja"], cwd=RAIZ, env=env, capture_output=True, text=True, encoding="utf-8")
    return r.stdout + r.stderr


def test_ao_iniciar_avisa_claramente_se_o_pix_nao_chegou():
    assert "PIX_COPIA_COLA não chegou ao bot" in _importar_loja("")


def test_ao_iniciar_avisa_se_o_pix_estiver_invalido():
    assert "inválido" in _importar_loja(PIX_TESTE[:-1] + "0" if PIX_TESTE[-1] != "0" else PIX_TESTE[:-1] + "1")


def test_ao_iniciar_confirma_o_pix_ativo():
    assert "PIX ativo (beneficiário: Loja de Teste)" in _importar_loja(PIX_TESTE)
