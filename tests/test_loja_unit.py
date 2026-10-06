"""Lógica da loja: formatação, catálogo, carrinho, janela de quantidades e limites do Discord."""
import asyncio
import random
import re

import discord
import pytest

import loja
from conftest import comprar, interacao, membro, staff


# ---------------------------------------------------------------- formatação
@pytest.mark.parametrize("centavos,esperado", [
    (0, "R$ 0,00"), (1, "R$ 0,01"), (99, "R$ 0,99"), (100, "R$ 1,00"), (500, "R$ 5,00"),
    (12345, "R$ 123,45"), (150000, "R$ 1500,00"),
])
def test_brl(centavos, esperado):
    assert loja.brl(centavos) == esperado


@pytest.mark.parametrize("nick,esperado", [
    ("Percy", "percy"), ("Percy Jackson", "percy-jackson"), ("João da Sílva", "joao-da-silva"),
    ("  espaços  ", "espacos"), ("a__b--c", "a-b-c"), ("ÀÉÎÕÜ", "aeiou"), ("𝕻𝖊𝖗𝖈𝖞", "percy"),
    ("🤡🤡🤡", "cliente"), ("", "cliente"), ("---", "cliente"), ("日本語", "cliente"),
    ("@everyone", "everyone"), ("#geral", "geral"), ("../../etc/passwd", "etc-passwd"),
])
def test_slug_nick(nick, esperado):
    assert loja.slug_nick(nick) == esperado


def test_slug_nick_fuzz_sempre_gera_nome_de_canal_valido():
    rng = random.Random(7)
    alfabeto = [chr(c) for c in list(range(0, 600)) + list(range(0x1F300, 0x1F3FF)) + [0x202E, 0x200B, 0xFEFF]]
    for _ in range(2000):
        nick = "".join(rng.choice(alfabeto) for _ in range(rng.randint(0, 120)))
        slug = loja.slug_nick(nick)
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug), repr(nick)
        assert 1 <= len(slug) <= 80


# ---------------------------------------------------------------- catálogo
def test_catalogo_chaves_unicas_e_precos_inteiros_positivos():
    chaves = [i["key"] for p in loja.PAGINAS for i in p["itens"]]
    assert len(chaves) == len(set(chaves)) == len(loja.ITENS) == 12
    for item in loja.ITENS.values():
        assert isinstance(item["preco"], int) and item["preco"] > 0 and item["preco"] % 500 == 0
        assert 0 < len(item["nome"]) <= 100


def test_precos_conferem_com_o_texto_da_loja():
    esperado = {"escolhidos": 500, "mudanca": 500, "renascimento": 1500, "manobra": 500, "aviso": 500, "cacador": 1000,
                "sorte": 1500, "token": 500, "aprendiz": 1000, "lunariano": 2000, "rumores": 500, "supertesouro": 2000}
    assert {k: v["preco"] for k, v in loja.ITENS.items()} == esperado


def test_descricoes_cabem_nos_limites_do_discord():
    for p in loja.PAGINAS:
        assert len(p["descricao"]) <= 4096


def test_menu_de_itens_respeita_limites_do_discord():
    sel = loja.AdicionarSelect()
    assert len(sel.options) == len(loja.ITENS) <= 25
    assert sel.max_values == len(sel.options)
    for o in sel.options:
        assert len(o.label) <= 100 and len(o.description) <= 100 and len(o.value) <= 100


def test_embed_com_todos_os_itens_no_maximo_cabe_no_limite():
    v = loja.LojaView(None, membro())
    v.carrinho = {k: 99999 for k in loja.ITENS}
    for pag in range(len(loja.PAGINAS)):
        v.pagina = pag
        embeds = v.embeds()
        assert sum(len(e.description or "") + len(e.title or "") + len(e.footer.text or "") for e in embeds) < 6000
    assert loja.total_carrinho(v.carrinho) == sum(i["preco"] for i in loja.ITENS.values()) * 99999


def test_total_e_linhas():
    c = {"escolhidos": 2, "renascimento": 1}
    assert loja.total_carrinho(c) == 2500
    linhas = loja.linhas_carrinho(c).splitlines()
    assert linhas == ["**2x** Escolhidos dos Mares — R$ 10,00", "**1x** Renascimento — R$ 15,00"]


def test_ajuda_do_bot_respeita_limites_do_discord():
    import bot
    e = bot._build_full_help_embed()
    assert all(len(f.value) <= 1024 for f in e.fields) and len(e) < 6000 and len(e.fields) <= 25


# ---------------------------------------------------------------- janela de quantidades
def _modal(view, keys, valores):
    m = loja.QuantidadesModal(view, keys)
    for k, v in zip(keys, valores):
        m.campos[k]._value = v
    return m


async def _submeter(view, keys, valores):
    inter = interacao(membro(), None)
    await _modal(view, keys, valores).on_submit(inter)
    return inter


@pytest.mark.parametrize("digitado,esperado", [
    ("3", 3), (" 7 ", 7), ("0", None), ("99999", 99999), ("１２", 12), ("-5", None), ("00004", 4),
])
def test_quantidade_aceita(digitado, esperado):
    v = loja.LojaView(None, membro())
    v.carrinho = {"escolhidos": 9}
    asyncio.run(_submeter(v, ["escolhidos"], [digitado]))
    assert v.carrinho.get("escolhidos") == esperado


@pytest.mark.parametrize("lixo", ["abc", "", " ", "3.5", "1e3", "3,5", "--2", "٣x", "NaN", "∞"])
def test_quantidade_invalida_nao_altera_o_carrinho(lixo):
    v = loja.LojaView(None, membro())
    v.carrinho = {"escolhidos": 2}
    inter = asyncio.run(_submeter(v, ["escolhidos"], [lixo]))
    assert v.carrinho == {"escolhidos": 2}
    assert "inválida" in inter.response.send_message.call_args.args[0]


def test_um_campo_invalido_cancela_todos_os_outros():
    v = loja.LojaView(None, membro())
    v.carrinho = {"escolhidos": 1}
    asyncio.run(_submeter(v, ["escolhidos", "renascimento"], ["5", "x"]))
    assert v.carrinho == {"escolhidos": 1}


def test_ate_cinco_itens_abre_a_janela_e_mais_que_isso_adiciona_direto():
    v = loja.LojaView(None, membro())
    sel = next(c for c in v.children if getattr(c, "row", None) == 1)
    inter = interacao(membro(), None)
    sel._values = list(loja.ITENS)[:5]
    asyncio.run(sel.callback(inter))
    inter.response.send_modal.assert_awaited_once()
    assert v.carrinho == {}

    v.carrinho = {"escolhidos": 4}
    sel2 = next(c for c in v.children if getattr(c, "row", None) == 1)
    sel2._values = list(loja.ITENS)  # os 12 de uma vez
    inter2 = interacao(membro(), None)
    asyncio.run(sel2.callback(inter2))
    assert len(v.carrinho) == 12 and v.carrinho["escolhidos"] == 4  # o que já tinha não é sobrescrito
    assert all(q >= 1 for q in v.carrinho.values())
    assert "12 itens" in inter2.response.edit_message.call_args.kwargs["content"]


def test_menu_do_carrinho_edita_ate_cinco_por_vez():
    v = loja.LojaView(None, membro())
    v.carrinho = {k: 1 for k in loja.ITENS}
    v.montar()
    ed = next(c for c in v.children if getattr(c, "row", None) == 2)
    assert ed.max_values == 5 and len(ed.options) == 12


def test_botoes_finalizar_e_limpar_dependem_do_carrinho():
    v = loja.LojaView(None, membro())
    fin = next(c for c in v.children if isinstance(c, loja.FinalizarButton))
    assert fin.disabled
    v.carrinho = {"escolhidos": 1}
    v.montar()
    fin = next(c for c in v.children if isinstance(c, loja.FinalizarButton))
    assert not fin.disabled


# ---------------------------------------------------------------- permissões básicas
def test_eh_staff(cog):
    assert cog.eh_staff(staff())
    assert cog.eh_staff(membro(cargos=("Developer",)))
    assert not cog.eh_staff(membro(cargos=("Membro", "VIP")))
    assert not cog.eh_staff(membro(cargos=("moderação",)))     # o nome do cargo é exato
    outro = discord.User  # um usuário sem cargos (fora de servidor) nunca é staff
    assert not cog.eh_staff(object())
