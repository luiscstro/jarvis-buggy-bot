"""Catálogo editável: validação dos documentos do banco, montagem das páginas, semeadura e sincronização ao vivo."""
import json
import random
import threading
from datetime import datetime, timezone

import pytest

import catalogo as cat
import loja
from catalogo_padrao import ITENS_PADRAO, PAGINAS_PADRAO


def item_doc(nome="Item", preco=500, pagina="p1", ordem=1, ativo=True, descricao="", **extra):
    return {"nome": nome, "preco_centavos": preco, "descricao": descricao, "pagina": pagina, "ordem": ordem, "ativo": ativo, **extra}


def pagina_doc(ordem=1, introducao=""):
    return {"introducao": introducao, "ordem": ordem}


# ---------------------------------------------------------------- catálogo padrão (o que existia antes)
def test_catalogo_padrao_mantem_paginas_itens_e_precos_de_antes():
    paginas, itens = cat.catalogo_padrao()
    assert [len(p["itens"]) for p in paginas] == [3, 4, 5] and len(itens) == 12
    assert {k: v["preco"] for k, v in itens.items()} == {
        "escolhidos": 500, "mudanca": 500, "renascimento": 1500, "manobra": 500, "aviso": 500, "cacador": 1000,
        "sorte": 1500, "token": 500, "aprendiz": 1000, "lunariano": 2000, "rumores": 500, "supertesouro": 2000}


def test_cada_pagina_mostra_titulo_com_nome_e_preco_seguido_da_descricao():
    paginas, _ = cat.catalogo_padrao()
    for p in paginas:
        for i in p["itens"]:
            assert f"## 🔷 {i['nome']} - {cat.brl(i['preco'])}\n{i['descricao'][:40]}" in p["descricao"]
    assert paginas[0]["descricao"].startswith("# __APOIO AO SERVIDOR__")          # introdução da página 1
    assert all(len(p["descricao"]) <= cat.MAX_TEXTO_PAGINA for p in paginas)


def test_nenhuma_descricao_padrao_repete_o_titulo_nem_o_preco():
    for i in ITENS_PADRAO:
        assert not i["descricao"].lstrip().startswith("## 🔷") and "R$" not in i["descricao"].split("\n")[0]


def test_textos_originais_foram_preservados_palavra_por_palavra():
    texto = " ".join(p["descricao"] for p in cat.catalogo_padrao()[0])
    for trecho in ("Essa benção irá conceder o dobro das recompensas da missão", "Trocar o tipo de arma meito escolhida",
                   "RB que mudarem a __Raça do Personagem__", "O usuário que perder a akuma no mi com este recurso",
                   "Pode rolar novamente __**o dado de tesouro do arqueólogo depois de acharem a ruína**__",
                   "Proposto por Roku", "Blues e Paradise: x2.", "https://discord.com/channels/1220345032079572993/1463579296130928650"):
        assert trecho in texto, trecho


# ---------------------------------------------------------------- validação de itens (entrada não confiável)
@pytest.mark.parametrize("chave", ["", "A", "Escolhidos", "a/b", "../x", "a b", "-x", "_x", "x" * 41, "é", "a.b", "a\nb", None, 5])
def test_chave_invalida_e_ignorada(chave):
    assert cat.normalizar_item(chave, item_doc()) is None


@pytest.mark.parametrize("chave", ["a", "item1", "meu-item", "meu_item", "9x", "x" * 40])
def test_chave_valida(chave):
    assert cat.normalizar_item(chave, item_doc())["key"] == chave


@pytest.mark.parametrize("preco", [0, -1, -500, cat.PRECO_MAX + 1, True, False, 5.0, 5.5, "500", None, [], {}, float("nan")])
def test_preco_invalido_e_ignorado(preco):
    assert cat.normalizar_item("x", item_doc(preco=preco)) is None


@pytest.mark.parametrize("preco", [1, 99, 500, 12345, cat.PRECO_MAX])
def test_preco_valido(preco):
    assert cat.normalizar_item("x", item_doc(preco=preco))["preco"] == preco


@pytest.mark.parametrize("nome", ["", "   ", "x" * 61, None, 5, ["a"]])
def test_nome_invalido_e_ignorado(nome):
    assert cat.normalizar_item("x", item_doc(nome=nome)) is None


def test_nome_e_aparado_e_aceita_ate_60_caracteres():
    assert cat.normalizar_item("x", item_doc(nome="  Nome  "))["nome"] == "Nome"
    assert cat.normalizar_item("x", item_doc(nome="n" * 60))["nome"] == "n" * 60


def test_campos_opcionais_tem_padroes_seguros():
    i = cat.normalizar_item("x", {"nome": "N", "preco_centavos": 100})
    assert i["descricao"] == "" and i["pagina"] == "" and i["ordem"] == 0 and i["ativo"] is True


@pytest.mark.parametrize("ativo,esperado", [(True, True), (False, False), (None, False), ("true", False), (1, False), ("sim", False)])
def test_so_o_valor_true_deixa_o_item_visivel(ativo, esperado):
    assert cat.normalizar_item("x", item_doc(ativo=ativo))["ativo"] is esperado


def test_descricao_gigante_e_cortada_e_tipos_estranhos_viram_vazio():
    assert len(cat.normalizar_item("x", item_doc(descricao="d" * 9999))["descricao"]) == cat.MAX_DESCRICAO
    assert cat.normalizar_item("x", item_doc(descricao=123))["descricao"] == ""
    assert cat.normalizar_item("x", item_doc(ordem=True))["ordem"] == 0 and cat.normalizar_item("x", item_doc(ordem="3"))["ordem"] == 0


@pytest.mark.parametrize("doc", [None, [], "texto", 5, ()])
def test_documento_que_nao_e_dicionario_e_ignorado(doc):
    assert cat.normalizar_item("x", doc) is None and cat.normalizar_pagina("p1", doc) is None


@pytest.mark.parametrize("pid", ["", "p0", "p10", "P1", "pagina1", "x", "p", "1", None])
def test_id_de_pagina_invalido(pid):
    assert cat.normalizar_pagina(pid, pagina_doc()) is None


def test_pagina_valida_e_normalizada():
    p = cat.normalizar_pagina("p3", {"introducao": "  oi  ", "ordem": 7})
    assert p == {"id": "p3", "ordem": 7, "introducao": "oi"}
    assert cat.normalizar_pagina("p1", {"introducao": 5, "ordem": "a"}) == {"id": "p1", "ordem": 0, "introducao": ""}
    assert len(cat.normalizar_pagina("p1", {"introducao": "i" * 9999})["introducao"]) == cat.MAX_INTRO


# ---------------------------------------------------------------- montagem
def test_ordem_das_paginas_e_dos_itens_vem_da_staff():
    paginas, itens = cat.montar_catalogo(
        {"p2": pagina_doc(1), "p1": pagina_doc(2)},
        {"b": item_doc("B", ordem=2, pagina="p2"), "a": item_doc("A", ordem=1, pagina="p2"), "c": item_doc("C", ordem=1, pagina="p1")})
    assert [p["id"] for p in paginas] == ["p2", "p1"]                       # p2 tem ordem menor, vem primeiro
    assert [i["key"] for i in paginas[0]["itens"]] == ["a", "b"] and list(itens) == ["a", "b", "c"]


def test_empate_de_ordem_desempata_pela_chave():
    paginas, _ = cat.montar_catalogo({"p1": pagina_doc()}, {"z": item_doc(ordem=1), "m": item_doc(ordem=1), "a": item_doc(ordem=1)})
    assert [i["key"] for i in paginas[0]["itens"]] == ["a", "m", "z"]


def test_itens_ocultos_invalidos_ou_de_pagina_inexistente_ficam_de_fora():
    paginas, itens = cat.montar_catalogo(
        {"p1": pagina_doc()},
        {"ok": item_doc(), "oculto": item_doc(ativo=False), "orfao": item_doc(pagina="p4"), "ruim": item_doc(preco=0), "A/B": item_doc()})
    assert list(itens) == ["ok"] and [i["key"] for i in paginas[0]["itens"]] == ["ok"]


def test_limites_de_paginas_e_de_itens():
    paginas, itens = cat.montar_catalogo({f"p{n}": pagina_doc(n) for n in range(1, 10)}, {})
    assert [p["id"] for p in paginas] == ["p1", "p2", "p3", "p4", "p5"]       # só 5 botões cabem numa linha
    _, itens = cat.montar_catalogo({"p1": pagina_doc()}, {f"i{n:02d}": item_doc(ordem=n) for n in range(40)})
    assert len(itens) == cat.MAX_ITENS == 25 and list(itens)[0] == "i00"      # um menu do Discord aceita 25 opções


def test_catalogo_vazio_ou_sem_paginas_nao_quebra():
    assert cat.montar_catalogo({}, {}) == ([], {})
    assert cat.montar_catalogo({}, {"a": item_doc()}) == ([], {})
    paginas, itens = cat.montar_catalogo({"p1": pagina_doc(introducao="só texto")}, {})
    assert paginas[0]["descricao"] == "só texto" and itens == {}


def test_texto_da_pagina_nunca_passa_do_limite_do_discord():
    docs = {f"i{n}": item_doc(f"Item {n}", descricao="x" * 1400, ordem=n) for n in range(10)}
    paginas, _ = cat.montar_catalogo({"p1": pagina_doc(introducao="i" * 1500)}, docs)
    texto = paginas[0]["descricao"]
    assert len(texto) <= cat.MAX_TEXTO_PAGINA and texto.endswith("(texto cortado: página muito longa)")


def test_documentos_lixo_aleatorios_nunca_derrubam_a_montagem():
    rng = random.Random(11)
    valores = [None, True, 5, -3, 2.5, "x", "", "p1", [], {}, [1], {"a": 1}, "‮", 10 ** 30, float("inf")]
    campos = ["nome", "preco_centavos", "descricao", "pagina", "ordem", "ativo", "introducao", "lixo"]
    for _ in range(3000):
        itens = {rng.choice(["a", "b", "X", "p1", "", "ok-1"]): {c: rng.choice(valores) for c in rng.sample(campos, rng.randint(0, 8))} for _ in range(rng.randint(0, 6))}
        pags = {rng.choice(["p1", "p2", "p9", "x", ""]): {c: rng.choice(valores) for c in rng.sample(campos, rng.randint(0, 8))} for _ in range(rng.randint(0, 4))}
        paginas, resultado = cat.montar_catalogo(pags, itens)
        assert len(paginas) <= 5 and len(resultado) <= 25
        for p in paginas:
            assert len(p["descricao"]) <= cat.MAX_TEXTO_PAGINA
        for i in resultado.values():
            assert isinstance(i["preco"], int) and 1 <= i["preco"] <= cat.PRECO_MAX and 1 <= len(i["nome"]) <= 60


# ---------------------------------------------------------------- assinatura de preços
def test_assinatura_muda_com_preco_e_com_entrada_ou_saida_de_item_mas_nao_com_a_descricao():
    base = {"a": item_doc("A", 500), "b": item_doc("B", 700, ordem=2)}
    _, i0 = cat.montar_catalogo({"p1": pagina_doc()}, base)
    _, igual = cat.montar_catalogo({"p1": pagina_doc()}, {**base, "a": item_doc("A novo nome", 500, descricao="outra")})
    _, preco = cat.montar_catalogo({"p1": pagina_doc()}, {**base, "a": item_doc("A", 600)})
    _, menos = cat.montar_catalogo({"p1": pagina_doc()}, {"a": base["a"]})
    _, mais = cat.montar_catalogo({"p1": pagina_doc()}, {**base, "c": item_doc("C", 100, ordem=3)})
    _, oculto = cat.montar_catalogo({"p1": pagina_doc()}, {**base, "b": item_doc("B", 700, ativo=False)})
    a = cat.assinatura_precos
    assert a(i0) == a(igual)                                    # nome e descrição não mudam a assinatura
    assert a(menos) == a(oculto)                                # ocultar equivale a tirar da loja
    # preço, item a menos e item a mais mudam a assinatura, e cada mudança é diferente das outras
    assert len({a(i0), a(preco), a(menos), a(mais)}) == 4


# ---------------------------------------------------------------- cópia local
def test_cache_vai_e_volta_e_ignora_tipos_nao_serializaveis(tmp_path):
    caminho = str(tmp_path / "c.json")
    paginas, itens = {"p1": pagina_doc()}, {"a": item_doc("A", atualizado_em=datetime.now(timezone.utc))}
    cat.salvar_cache(paginas, itens, caminho)
    lidos = cat.carregar_cache(caminho)
    assert lidos[0] == paginas and "atualizado_em" not in lidos[1]["a"] and lidos[1]["a"]["nome"] == "A"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.json"]            # sem arquivo temporário sobrando


@pytest.mark.parametrize("conteudo", ["", "não é json", "[]", "{}", '{"paginas": 1}', '{"paginas": {}, "itens": null}'])
def test_cache_corrompido_e_ignorado(tmp_path, conteudo):
    arq = tmp_path / "c.json"
    arq.write_text(conteudo, encoding="utf-8")
    resultado = cat.carregar_cache(str(arq))
    assert resultado is None or isinstance(resultado[0], dict)
    assert cat.carregar_cache(str(tmp_path / "nao-existe.json")) is None


# ---------------------------------------------------------------- semear o Firebase
def test_semear_envia_o_padrao_uma_unica_vez(bd):
    cliente = _cliente(bd)
    assert cat.semear(cliente) is True
    assert len(bd.cols["loja_itens"]) == 12 and len(bd.cols["loja_paginas"]) == 3
    assert bd.cols["loja_itens"]["escolhidos"]["preco_centavos"] == 500 and bd.cols["loja_itens"]["escolhidos"]["atualizado_por"] == "sistema"
    assert cat.semear(cliente) is False                                        # já semeado


def test_apagar_tudo_de_proposito_nao_ressuscita_o_catalogo(bd):
    cliente = _cliente(bd)
    cat.semear(cliente)
    bd.cols["loja_itens"].clear(); bd.cols["loja_paginas"].clear()
    assert cat.semear(cliente) is False and not bd.cols["loja_itens"]


def test_nao_semeia_por_cima_de_dados_existentes_mas_marca_como_feito(bd):
    bd.cols["loja_itens"] = {"meu": item_doc("Meu", 999)}
    cliente = _cliente(bd)
    assert cat.semear(cliente) is False and list(bd.cols["loja_itens"]) == ["meu"]
    assert "catalogo_semeado" in bd.cols["loja_meta"]


def test_semear_forcado(bd):
    cliente = _cliente(bd)
    bd.cols["loja_itens"] = {"meu": item_doc("Meu", 999)}
    assert cat.semear(cliente, forcar=True) is True and len(bd.cols["loja_itens"]) == 13


def _cliente(bd):
    return bd  # o FakeFirestore já tem a interface do cliente


# ---------------------------------------------------------------- sincronização ao vivo
class Aplicador:
    def __init__(self):
        self.chamadas = []

    def __call__(self, paginas, itens, fonte):
        self.chamadas.append((paginas, itens, fonte))

    @property
    def itens(self):
        return self.chamadas[-1][1]


def _sync(bd, tmp_path, **kw):
    from conftest import ld
    ap = Aplicador()
    s = cat.CatalogoSync(ld.FirestoreRemoto(bd), ap, str(tmp_path / "cache.json"))
    s.iniciar(**kw)
    return s, ap


def test_primeira_partida_semeia_e_aplica_o_catalogo_do_firebase(bd, tmp_path):
    s, ap = _sync(bd, tmp_path)
    assert s.fonte == "firebase" and ap.chamadas[-1][2] == "firebase" and len(ap.itens) == 12
    assert (tmp_path / "cache.json").exists()                                  # guardou a cópia local


def test_editar_no_painel_muda_a_loja_ao_vivo(bd, tmp_path):
    s, ap = _sync(bd, tmp_path)
    docs = dict(bd.cols["loja_itens"]["escolhidos"])
    docs["preco_centavos"] = 750
    bd.collection("loja_itens").document("escolhidos").set(docs)               # o painel gravou
    assert ap.itens["escolhidos"]["preco"] == 750
    bd.collection("loja_itens").document("novo").set(item_doc("Brinde", 100, "p1", 99))
    assert ap.itens["novo"]["nome"] == "Brinde"
    bd.collection("loja_itens").document("mudanca").delete()
    assert "mudanca" not in ap.itens and len(ap.itens) == 12
    bd.collection("loja_itens").document("aviso").set({**bd.cols["loja_itens"]["aviso"], "ativo": False})
    assert "aviso" not in ap.itens


def test_documento_invalido_no_banco_e_ignorado_sem_derrubar_nada(bd, tmp_path):
    s, ap = _sync(bd, tmp_path)
    bd.collection("loja_itens").document("lixo").set({"nome": "", "preco_centavos": -5})
    bd.collection("loja_itens").document("Maiuscula").set(item_doc())
    assert len(ap.itens) == 12 and "lixo" not in ap.itens and s.ultimo_erro is None


def test_aplicar_com_erro_nao_mata_a_escuta(bd, tmp_path):
    from conftest import ld
    chamadas = {"n": 0}

    def aplicar(*a):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("falha de teste")
    s = cat.CatalogoSync(ld.FirestoreRemoto(bd), aplicar, str(tmp_path / "c.json"))
    s.iniciar(espera=0.2)
    bd.collection("loja_itens").document("x").set(item_doc("X"))
    assert chamadas["n"] >= 2                                                  # continuou recebendo depois do erro


def test_parar_cancela_a_escuta(bd, tmp_path):
    s, ap = _sync(bd, tmp_path)
    antes = len(ap.chamadas)
    s.parar()
    bd.collection("loja_itens").document("x").set(item_doc("X"))
    assert len(ap.chamadas) == antes and bd.escutas == []


def test_firebase_fora_do_ar_na_partida_usa_a_copia_local(bd, tmp_path):
    from conftest import ld
    cat.salvar_cache({"p1": pagina_doc()}, {"so-local": item_doc("Só local", 321)}, str(tmp_path / "cache.json"))
    bd.fora = True
    ap = Aplicador()
    s = cat.CatalogoSync(ld.FirestoreRemoto(bd), ap, str(tmp_path / "cache.json"))
    s.iniciar(espera=0.2)
    assert s.fonte == "cache" and ap.chamadas[-1][2] == "cache" and ap.itens["so-local"]["preco"] == 321 and s.ultimo_erro


def test_sem_firebase_e_sem_cache_usa_o_padrao_do_codigo(tmp_path):
    ap = Aplicador()
    s = cat.CatalogoSync(None, ap, str(tmp_path / "nao-existe.json"))
    s.iniciar()
    assert s.fonte == "padrao" and ap.chamadas[-1][2] == "padrao" and len(ap.itens) == 12


def test_sem_firebase_com_cache_usa_a_copia(tmp_path):
    cat.salvar_cache({"p1": pagina_doc()}, {"a": item_doc("A", 111)}, str(tmp_path / "c.json"))
    ap = Aplicador()
    s = cat.CatalogoSync(None, ap, str(tmp_path / "c.json"))
    s.iniciar()
    assert s.fonte == "cache" and list(ap.itens) == ["a"]


def test_firebase_que_nao_responde_a_tempo_cai_no_plano_b(tmp_path):
    class Mudo:                                   # aceita a escuta mas nunca entrega nada
        def collection(self, nome):
            class C:
                def stream(self_inner): return []
                def document(self_inner, i):
                    class D:
                        exists = False
                        def get(self_d): return self_d
                        def set(self_d, *a): pass
                    return D()
                def on_snapshot(self_inner, cb): return type("W", (), {"unsubscribe": lambda s: None})()
            return C()
    from conftest import ld
    ap = Aplicador()
    s = cat.CatalogoSync(ld.FirestoreRemoto(Mudo()), ap, str(tmp_path / "c.json"))
    s.iniciar(espera=0.2)
    assert s.fonte == "padrao" and "não respondeu" in (s.ultimo_erro or "")


def test_aplicar_catalogo_da_loja_troca_tudo_de_uma_vez():
    paginas, itens = cat.montar_catalogo({"p1": pagina_doc()}, {"a": item_doc("A", 123)})
    loja.aplicar_catalogo(paginas, itens, "firebase")
    assert list(loja.ITENS) == ["a", "indicacao"] and loja.CATALOGO_FONTE == "firebase" and loja.ASSINATURA == (("a", 123), ("indicacao", 0))
    assert loja.PAGINAS[0]["itens"][0]["preco"] == 123
