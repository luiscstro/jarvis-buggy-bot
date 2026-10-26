"""Opção fixa "Indicação" (R$ 0,00): sempre na loja, fora do banco/painel e fora dos VIPs dos planos."""
import asyncio

import pytest

import beneficios as ben
import catalogo as cat
import loja
from conftest import PIX_TESTE, comprar, guild_falso, interacao, interacao_staff_no_canal, membro, staff


def run(coro):
    return asyncio.run(coro)


def doc(nome="Alfa", preco=500, pagina="p1"):
    return {"nome": nome, "preco_centavos": preco, "descricao": "d", "pagina": pagina, "ordem": 1, "ativo": True}


def trocar(paginas, itens):
    p, i = cat.montar_catalogo(paginas, itens)
    loja.aplicar_catalogo(p, i, "firebase")


# ---------------------------------------------------------------- sempre presente, nunca editável
def test_esta_no_catalogo_padrao_da_loja_e_nao_no_do_banco():
    assert loja.ITENS["indicacao"]["preco"] == 0 and loja.ITENS["indicacao"]["fixo"] is True
    paginas, itens = cat.catalogo_padrao()
    assert "indicacao" not in itens and all(i["key"] != "indicacao" for p in paginas for i in p["itens"])    # o painel nunca a vê


def test_continua_la_mesmo_com_o_catalogo_vazio_ou_trocado():
    loja.aplicar_catalogo([], {}, "firebase")
    assert list(loja.ITENS) == ["indicacao"]
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {"a": doc()})
    assert list(loja.ITENS) == ["a", "indicacao"]


def test_item_do_banco_com_o_mesmo_nome_nao_a_substitui():
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {"indicacao": doc("Indicação cara", 5000)})
    assert loja.ITENS["indicacao"]["preco"] == 0 and loja.ITENS["indicacao"]["nome"] == "Indicação"
    menu = next(c for c in loja.LojaView(None, membro()).children if isinstance(c, loja.AdicionarSelect))
    assert [o.value for o in menu.options].count("indicacao") == 1


def test_e_a_primeira_do_menu_e_aparece_em_todas_as_paginas():
    trocar({"p1": {"ordem": 1, "introducao": ""}, "p2": {"ordem": 2, "introducao": ""}}, {"a": doc("A", 500, "p1"), "b": doc("B", 700, "p2")})
    v = loja.LojaView(None, membro())
    menu = next(c for c in v.children if isinstance(c, loja.AdicionarSelect))
    assert [o.label for o in menu.options] == ["Indicação", "A", "B"] and menu.options[0].description.startswith("Grátis")
    for n in (0, 1):
        v.pagina = n
        assert "Indicação" in v.embeds()[0].fields[0].name and "Grátis" in v.embeds()[0].fields[0].name


def test_menu_nunca_passa_de_25_opcoes_com_a_opcao_fixa():
    itens = {f"i{n:02d}": doc(f"Item {n}", 100 * (n + 1)) for n in range(25)}
    trocar({"p1": {"ordem": 1, "introducao": ""}}, itens)
    menu = next(c for c in loja.LojaView(None, membro()).children if isinstance(c, loja.AdicionarSelect))
    assert len(menu.options) == 25 and menu.options[0].value == "indicacao"


def test_preco_aparece_como_gratis():
    assert cat.preco_txt(0) == "Grátis" and cat.preco_txt(500) == "R$ 5,00"
    assert loja.linhas_carrinho({"indicacao": 2}) == "**2x** Indicação — Grátis"
    v = loja.LojaView(None, membro())
    v.carrinho = {"indicacao": 1}
    assert v.embeds()[1].footer.text == "Total: Grátis"


# ---------------------------------------------------------------- comprar a indicação
def test_so_indicacao_abre_o_canal_sem_pix(cog):
    inter, canal = run(comprar(cog, membro(1, "Ana"), {"indicacao": 1}))
    assert "criada" in inter.edit_original_response.call_args.kwargs["content"]
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["total_centavos"] == 0 and ped["status"] == "aguardando_pagamento"
    assert ped["itens_detalhe"] == [{"nome": "Indicação", "qtd": 1, "preco_centavos": 0}]
    kw = canal.send.call_args.kwargs
    assert len(kw["embeds"]) == 1 and not kw.get("files")                       # sem QR e sem PIX
    resumo = kw["embeds"][0]
    campos = {f.name: f.value for f in resumo.fields}
    assert campos["Total a pagar"] == "**Grátis**" and "quem você indicou" in campos["🤝 Indicação"] and "nada a pagar" in resumo.footer.text
    assert PIX_TESTE[:20] not in str(kw)


def test_compra_mista_cobra_so_o_que_tem_preco_e_mantem_o_pix(cog):
    inter, canal = run(comprar(cog, membro(2), {"indicacao": 1, "escolhidos": 2}))
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["total_centavos"] == 1000
    kw = canal.send.call_args.kwargs
    assert len(kw["embeds"]) == 2 and PIX_TESTE[:20] in kw["embeds"][1].fields[1].value
    assert "Indicação — Grátis" in kw["embeds"][0].description and "R$ 10,00" in kw["embeds"][0].description


def test_aprovar_indicacao_nao_fala_em_pagamento_nem_em_gacha_e_vai_para_o_registro(cog, registro):
    g = guild_falso()
    inter, canal = run(comprar(cog, membro(3, "Caio"), {"indicacao": 3}, g))
    i = interacao_staff_no_canal(canal.id, staff(12, "Beto"))
    run(cog.mudar_status(i, "paga"))
    aviso = i.channel.send.call_args.args[0]
    assert "Aprovado por" in aviso and "Pagamento" not in aviso and "gacha" not in aviso
    post = registro.mensagens[0].embeds[0]
    campos = {f.name: f.value for f in post.fields}
    assert post.title == "🤝 Indicação #01 aprovada" and "registrou uma indicação" in post.description
    assert "3x" in campos["🛍️ Itens"] and "Indicação" in campos["🛍️ Itens"]
    assert campos["💵 Total"] == "**Grátis**" and "🎰 Gachas" not in campos and "✅ Aprovado por" in campos
    i2 = interacao_staff_no_canal(canal.id, staff(12, "Beto"))
    run(cog.mudar_status(i2, "entregue"))
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "entregue" and len(registro.mensagens) == 1


def test_compra_paga_continua_dizendo_pagamento_confirmado(cog):
    g = guild_falso()
    _, canal = run(comprar(cog, membro(4), {"escolhidos": 1}, g))
    i = interacao_staff_no_canal(canal.id, staff(12, "Beto"))
    run(cog.mudar_status(i, "paga"))
    assert "Pagamento confirmado" in i.channel.send.call_args.args[0]


def test_nao_ha_mudanca_de_preco_que_a_pessoa_nao_tenha_visto(cog):
    """A opção fixa entra na assinatura de preços, mas ela nunca muda: editar o catálogo continua pausando o checkout."""
    a = loja.ASSINATURA
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {"a": doc("A", 500)})
    antes = loja.ASSINATURA
    trocar({"p1": {"ordem": 1, "introducao": ""}}, {"a": doc("A", 600)})
    assert antes != loja.ASSINATURA and a != antes


# ---------------------------------------------------------------- fora dos planos mensais
def test_nao_e_opcao_de_vip_dos_beneficios():
    assert "indicacao" not in {o["key"] for o in ben.opcoes_de_escolha(loja.ITENS)}
    ok, msg = ben.validar_escolhas(1, ["indicacao"], loja.ITENS)
    assert ok is False and "não está mais disponível" in msg
    assert ben.validar_escolhas(2, ["escolhidos", "indicacao"], loja.ITENS)[0] is False


def test_menu_dos_beneficios_nao_mostra_a_indicacao(cog, registro):
    from datetime import datetime, timezone
    g = guild_falso()
    cog.bot.get_guild = lambda gid: g
    user = membro(5, "Dee", cargo_ids=[ben.CARGO_ASSINANTE_2_ID])
    inter = interacao(user, g)
    inter.created_at = datetime(2026, 10, 16, 15, tzinfo=timezone.utc)
    run(cog.abrir_beneficios(inter))
    view = inter.followup.send.call_args.kwargs["view"]
    for menu in (c for c in view.children if isinstance(c, ben.EscolhaSelect)):
        assert all(o.value != "indicacao" and "Indica" not in o.label for o in menu.options)


@pytest.mark.parametrize("nivel", [1, 2])
def test_resgate_forjado_com_a_indicacao_e_recusado(cog, registro, bd, nivel):
    from datetime import datetime, timezone
    g = guild_falso()
    cog.bot.get_guild = lambda gid: g
    user = membro(6, "Eli", cargo_ids=[ben.NIVEIS[nivel]["cargo_id"]])
    view = ben.BeneficioView(cog, user, nivel, "2026-10")
    view.escolhas = ["indicacao"] * ben.NIVEIS[nivel]["escolhas"]
    inter = interacao(user, g)
    inter.created_at = datetime(2026, 10, 16, 15, tzinfo=timezone.utc)
    run(cog.resgatar_beneficio(inter, view))
    assert g.canais == [] and not bd.cols.get("loja_beneficios") and not cog.armazenamento.dados["pedidos"]


def test_diagnostico_conta_so_os_itens_do_catalogo():
    loja.aplicar_catalogo(*cat.catalogo_padrao(), "padrao")
    assert "**12** item(ns)" in loja.LojaCog._diagnostico_catalogo()[0]
