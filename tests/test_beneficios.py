"""Benefícios mensais das assinaturas: 1 resgate por mês, à prova de burla, de queda do bot e de erros."""
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import beneficios as ben
import loja
import loja_dados as ld
from conftest import ligar_registro, guild_falso, interacao, interacao_staff_no_canal, membro, staff

CARGO1, CARGO2 = ben.CARGO_ASSINANTE_1_ID, ben.CARGO_ASSINANTE_2_ID
OUTUBRO = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
NOVEMBRO = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


def assinante(uid=500, nivel=1, nome=None):
    return membro(uid, nome or f"Assinante {uid}", cargo_ids=[CARGO1 if nivel == 1 else CARGO2])


@pytest.fixture
def ambiente(cog, registro):
    g = guild_falso()
    g.get_member = lambda uid: None
    cog.bot.get_guild = lambda gid: g if gid == g.id else None
    return cog, registro, g


def abrir(cog, user, guild, quando=OUTUBRO):
    inter = interacao(user, guild)
    inter.created_at = quando
    run(cog.abrir_beneficios(inter))
    return inter


def view_do(inter):
    return inter.followup.send.call_args.kwargs["view"]


async def _resgatar(cog, user, guild, escolhas, quando=OUTUBRO, sleep=False, nivel=None):
    nivel = nivel or ben.nivel_do_membro([r.id for r in user.roles])
    view = ben.BeneficioView(cog, user, nivel, ben.ciclo_de(quando))
    view.escolhas = list(escolhas)
    inter = interacao(user, guild, sleep)
    inter.created_at = quando
    await cog.resgatar_beneficio(inter, view)
    return inter


def resgatar(cog, user, guild, escolhas, quando=OUTUBRO):
    return run(_resgatar(cog, user, guild, escolhas, quando))


def texto_final(inter):
    return inter.edit_original_response.call_args.kwargs["content"]


def doc_beneficio(bd, user_id=500, ciclo="2026-10"):
    return bd.cols.get("loja_beneficios", {}).get(ben.chave_beneficio(ciclo, user_id))


# ================================================================= funções puras
@pytest.mark.parametrize("dt,esperado", [
    (datetime(2026, 10, 1, 0, 0, tzinfo=ben.FUSO_BRASILIA), "2026-10"),
    (datetime(2026, 10, 31, 23, 59, 59, tzinfo=ben.FUSO_BRASILIA), "2026-10"),
    (datetime(2026, 11, 1, 0, 0, 0, tzinfo=ben.FUSO_BRASILIA), "2026-11"),
    (datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc), "2026-10"),        # 23:30 de 31/10 em Brasília
    (datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc), "2026-11"),         # 00:00 de 01/11 em Brasília
    (datetime(2026, 12, 31, 23, 59, tzinfo=ben.FUSO_BRASILIA), "2026-12"),
    (datetime(2027, 1, 1, 0, 0, tzinfo=ben.FUSO_BRASILIA), "2027-01"),
    (datetime(2026, 10, 16, 15, 0), "2026-10"),                            # sem fuso: tratado como UTC
])
def test_mes_do_beneficio_segue_brasilia(dt, esperado):
    assert ben.ciclo_de(dt) == esperado


def test_nomes_e_proximo_mes():
    assert ben.nome_do_ciclo("2026-03") == "março de 2026" and ben.nome_do_ciclo("2026-12") == "dezembro de 2026"
    assert ben.inicio_do_proximo_ciclo("2026-10") == datetime(2026, 11, 1, tzinfo=ben.FUSO_BRASILIA)
    assert ben.inicio_do_proximo_ciclo("2026-12") == datetime(2027, 1, 1, tzinfo=ben.FUSO_BRASILIA)


def test_chave_do_beneficio_e_por_jogador_e_mes():
    assert ben.chave_beneficio("2026-10", 123) == "2026-10-123"
    assert len({ben.chave_beneficio(c, u) for c in ("2026-10", "2026-11") for u in (1, 2)}) == 4


def test_nivel_pelo_cargo():
    assert ben.nivel_do_membro([]) is None and ben.nivel_do_membro([1, 2, 3]) is None
    assert ben.nivel_do_membro([CARGO1]) == 1 and ben.nivel_do_membro([CARGO2]) == 2
    assert ben.nivel_do_membro([CARGO1, CARGO2, 5]) == 2                       # tendo os dois, vale o maior
    assert ben.nivel_do_membro([0]) is None                                    # cargo "0" (não configurado) nunca casa


def test_beneficios_de_cada_nivel_conferem_com_o_combinado():
    n1, n2 = ben.NIVEIS[1], ben.NIVEIS[2]
    assert [(k, q) for k, _, q in n1["fixos"]] == [("escolhidos", 3), ("sorte", 1)] and n1["escolhas"] == 1
    assert [(k, q) for k, _, q in n2["fixos"]] == [("escolhidos", 6), ("sorte", 2)] and n2["escolhas"] == 2
    assert ben.TETO_VIP_CENTAVOS == 1500


def test_opcoes_de_escolha_respeitam_o_teto_e_a_ordem():
    itens = loja.ITENS
    opcoes = ben.opcoes_de_escolha(itens)
    assert all(o["preco"] <= 1500 for o in opcoes)
    assert [o["preco"] for o in opcoes] == sorted(o["preco"] for o in opcoes)
    assert {"renascimento", "sorte"} <= {o["key"] for o in opcoes}              # R$ 15,00 exato entra
    assert not {"lunariano", "supertesouro"} & {o["key"] for o in opcoes}      # R$ 20,00 não


def test_teto_exato_e_um_centavo_acima():
    itens = {"a": {"key": "a", "nome": "A", "preco": 1500}, "b": {"key": "b", "nome": "B", "preco": 1501}}
    assert ben.validar_escolhas(1, ["a"], itens)[0] is True
    assert ben.validar_escolhas(1, ["b"], itens)[0] is False


@pytest.mark.parametrize("nivel,escolhas", [
    (1, []), (1, ["escolhidos", "escolhidos"]), (2, ["escolhidos"]), (2, []), (1, [None]), (1, [5]), (1, [""]), (1, ["nao-existe"]),
    (1, ["lunariano"]), (2, ["escolhidos", "supertesouro"]), (1, ["../x"]), (1, [["escolhidos"]]),
])
def test_escolhas_invalidas_sao_recusadas(nivel, escolhas):
    assert ben.validar_escolhas(nivel, escolhas, loja.ITENS)[0] is False


def test_escolhas_validas_incluindo_repetir_o_mesmo_vip():
    assert ben.validar_escolhas(1, ["sorte"], loja.ITENS) == (True, "")
    assert ben.validar_escolhas(2, ["escolhidos", "escolhidos"], loja.ITENS)[0] is True
    assert ben.validar_escolhas(2, ["renascimento", "mudanca"], loja.ITENS)[0] is True


def test_linhas_do_beneficio_tem_fixos_e_escolhas_a_preco_zero():
    linhas = ben.itens_do_beneficio(2, ["renascimento", "renascimento"], loja.ITENS)
    assert [(l["nome"], l["qtd"]) for l in linhas] == [("Escolhidos dos Mares", 6), ("Sorte Grande", 2), ("Renascimento (à escolha)", 2)]
    assert all(l["preco_centavos"] == 0 for l in linhas) and {l["origem"] for l in linhas} == {"assinatura", "escolha"}
    assert [l["qtd"] for l in ben.itens_do_beneficio(1, ["sorte"], loja.ITENS)] == [3, 1, 1]


def test_se_o_item_fixo_sumiu_do_catalogo_usa_o_nome_de_reserva():
    linhas = ben.itens_do_beneficio(1, ["mudanca"], {"mudanca": {"key": "mudanca", "nome": "Mudança Pequena", "preco": 500}})
    assert [l["nome"] for l in linhas][:2] == ["Escolhido dos Mares", "Sorte Grande"]


def test_codigo_do_registro_e_estavel_e_diferencia_epocas():
    a = ben.codigo_registro(1, "2026-10-01T10:00:00+00:00", 5)
    assert a == ben.codigo_registro(1, "2026-10-01T10:00:00+00:00", 5) and len(a) == 6
    assert a != ben.codigo_registro(1, "2026-11-01T10:00:00+00:00", 5) and a != ben.codigo_registro(2, "2026-10-01T10:00:00+00:00", 5)


# ================================================================= o botão do painel
def test_painel_tem_o_botao_de_beneficios_persistente(cog):
    v = loja.PainelLojaView(cog)
    assert v.timeout is None and [c.custom_id for c in v.children] == ["loja:abrir", "loja:beneficios"]
    assert [c.label for c in v.children] == ["Abrir loja", "Benefícios do mês"]


def test_botao_chama_o_fluxo_de_beneficios(ambiente):
    cog, _, g = ambiente
    v = loja.PainelLojaView(cog)
    inter = interacao(membro(1), g)
    run(v.beneficios.callback(inter))
    inter.response.send_message.assert_awaited_once()                         # quem não é assinante recebe a explicação


# ================================================================= abrir a tela
def test_quem_nao_e_assinante_ve_quem_tem_direito(ambiente):
    cog, _, g = ambiente
    inter = abrir(cog, membro(1, "Comum"), g)
    e = inter.response.send_message.call_args.kwargs["embed"]
    assert inter.response.send_message.call_args.kwargs["ephemeral"] is True
    assert f"<@&{CARGO1}>" in str(e.fields[0].value) and f"<@&{CARGO2}>" in str(e.fields[1].value)
    assert "3x Escolhido dos Mares" in e.fields[0].value and "6x Escolhido dos Mares" in e.fields[1].value


def test_nivel_1_pede_um_vip_e_nivel_2_pede_dois(ambiente):
    cog, _, g = ambiente
    v1 = view_do(abrir(cog, assinante(1, 1), g))
    assert len(v1.escolhas) == 1 and len([c for c in v1.children if isinstance(c, ben.EscolhaSelect)]) == 1
    v2 = view_do(abrir(cog, assinante(2, 2), g))
    assert len(v2.escolhas) == 2 and len([c for c in v2.children if isinstance(c, ben.EscolhaSelect)]) == 2
    assert next(c for c in v2.children if isinstance(c, ben.ConfirmarBeneficioButton)).disabled        # só libera depois de escolher


def test_quem_tem_os_dois_cargos_recebe_o_nivel_maior(ambiente):
    cog, _, g = ambiente
    user = membro(3, "Dois", cargo_ids=[CARGO1, CARGO2])
    assert view_do(abrir(cog, user, g)).nivel == 2


def test_menu_so_oferece_itens_ate_15_reais(ambiente):
    cog, _, g = ambiente
    menu = next(c for c in view_do(abrir(cog, assinante(), g)).children if isinstance(c, ben.EscolhaSelect))
    rotulos = {o.label: o.description for o in menu.options}
    assert "Renascimento" in rotulos and rotulos["Renascimento"] == "R$ 15,00" and "Re-roll de Lunariano" not in rotulos


def test_so_o_dono_da_tela_pode_usa_la(ambiente):
    cog, _, g = ambiente
    v = view_do(abrir(cog, assinante(), g))
    intruso = interacao(membro(999, "Intruso"), g)
    assert run(v.interaction_check(intruso)) is False
    assert "outra pessoa" in intruso.response.send_message.call_args.args[0]
    assert run(v.interaction_check(interacao(v.usuario, g))) is True


def test_escolher_no_menu_libera_o_botao(ambiente):
    cog, _, g = ambiente
    v = view_do(abrir(cog, assinante(), g))
    menu = next(c for c in v.children if isinstance(c, ben.EscolhaSelect))
    menu._values = ["sorte"]
    run(menu.callback(interacao(v.usuario, g)))
    assert v.escolhas == ["sorte"] and not next(c for c in v.children if isinstance(c, ben.ConfirmarBeneficioButton)).disabled


def test_menu_forjado_com_item_fora_da_regra_e_rejeitado(ambiente):
    cog, _, g = ambiente
    v = view_do(abrir(cog, assinante(), g))
    menu = next(c for c in v.children if isinstance(c, ben.EscolhaSelect))
    menu._values = ["lunariano"]                                               # R$ 20,00: não existe nas opções
    inter = interacao(v.usuario, g)
    run(menu.callback(inter))
    assert v.escolhas == [None] and "não está mais disponível" in inter.response.edit_message.call_args.kwargs["content"]


def test_sem_firebase_nao_deixa_resgatar(cog_local):
    g = guild_falso()
    inter = abrir(cog_local, assinante(), g)
    assert "banco de dados online" in inter.response.send_message.call_args.args[0] and "suporte" in inter.response.send_message.call_args.args[0]


def test_fora_do_servidor_nao_funciona(cog):
    inter = interacao(MagicMock(spec=discord.User), guild_falso())
    run(cog.abrir_beneficios(inter))
    assert "dentro do servidor" in inter.response.send_message.call_args.args[0]


def test_erro_inesperado_ao_abrir_mostra_codigo_e_manda_acionar_o_suporte(ambiente, monkeypatch):
    cog, _, g = ambiente
    monkeypatch.setattr(cog.armazenamento.remoto, "ler_beneficio", MagicMock(side_effect=RuntimeError("firebase caiu")))
    inter = abrir(cog, assinante(), g)
    texto = inter.followup.send.call_args.args[0]
    assert "Código:" in texto and "acione o suporte" in texto and "firebase caiu" not in texto      # sem vazar detalhes internos


# ================================================================= resgatar: o caminho feliz
def test_resgate_nivel_1_cria_ticket_pedido_anuncio_e_trava(ambiente, bd):
    cog, registro, g = ambiente
    user = assinante(500, 1, "Ana Assinante")
    inter = resgatar(cog, user, g, ["renascimento"])
    assert "outubro de 2026" in texto_final(inter) and "compra **#01**" in texto_final(inter) and len(g.canais) == 1
    canal = g.canais[0]
    assert canal.name == "ana-assinante-01" and canal.topic.startswith("Benefício 2026-10-500 · compra 01")

    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert ped["tipo"] == "beneficio" and ped["status"] == "paga" and ped["total_centavos"] == 0 and ped["canal_id"] == canal.id
    assert ped["beneficio_chave"] == "2026-10-500" and ped["aprovado_por_id"] is None and "Automático" in ped["aprovado_por_nome"]
    assert [(d["nome"], d["qtd"]) for d in ped["itens_detalhe"]] == [("Escolhidos dos Mares", 3), ("Sorte Grande", 1), ("Renascimento (à escolha)", 1)]
    assert ped["itens"] == {"escolhidos": 3, "sorte": 1, "renascimento": 1}

    ticket = canal.send.call_args.kwargs["embeds"][0]
    assert "outubro de 2026" in ticket.title and "**3x** Escolhidos dos Mares" in ticket.description and "Renascimento (à escolha)" in ticket.description
    assert doc_beneficio(bd)["estado"] == "concluido" and doc_beneficio(bd)["compra_id"] == 1 and doc_beneficio(bd)["canal_id"] == canal.id

    post = registro.mensagens[0].embeds[0]
    assert post.title == "🎁 Benefício mensal #01 resgatado"
    c = {f.name: f.value for f in post.fields}
    assert "Automático" in c["✅ Aprovado por"] and c["💵 Valor"] == "Incluído na assinatura" and f"<@&{CARGO1}>" in c["🏷️ Cargo"]
    assert "🎰 Gachas" not in c                                                 # benefício não dá gacha


def test_resgate_nivel_2_traz_o_dobro_e_dois_vips(ambiente):
    cog, _, g = ambiente
    resgatar(cog, assinante(501, 2), g, ["escolhidos", "renascimento"])
    ped = cog.armazenamento.dados["pedidos"]["01"]
    assert [(d["nome"], d["qtd"]) for d in ped["itens_detalhe"]] == [
        ("Escolhidos dos Mares", 6), ("Sorte Grande", 2), ("Escolhidos dos Mares (à escolha)", 1), ("Renascimento (à escolha)", 1)]
    assert ped["itens"] == {"escolhidos": 7, "sorte": 2, "renascimento": 1} and ped["nivel"] == 2


def test_nivel_2_pode_escolher_o_mesmo_vip_duas_vezes(ambiente):
    cog, _, g = ambiente
    resgatar(cog, assinante(502, 2), g, ["sorte", "sorte"])
    assert cog.armazenamento.dados["pedidos"]["01"]["itens"]["sorte"] == 4        # 2 fixos + 2 escolhidos


def test_staff_entrega_o_beneficio_e_o_post_publico_e_atualizado(ambiente):
    cog, registro, g = ambiente
    resgatar(cog, assinante(), g, ["sorte"])
    canal = g.canais[0]
    i = interacao_staff_no_canal(canal.id, staff(12, "Beto"))
    run(cog.mudar_status(i, "entregue"))
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "entregue"
    assert "Entregue por <@12>" in {f.name: f.value for f in registro.mensagens[0].embeds[0].fields}["📦 Entrega"] and len(registro.mensagens) == 1
    i2 = interacao_staff_no_canal(canal.id, staff(12, "Beto"))
    run(cog.mudar_status(i2, "paga"))                                           # já nasce paga: apertar "pago" só avisa
    assert "já está" in i2.response.send_message.call_args.args[0]


# ================================================================= 1 vez por mês (a regra central)
def test_segundo_resgate_no_mesmo_mes_e_recusado(ambiente):
    cog, registro, g = ambiente
    user = assinante()
    resgatar(cog, user, g, ["sorte"])
    inter = resgatar(cog, user, g, ["renascimento"])
    assert "já resgatou" in texto_final(inter) and len(g.canais) == 1 and len(cog.armazenamento.dados["pedidos"]) == 1 and len(registro.mensagens) == 1


def test_a_tela_de_quem_ja_resgatou_mostra_quando_libera(ambiente):
    cog, _, g = ambiente
    user = assinante()
    resgatar(cog, user, g, ["sorte"])
    e = abrir(cog, user, g).followup.send.call_args.kwargs["embed"]
    assert "já resgatou" in e.title and "outubro de 2026" in e.description and "<t:" in e.description
    assert str(g.canais[0].id) in e.fields[0].value


def test_no_mes_seguinte_pode_resgatar_de_novo(ambiente):
    cog, _, g = ambiente
    user = assinante()
    resgatar(cog, user, g, ["sorte"], OUTUBRO)
    inter = resgatar(cog, user, g, ["mudanca"], NOVEMBRO)
    assert "novembro de 2026" in texto_final(inter) and len(g.canais) == 2
    assert {p["ciclo"] for p in cog.armazenamento.dados["pedidos"].values()} == {"2026-10", "2026-11"}


def test_virada_do_mes_no_fuso_de_brasilia(ambiente):
    cog, _, g = ambiente
    user = assinante()
    ultimo_minuto = datetime(2026, 11, 1, 2, 59, tzinfo=timezone.utc)           # 23:59 de 31/10 em Brasília
    primeiro = datetime(2026, 11, 1, 3, 1, tzinfo=timezone.utc)                 # 00:01 de 01/11
    assert "outubro de 2026" in texto_final(resgatar(cog, user, g, ["sorte"], ultimo_minuto))
    assert "já resgatou" in texto_final(resgatar(cog, user, g, ["sorte"], ultimo_minuto + timedelta(seconds=30)))
    assert "novembro de 2026" in texto_final(resgatar(cog, user, g, ["sorte"], primeiro))


def test_jogadores_diferentes_sao_independentes(ambiente):
    cog, _, g = ambiente
    resgatar(cog, assinante(1), g, ["sorte"]); resgatar(cog, assinante(2, 2), g, ["sorte", "sorte"]); resgatar(cog, assinante(3), g, ["mudanca"])
    assert len(g.canais) == 3 and len(cog.armazenamento.dados["pedidos"]) == 3


def test_subir_de_nivel_no_meio_do_mes_nao_libera_outro_resgate(ambiente):
    cog, _, g = ambiente
    user = assinante(7, 1)
    resgatar(cog, user, g, ["sorte"])
    user.roles = assinante(7, 2).roles
    assert "já resgatou" in texto_final(resgatar(cog, user, g, ["sorte", "sorte"])) and len(g.canais) == 1


def test_perder_e_recuperar_o_cargo_no_mes_nao_permite_resgatar_de_novo(ambiente):
    cog, _, g = ambiente
    user = assinante(8)
    resgatar(cog, user, g, ["sorte"])
    roles = user.roles; user.roles = []
    inter = run(_resgatar(cog, user, g, ["sorte"], nivel=1))                   # a tela ainda é a do nível 1, mas o cargo já era
    assert "já resgatou" in texto_final(inter) or "não tem mais o cargo" in texto_final(inter)
    assert len(g.canais) == 1
    user.roles = roles
    assert "já resgatou" in texto_final(resgatar(cog, user, g, ["sorte"])) and len(g.canais) == 1


def test_muitos_cliques_ao_mesmo_tempo_do_mesmo_jogador_resgatam_uma_vez(ambiente):
    cog, registro, g = ambiente
    user = assinante(9)

    async def cliques():
        return await asyncio.gather(*[_resgatar(cog, user, g, ["sorte"], sleep=True) for _ in range(30)])
    run(cliques())
    assert len(g.canais) == 1 and len(cog.armazenamento.dados["pedidos"]) == 1 and len(registro.mensagens) == 1
    assert len(cog.armazenamento.dados["pedidos"]) == 1


def test_duas_copias_do_bot_ao_mesmo_tempo_resgatam_uma_vez_so(ambiente, bd, tmp_path):
    """A trava está no servidor (create atômico), não na memória de um bot: vale mesmo com 2 instâncias rodando."""
    cog_a, registro, g = ambiente
    arm_b = ld.Armazenamento(str(tmp_path / "b.json"), ld.FirestoreRemoto(bd)); arm_b.sincronizar_inicio()
    cog_b = loja.LojaCog(cog_a.bot, ["Developer", "Moderação"], arm_b)
    user = assinante(10)

    async def corrida():
        await asyncio.gather(*[_resgatar(c, user, g, ["sorte"], sleep=True) for c in (cog_a, cog_b) * 8])
    run(corrida())
    assert len(g.canais) == 1 and len(bd.cols["loja_beneficios"]) == 1
    assert len(cog_a.armazenamento.dados["pedidos"]) + len(arm_b.dados["pedidos"]) == 1


def test_reiniciar_o_bot_e_apagar_o_arquivo_local_nao_libera_outro_resgate(ambiente, bd, tmp_path):
    cog, _, g = ambiente
    user = assinante(11)
    resgatar(cog, user, g, ["sorte"])
    novo = ld.Armazenamento(str(tmp_path / "zero.json"), ld.FirestoreRemoto(bd)); novo.sincronizar_inicio()
    cog2 = loja.LojaCog(cog.bot, ["Developer", "Moderação"], novo)
    assert "já resgatou" in texto_final(resgatar(cog2, user, g, ["sorte"])) and len(g.canais) == 1
    zerado = ld.Armazenamento(str(tmp_path / "zero2.json"), ld.FirestoreRemoto(bd))          # banco local vazio, sem sincronizar
    assert "já resgatou" in texto_final(resgatar(loja.LojaCog(cog.bot, ["Developer"], zerado), user, g, ["sorte"]))


def test_zerar_os_pedidos_nao_devolve_o_direito_ao_mes(ambiente, bd):
    """A trava mora em loja_beneficios, que a limpeza do banco de pedidos não toca."""
    cog, _, g = ambiente
    user = assinante(12)
    resgatar(cog, user, g, ["sorte"])
    bd.cols["loja_pedidos"].clear(); cog.armazenamento.dados["pedidos"].clear()
    assert "já resgatou" in texto_final(resgatar(cog, user, g, ["sorte"]))


# ================================================================= segurança do resgate
def test_perdeu_o_cargo_entre_abrir_a_tela_e_confirmar(ambiente, bd):
    cog, _, g = ambiente
    user = assinante(20)
    v = view_do(abrir(cog, user, g)); v.escolhas = ["sorte"]
    user.roles = []                                                              # saiu da assinatura
    inter = interacao(user, g)
    run(cog.resgatar_beneficio(inter, v))
    assert "não tem mais o cargo" in texto_final(inter) and g.canais == [] and not bd.cols.get("loja_beneficios")


@pytest.mark.parametrize("escolhas", [[None], [], ["lunariano"], ["supertesouro"], ["nao-existe"], ["sorte", "sorte"], ["../../x"]])
def test_escolhas_adulteradas_nao_resgatam_nada(ambiente, bd, escolhas):
    cog, _, g = ambiente
    inter = resgatar(cog, assinante(21), g, escolhas)
    assert "⚠️" in texto_final(inter) and g.canais == [] and not bd.cols.get("loja_beneficios") and not cog.armazenamento.dados["pedidos"]


def test_item_escolhido_que_saiu_do_catalogo_e_recusado(ambiente):
    cog, _, g = ambiente
    user = assinante(22)
    v = view_do(abrir(cog, user, g)); v.escolhas = ["mudanca"]
    import catalogo
    paginas, itens = catalogo.catalogo_padrao()
    itens.pop("mudanca")
    loja.aplicar_catalogo(paginas, itens, "firebase")                            # a staff tirou o item enquanto a pessoa decidia
    inter = interacao(user, g); run(cog.resgatar_beneficio(inter, v))
    assert "não está mais disponível" in texto_final(inter) and g.canais == []


def test_preco_do_item_subiu_acima_do_teto_enquanto_escolhia(ambiente):
    cog, _, g = ambiente
    user = assinante(23)
    v = view_do(abrir(cog, user, g)); v.escolhas = ["mudanca"]
    import catalogo
    paginas, itens = catalogo.catalogo_padrao()
    itens["mudanca"] = {**itens["mudanca"], "preco": 2500}
    loja.aplicar_catalogo(paginas, itens, "firebase")
    inter = interacao(user, g); run(cog.resgatar_beneficio(inter, v))
    assert "não está mais disponível" in texto_final(inter) and g.canais == []


def test_banco_fora_do_ar_recusa_o_resgate_sem_criar_nada(ambiente, bd):
    """Falha fechada: sem poder garantir o '1 por mês', não entrega nada."""
    cog, _, g = ambiente
    user = assinante(24)
    bd.fora = True
    inter = resgatar(cog, user, g, ["sorte"])
    assert "Código:" in texto_final(inter) and "não foi feito" in texto_final(inter) and g.canais == []
    bd.fora = False
    assert "compra **#01**" in texto_final(resgatar(cog, user, g, ["sorte"])) and len(g.canais) == 1     # depois, normal


def test_sem_firebase_configurado_recusa(cog_local):
    g = guild_falso()
    inter = resgatar(cog_local, assinante(25), g, ["sorte"])
    assert "não está disponível" in texto_final(inter) and "suporte" in texto_final(inter) and g.canais == []


def test_botao_cancelar_nao_resgata_nada(ambiente, bd):
    cog, _, g = ambiente
    v = view_do(abrir(cog, assinante(26), g))
    inter = interacao(v.usuario, g)
    cancelar = next(c for c in v.children if isinstance(c, ben.CancelarBeneficioButton))
    run(cancelar.callback(inter))
    assert "cancelado" in inter.response.edit_message.call_args.kwargs["content"] and not bd.cols.get("loja_beneficios")


# ================================================================= erros: o jogador sempre recebe o caminho do suporte
def test_falha_ao_criar_o_canal_devolve_a_vez_e_orienta(ambiente, bd):
    cog, _, g = ambiente
    user = assinante(30)
    original = g.create_text_channel.side_effect
    g.create_text_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403, reason="x"), "sem permissão"))
    inter = resgatar(cog, user, g, ["sorte"])
    t = texto_final(inter)
    assert "Código:" in t and "acione o suporte" in t and "tentar de novo" in t
    assert not bd.cols.get("loja_beneficios") and not cog.armazenamento.dados["pedidos"]           # vez devolvida: nada ficou preso
    g.create_text_channel = AsyncMock(side_effect=original)
    assert "compra **#01**" in texto_final(resgatar(cog, user, g, ["sorte"]))                       # e a nova tentativa funciona


def test_nao_vaza_detalhes_internos_nas_mensagens_de_erro(ambiente):
    cog, _, g = ambiente
    g.create_text_channel = AsyncMock(side_effect=RuntimeError("SEGREDO: senha=123 /home/bot/.env"))
    t = texto_final(resgatar(cog, assinante(31), g, ["sorte"]))
    assert "SEGREDO" not in t and ".env" not in t and "Código:" in t


# ================================================================= queda do bot: continua de onde parou
def doc_reservado(user, ciclo="2026-10", guild_id=4242, minutos=10, nivel=1, escolhas=("sorte",)):
    agora = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()
    return {
        "chave": ben.chave_beneficio(ciclo, user.id), "usuario_id": user.id, "usuario": str(user), "nome": user.display_name,
        "guild_id": guild_id, "ciclo": ciclo, "nivel": nivel, "cargo_id": ben.NIVEIS[nivel]["cargo_id"], "escolhas": list(escolhas),
        "itens": ben.itens_do_beneficio(nivel, list(escolhas), loja.ITENS), "estado": "reservado", "compra_id": None, "canal_id": None,
        "tentativas": 0, "criado_em": agora, "atualizado_em": agora,
    }


def reservar_a_mao(bd, doc):
    bd.cols.setdefault("loja_beneficios", {})[doc["chave"]] = doc


def test_bot_caiu_logo_apos_reservar_e_ao_voltar_termina_o_resgate(ambiente, bd):
    cog, registro, g = ambiente
    user = assinante(40)
    g.get_member = lambda uid: user if uid == 40 else None
    reservar_a_mao(bd, doc_reservado(user))
    assert g.canais == [] and not cog.armazenamento.dados["pedidos"]
    assert run(cog.retomar_beneficios()) == 1
    assert len(g.canais) == 1 and len(cog.armazenamento.dados["pedidos"]) == 1 and len(registro.mensagens) == 1
    assert doc_beneficio(bd, 40)["estado"] == "concluido" and g.canais[0].send.await_count == 1
    assert run(cog.retomar_beneficios()) == 0 and len(g.canais) == 1 and len(registro.mensagens) == 1        # repetir não duplica nada


def test_a_retomada_entrega_o_que_o_jogador_tinha_escolhido(ambiente, bd):
    cog, _, g = ambiente
    user = assinante(41, 2)
    g.get_member = lambda uid: user
    reservar_a_mao(bd, doc_reservado(user, nivel=2, escolhas=("renascimento", "mudanca")))
    run(cog.retomar_beneficios())
    nomes = [d["nome"] for d in cog.armazenamento.dados["pedidos"]["01"]["itens_detalhe"]]
    assert nomes[:2] == ["Escolhidos dos Mares", "Sorte Grande"]
    assert sorted(nomes[2:]) == ["Mudança Pequena (à escolha)", "Renascimento (à escolha)"]


def test_queda_depois_de_criar_o_canal_e_antes_de_registrar_nao_duplica_o_canal(ambiente, bd, monkeypatch):
    cog, registro, g = ambiente
    user = assinante(42)
    g.get_member = lambda uid: user
    monkeypatch.setattr(ben, "IDADE_MINIMA_RECUPERACAO", 0)
    original = cog.armazenamento.registrar
    estado = {"n": 0}

    def uma_falha(*a, **k):
        estado["n"] += 1
        if estado["n"] == 1:
            raise RuntimeError("o bot caiu aqui")
        return original(*a, **k)
    cog.armazenamento.registrar = uma_falha
    inter = resgatar(cog, user, g, ["sorte"])
    t = texto_final(inter)
    assert "foi registrado" in t and "Não tente de novo" in t and "Código:" in t
    assert len(g.canais) == 1 and not cog.armazenamento.dados["pedidos"] and doc_beneficio(bd, 42)["estado"] == "reservado"
    assert run(cog.retomar_beneficios()) == 1                                    # a retomada reaproveita o canal que já existe
    assert len(g.canais) == 1 and len(cog.armazenamento.dados["pedidos"]) == 1 and len(registro.mensagens) == 1
    assert cog.armazenamento.dados["pedidos"]["01"]["canal_id"] == g.canais[0].id and doc_beneficio(bd, 42)["estado"] == "concluido"


def test_queda_depois_do_pedido_e_antes_do_ticket_e_do_anuncio(ambiente, bd):
    cog, registro, g = ambiente
    user = assinante(43)
    g.get_member = lambda uid: user
    doc = doc_reservado(user)
    reservar_a_mao(bd, doc)
    canal = run(g.create_text_channel("x-01", topic=f"Benefício {doc['chave']} · compra 01 — teste"))
    cog.armazenamento.registrar(1, {"id": 1, "tipo": "beneficio", "beneficio_chave": doc["chave"], "ciclo": "2026-10", "nivel": 1, "cargo_id": CARGO1,
                                    "usuario_id": 43, "usuario": "u", "nome": "U", "canal_id": canal.id, "itens": {}, "itens_detalhe": doc["itens"], "total_centavos": 0,
                                    "criado_em": ben.agora_iso(), "status": "paga", "pago_em": ben.agora_iso(), "aprovado_em": ben.agora_iso(), "ticket_enviado": False})
    assert run(cog.retomar_beneficios()) == 1
    assert canal.send.await_count == 1 and len(registro.mensagens) == 1 and len(cog.armazenamento.dados["pedidos"]) == 1
    assert doc_beneficio(bd, 43)["estado"] == "concluido"


def test_a_retomada_nao_atropela_um_resgate_em_andamento(ambiente, bd):
    cog, _, g = ambiente
    user = assinante(44)
    g.get_member = lambda uid: user
    reservar_a_mao(bd, doc_reservado(user, minutos=0))                           # recém-criado: provavelmente ainda em andamento
    assert run(cog.retomar_beneficios()) == 0 and g.canais == []
    reservar_a_mao(bd, doc_reservado(assinante(45), minutos=10))
    cog._em_andamento.add(ben.chave_beneficio("2026-10", 45))                    # marcado como em andamento neste bot
    assert run(cog.retomar_beneficios()) == 0 and g.canais == []


def test_retomada_ignora_o_que_ja_terminou_e_trata_membro_que_saiu(ambiente, bd):
    cog, _, g = ambiente
    concluido = doc_reservado(assinante(46)); concluido["estado"] = "concluido"
    reservar_a_mao(bd, concluido)
    saiu = doc_reservado(assinante(47))
    reservar_a_mao(bd, saiu)
    g.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "saiu"))
    assert run(cog.retomar_beneficios()) == 0 and g.canais == []
    assert doc_beneficio(bd, 47)["estado"] == "membro_ausente"                   # continua bloqueando o mês; não cria canal para quem saiu


def test_retomada_em_servidor_desconhecido_nao_quebra(ambiente, bd):
    cog, _, g = ambiente
    reservar_a_mao(bd, doc_reservado(assinante(48), guild_id=99999))
    assert run(cog.retomar_beneficios()) == 0 and g.canais == []


def test_depois_de_muitas_falhas_marca_como_falhou_sem_loop_infinito(ambiente, bd, monkeypatch):
    cog, _, g = ambiente
    user = assinante(49)
    g.get_member = lambda uid: user
    monkeypatch.setattr(ben, "IDADE_MINIMA_RECUPERACAO", 0)
    g.create_text_channel = AsyncMock(side_effect=RuntimeError("sem permissão"))
    reservar_a_mao(bd, doc_reservado(user))
    for _ in range(ben.MAX_TENTATIVAS_RESGATE + 3):
        run(cog.retomar_beneficios())
    d = doc_beneficio(bd, 49)
    assert d["estado"] == "falhou" and d["tentativas"] == ben.MAX_TENTATIVAS_RESGATE
    assert g.create_text_channel.await_count == ben.MAX_TENTATIVAS_RESGATE                          # parou de tentar


def test_manutencao_roda_as_duas_retomadas_e_nunca_levanta_erro(ambiente, bd, monkeypatch):
    cog, registro, g = ambiente
    user = assinante(50)
    g.get_member = lambda uid: user
    reservar_a_mao(bd, doc_reservado(user))
    assert run(cog.manutencao()) == (1, 0)
    monkeypatch.setattr(cog, "retomar_beneficios", AsyncMock(side_effect=RuntimeError("explodiu")))
    assert run(cog.manutencao())[0] == 0                                         # um erro numa retomada não impede a outra


def test_rotina_de_retomada_inicia_uma_vez_e_para(cog):
    async def cenario():
        cog._iniciar_manutencao(); primeira = cog._tarefa_manutencao
        cog._iniciar_manutencao()
        igual = primeira is cog._tarefa_manutencao
        cog._parar_manutencao()
        return igual, cog._tarefa_manutencao
    igual, depois = run(cenario())
    assert igual is True and depois is None


# ================================================================= o ticket do benefício
def test_ticket_do_beneficio_tem_plano_b_quando_o_discord_recusa_embeds(ambiente):
    cog, registro, g = ambiente
    original = g.create_text_channel.side_effect

    async def criar_e_recusar_embeds(nome, **kw):
        canal = await original(nome, **kw)
        async def enviar(**k):
            if k.get("embeds"):
                raise discord.Forbidden(MagicMock(status=403, reason="x"), "Missing Permissions")
        canal.send = AsyncMock(side_effect=enviar)
        return canal
    g.create_text_channel = AsyncMock(side_effect=criar_e_recusar_embeds)
    inter = resgatar(cog, assinante(60), g, ["sorte"])
    assert "compra **#01**" in texto_final(inter)
    ultimo = g.canais[0].send.call_args.kwargs
    assert "embeds" not in ultimo and "Escolhidos dos Mares" in ultimo["content"]


def test_so_comprador_bot_e_staff_veem_o_canal_do_beneficio(ambiente):
    cog, _, g = ambiente
    user = assinante(61)
    cargo = MagicMock(); cargo.name = "Moderação"; cargo.id = 77; cargo.mention = "<@&77>"
    g.roles = [cargo]
    resgatar(cog, user, g, ["sorte"])
    ow = g.create_text_channel.call_args.kwargs["overwrites"]
    assert ow[g.default_role].view_channel is False and ow[user].view_channel is True and ow[cargo].manage_messages is True
    assert set(ow) == {g.default_role, user, g.me, cargo}


def test_benefico_nao_conta_nos_limites_de_compras_abertas(ambiente):
    cog, _, g = ambiente
    resgatar(cog, assinante(62), g, ["sorte"])
    assert all(p.get("status") != "aguardando_pagamento" for p in cog.armazenamento.dados["pedidos"].values())
