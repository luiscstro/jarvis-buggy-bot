"""A loja do Discord com catálogo que muda: telas, checkout seguro, concorrência e níveis de acesso."""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import catalogo as cat
import loja
from conftest import comprar, drenar, guild_falso, interacao, membro, staff


def run(coro):
    return asyncio.run(coro)


def doc(nome="Item", preco=500, pagina="p1", ordem=1, ativo=True, descricao="d"):
    return {"nome": nome, "preco_centavos": preco, "descricao": descricao, "pagina": pagina, "ordem": ordem, "ativo": ativo}


def trocar(paginas_docs, itens_docs, fonte="firebase"):
    loja.aplicar_catalogo(*cat.montar_catalogo(paginas_docs, itens_docs), fonte)


def catalogo_simples(**precos):
    """Catálogo com os itens a, b, c nos preços informados."""
    trocar({"p1": {"introducao": "Intro", "ordem": 1}},
           {k: doc(k.upper(), p, ordem=n) for n, (k, p) in enumerate(precos.items())})


def carrinho_aberto(cog, user, itens):
    v = loja.LojaView(cog, user)
    v.carrinho = dict(itens)
    v.montar()
    return v


async def finalizar(cog, view, user, guild=None):
    guild = guild or guild_falso()
    inter = interacao(user, guild)
    await cog.criar_compra(inter, view)
    return inter, guild


# ---------------------------------------------------------------- telas seguem o catálogo
def test_tela_reflete_itens_precos_e_paginas_do_catalogo_atual():
    trocar({"p1": {"introducao": "Bem-vindo", "ordem": 1}, "p2": {"introducao": "", "ordem": 2}},
           {"a": doc("Alfa", 1234, "p1"), "b": doc("Beta", 99, "p2")})
    v = loja.LojaView(None, membro())
    botoes = [c for c in v.children if isinstance(c, loja.PaginaButton)]
    assert [b.label for b in botoes] == ["Página 1", "Página 2"]
    menu = next(c for c in v.children if isinstance(c, loja.AdicionarSelect))
    assert [(o.label, o.description) for o in menu.options] == [("Indicação", "Grátis — opção fixa, para indicações"), ("Alfa", "R$ 12,34 — Página 1"), ("Beta", "R$ 0,99 — Página 2")]
    assert "## 🔷 Alfa - R$ 12,34" in v.embeds()[0].description and v.embeds()[0].description.startswith("Bem-vindo")


def test_pagina_aberta_que_deixou_de_existir_volta_para_uma_valida():
    trocar({"p1": {"ordem": 1, "introducao": ""}, "p2": {"ordem": 2, "introducao": ""}, "p3": {"ordem": 3, "introducao": ""}}, {"a": doc()})
    v = loja.LojaView(None, membro())
    v.pagina = 2
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {"a": doc()})
    inter = interacao(membro(), None)
    run(v.atualizar(inter))
    assert v.pagina == 0 and len([c for c in v.children if isinstance(c, loja.PaginaButton)]) == 1
    assert inter.response.edit_message.call_args.kwargs["content"] is None      # preços iguais: nada a avisar


def test_loja_sem_itens_mostra_aviso_e_nao_quebra():
    loja.aplicar_catalogo([], {}, "firebase")
    v = loja.LojaView(None, membro())
    assert not any(isinstance(c, loja.PaginaButton) for c in v.children)
    menu = next(c for c in v.children if isinstance(c, loja.AdicionarSelect))
    assert [o.value for o in menu.options] == ["indicacao"]                        # só a opção fixa (grátis) continua disponível
    assert "sem itens" in v.embeds()[0].description
    assert next(c for c in v.children if isinstance(c, loja.FinalizarButton)).disabled


def test_pagina_sem_itens_mostra_so_a_introducao():
    trocar({"p1": {"ordem": 1, "introducao": "Em breve!"}}, {})
    assert loja.LojaView(None, membro()).embeds()[0].description == "Em breve!"
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {})
    assert "vazia" in loja.LojaView(None, membro()).embeds()[0].description


def test_menu_nunca_passa_de_25_opcoes():
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {f"i{n:02d}": doc(f"Item {n}", ordem=n) for n in range(40)})
    menu = next(c for c in loja.LojaView(None, membro()).children if isinstance(c, loja.AdicionarSelect))
    assert len(menu.options) == 25 and menu.max_values == 25


# ---------------------------------------------------------------- o carrinho acompanha o catálogo
def test_item_que_sai_da_loja_sai_do_carrinho_com_aviso():
    catalogo_simples(a=100, b=200)
    v = carrinho_aberto(None, membro(), {"a": 1, "b": 2})
    catalogo_simples(a=100)
    inter = interacao(membro(), None)
    run(v.atualizar(inter))
    assert v.carrinho == {"a": 1}
    aviso = inter.response.edit_message.call_args.kwargs["content"]
    assert "catálogo da loja foi atualizado" in aviso and "Tirei do carrinho" in aviso


def test_total_e_linhas_ignoram_item_que_saiu():
    catalogo_simples(a=100)
    assert loja.total_carrinho({"a": 2, "fantasma": 5}) == 200 and "fantasma" not in loja.linhas_carrinho({"a": 2, "fantasma": 5})


def test_menus_e_janela_toleram_item_que_saiu_enquanto_estavam_abertos():
    catalogo_simples(a=100, b=200)
    v = carrinho_aberto(None, membro(), {"a": 1})
    menu = next(c for c in v.children if isinstance(c, loja.AdicionarSelect))
    menu._values = ["b", "a"]
    catalogo_simples(a=100)                               # b saiu da loja depois de o menu abrir
    inter = interacao(membro(), None)
    run(menu.callback(inter))                             # só "a" é considerado; abre a janela sem quebrar
    inter.response.send_modal.assert_awaited_once()
    assert list(inter.response.send_modal.call_args.args[0].campos) == ["a"]

    menu._values = ["b"]                                  # só o que saiu: avisa e redesenha
    inter2 = interacao(membro(), None)
    run(menu.callback(inter2))
    assert "saíram da loja" in inter2.response.edit_message.call_args.kwargs["content"]

    ed = next(c for c in carrinho_aberto(None, membro(), {"a": 1}).children if isinstance(c, loja.QuantidadeSelect))
    ed._values = ["a"]
    catalogo_simples()                                    # loja ficou sem nenhum item
    inter3 = interacao(membro(), None)
    ed.view.carrinho = {"a": 1}
    run(ed.callback(inter3))
    assert "saíram da loja" in inter3.response.edit_message.call_args.kwargs["content"]


def test_janela_de_quantidades_ignora_item_que_saiu_ao_enviar():
    catalogo_simples(a=100, b=200)
    v = carrinho_aberto(None, membro(), {})
    m = loja.QuantidadesModal(v, ["a", "b"])
    m.campos["a"]._value, m.campos["b"]._value = "2", "3"
    catalogo_simples(a=100)
    run(m.on_submit(interacao(membro(), None)))
    assert v.carrinho == {"a": 2}


# ---------------------------------------------------------------- checkout: nunca cobrar um preço que a pessoa não viu
def test_preco_mudou_depois_de_ver_o_carrinho_pausa_a_compra_e_mostra_o_novo_preco(cog):
    catalogo_simples(a=500)
    user = membro(1)
    v = carrinho_aberto(cog, user, {"a": 2})
    catalogo_simples(a=900)                               # a staff mudou o preço enquanto a pessoa decidia
    inter, g = run(finalizar(cog, v, user))
    assert g.canais == [] and cog.armazenamento.dados["pedidos"] == {}              # nada foi cobrado
    aviso = inter.response.edit_message.call_args.kwargs["content"]
    assert "catálogo foi atualizado" in aviso and "Finalizar compra" in aviso
    assert "R$ 18,00" in inter.response.edit_message.call_args.kwargs["embeds"][1].footer.text    # 2 × R$ 9,00
    # ela confere e finaliza de novo: agora vale o preço novo, que ela viu
    inter2, g2 = run(finalizar(cog, v, user))
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["total_centavos"] == 1800 and ped["itens_detalhe"][0]["preco_centavos"] == 900


def test_item_removido_antes_de_finalizar_pausa_a_compra(cog):
    catalogo_simples(a=100, b=200)
    user = membro(2)
    v = carrinho_aberto(cog, user, {"a": 1, "b": 1})
    catalogo_simples(a=100)
    inter, g = run(finalizar(cog, v, user))
    assert g.canais == [] and v.carrinho == {"a": 1}
    inter2, g2 = run(finalizar(cog, v, user))
    assert cog.armazenamento.dados["pedidos"]["01"]["total_centavos"] == 100


def test_mudar_so_a_descricao_ou_o_nome_nao_interrompe_a_compra(cog):
    catalogo_simples(a=500)
    user = membro(3)
    v = carrinho_aberto(cog, user, {"a": 1})
    trocar({"p1": {"introducao": "Intro nova", "ordem": 1}}, {"a": doc("Nome novo", 500, descricao="texto novo")})
    inter, g = run(finalizar(cog, v, user))
    assert len(g.canais) == 1 and cog.armazenamento.dados["pedidos"]["01"]["itens_detalhe"][0]["nome"] == "Nome novo"


def test_pedido_guarda_nome_e_preco_do_momento_mesmo_se_o_catalogo_mudar_depois(cog):
    catalogo_simples(a=500)
    user = membro(4)
    run(finalizar(cog, carrinho_aberto(cog, user, {"a": 3}), user))
    catalogo_simples(a=9999)
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["total_centavos"] == 1500 and ped["itens_detalhe"] == [{"nome": "A", "qtd": 3, "preco_centavos": 500}]


def test_ticket_usa_a_fotografia_da_compra_e_nao_o_catalogo_atual(cog):
    catalogo_simples(a=500)
    user = membro(5)
    v = carrinho_aberto(cog, user, {"a": 2})
    guild = guild_falso()
    original = guild.create_text_channel.side_effect

    async def criar_e_mudar_catalogo(nome, **kw):          # o catálogo muda NO MEIO da criação do canal
        catalogo_simples(a=7777)
        return await original(nome, **kw)
    guild.create_text_channel = AsyncMock(side_effect=criar_e_mudar_catalogo)
    run(finalizar(cog, v, user, guild))
    resumo = guild.canais[0].send.call_args.kwargs["embeds"][0]
    assert "R$ 10,00" in resumo.description and "R$ 10,00" in resumo.fields[0].value        # 2 × R$ 5,00, o que ela viu
    assert cog.armazenamento.dados["pedidos"]["01"]["total_centavos"] == 1000


def test_catalogo_vazio_com_carrinho_antigo_nao_cria_compra(cog):
    catalogo_simples(a=100)
    user = membro(6)
    v = carrinho_aberto(cog, user, {"a": 1})
    loja.aplicar_catalogo([], {}, "firebase")
    inter, g = run(finalizar(cog, v, user))
    assert g.canais == [] and v.carrinho == {} and cog.armazenamento.dados["pedidos"] == {}


# ---------------------------------------------------------------- concorrência: o catálogo muda enquanto muita gente compra
def test_catalogo_mudando_durante_o_pico_nunca_gera_compra_inconsistente(cog, monkeypatch):
    monkeypatch.setattr(loja, "LOJA_MAX_ABERTAS", 0)
    precos = [500, 700]
    catalogo_simples(a=precos[0], b=100)
    parar = threading.Event()

    def editor():                                          # a staff mexendo nos preços sem parar (outra thread, como no bot)
        n = 0
        while not parar.is_set():
            catalogo_simples(a=precos[n % 2], b=100)
            n += 1
    t = threading.Thread(target=editor, daemon=True); t.start()

    async def pico():
        g = guild_falso(sleep=True)
        usuarios = [membro(100 + i, f"J{i}") for i in range(80)]
        views = [carrinho_aberto(cog, u, {"a": 1 + i % 3, "b": 1}) for i, u in enumerate(usuarios)]
        await asyncio.gather(*[finalizar(cog, v, u, g) for v, u in zip(views, usuarios)])
        return g
    try:
        run(pico())
    finally:
        parar.set(); t.join()
    for p in cog.armazenamento.dados["pedidos"].values():
        assert p["total_centavos"] == sum(d["qtd"] * d["preco_centavos"] for d in p["itens_detalhe"])    # total sempre bate com o que foi cobrado
        assert {d["nome"]: d["preco_centavos"] for d in p["itens_detalhe"]}["A"] in precos           # preço de A é um dos que existiram
        assert {d["nome"]: d["preco_centavos"] for d in p["itens_detalhe"]}["B"] == 100


# ---------------------------------------------------------------- níveis de acesso ao painel
def _ctx(autor):
    c = MagicMock(); c.author = autor; c.send = AsyncMock(); c.message.delete = AsyncMock()
    return c


def acesso(cog, *args, autor=None):
    c = _ctx(autor or staff())
    run(cog.loja_acesso.callback(cog, c, *args))
    return c


def test_adicionar_cria_so_visualizacao_e_nao_rebaixa_um_editor(cog, bd):
    c = acesso(cog, "adicionar", "ana@x.com")
    assert bd.cols["loja_staff"]["ana@x.com"]["editor"] is False and "só para ver" in c.send.call_args.args[0]
    acesso(cog, "editor", "ana@x.com")
    acesso(cog, "adicionar", "ana@x.com")                  # "adicionar" de novo mantém o nível que ela já tinha
    assert bd.cols["loja_staff"]["ana@x.com"]["editor"] is True


def test_editor_e_visualizar_trocam_o_nivel_e_preservam_quem_adicionou(cog, bd):
    acesso(cog, "adicionar", "bia@x.com", autor=staff(7, "Primeiro"))
    c = acesso(cog, "editor", "bia@x.com", autor=staff(8, "Segundo"))
    d = bd.cols["loja_staff"]["bia@x.com"]
    assert d["editor"] is True and "(7)" in d["adicionado_por"] and "(8)" in d["atualizado_por"] and "editar o catálogo" in c.send.call_args.args[0]
    acesso(cog, "visualizar", "bia@x.com")
    assert bd.cols["loja_staff"]["bia@x.com"]["editor"] is False


def test_editor_cria_a_pessoa_se_ela_nao_existe_e_o_email_vai_em_minusculas(cog, bd):
    acesso(cog, "editor", "  CARLA@Gmail.COM ")
    assert bd.cols["loja_staff"]["carla@gmail.com"]["editor"] is True


def test_listar_mostra_quem_edita_e_quem_so_ve(cog):
    acesso(cog, "editor", "ed@x.com"); acesso(cog, "adicionar", "ver@x.com")
    texto = acesso(cog, "listar").send.call_args.args[0]
    assert "✏️ edita" in texto and "`ed@x.com`" in texto and "👁️ só vê" in texto and "`ver@x.com`" in texto


@pytest.mark.parametrize("quem", [membro(2), membro(3, cargos=("moderação",))])
@pytest.mark.parametrize("acao", ["editor", "visualizar", "adicionar", "remover"])
def test_so_a_staff_do_discord_muda_niveis(cog, bd, quem, acao):
    c = acesso(cog, acao, "x@x.com", autor=quem)
    assert "Sem permissão" in c.send.call_args.args[0] and not bd.cols.get("loja_staff")


def test_uso_incorreto_explica_os_niveis(cog, bd):
    texto = acesso(cog, "promover", "x@x.com").send.call_args.args[0]
    assert "editor <email>" in texto and "visualizar" in texto and not bd.cols.get("loja_staff")
    assert "Use:" in acesso(cog, "editor", "nao-e-email").send.call_args.args[0]


def test_remover_apaga_o_nivel_tambem(cog, bd):
    acesso(cog, "editor", "z@x.com")
    assert "removido" in acesso(cog, "remover", "z@x.com").send.call_args.args[0] and "z@x.com" not in bd.cols["loja_staff"]


# ---------------------------------------------------------------- diagnóstico
def test_diagnostico_mostra_a_origem_do_catalogo():
    for fonte, trecho in (("firebase", "ao vivo"), ("cache", "cópia local"), ("padrao", "padrão do código")):
        loja.aplicar_catalogo(*cat.catalogo_padrao(), fonte)
        texto = loja.LojaCog._diagnostico_catalogo()[0]
        assert trecho in texto and "12" in texto and "3" in texto
