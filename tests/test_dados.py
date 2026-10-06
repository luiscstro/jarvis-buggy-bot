"""Armazenamento: arquivo local, Firebase (falso), sincronização, falhas, pedidos antigos e época."""
import asyncio
import base64
import json
import threading

import pytest

import loja_dados as ld
from conftest import FakeFirestore, drenar, pedido_modelo


# ---------------------------------------------------------------- arquivo local
def test_registrar_atualizar_e_consultar(arm):
    assert arm.proximo_id() == 1
    arm.registrar(1, pedido_modelo(1))
    arm.registrar(2, pedido_modelo(2))
    assert arm.proximo_id() == 3
    arm.atualizar("01", status="paga")
    assert arm.dados["pedidos"]["01"]["status"] == "paga"
    assert arm.por_canal(9002)[0] == "02"
    assert arm.por_canal(123) is None


def test_todos_ordena_por_numero_e_nao_por_texto(arm):
    for i in (1, 2, 9, 10, 99, 100, 101):
        arm.registrar(i, pedido_modelo(i))
    assert [p["id"] for p in arm.todos()] == [101, 100, 99, 10, 9, 2, 1]


def test_persistencia_entre_reinicios(caminho_local):
    a = ld.Armazenamento(caminho_local)
    a.registrar(1, pedido_modelo(1))
    a.definir_dashboard(10, 20)
    b = ld.Armazenamento(caminho_local)
    assert b.dados["ultimo_id"] == 1 and b.dados["dashboard"] == {"canal_id": 10, "mensagem_id": 20}
    assert b.dados["pedidos"]["01"]["usuario_id"] == 1001


def test_gravacao_e_atomica_e_nao_deixa_lixo(arm, tmp_path):
    arm.registrar(1, pedido_modelo(1))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["store_data.json"]
    json.loads((tmp_path / "store_data.json").read_text(encoding="utf-8"))


def test_atualizado_em_muda_a_cada_alteracao(arm):
    arm.registrar(1, pedido_modelo(1, criado_em="2026-01-01T00:00:00+00:00"))
    assert arm.dados["pedidos"]["01"]["atualizado_em"] == "2026-01-01T00:00:00+00:00"
    arm.atualizar("01", status="paga")
    assert arm.dados["pedidos"]["01"]["atualizado_em"] > "2026-01-01"


def test_ids_de_nomes_com_caracteres_estranhos_nao_quebram_o_json(arm):
    arm.registrar(1, pedido_modelo(1, nome='Ana "A" \\ \n ‮ 🤡 </script>', usuario="x'; DROP--"))
    de_volta = ld.Armazenamento(arm.caminho)
    assert de_volta.dados["pedidos"]["01"]["nome"].startswith('Ana "A"')


# ---------------------------------------------------------------- pedidos de versões antigas
def test_completar_pedido_antigo():
    antigo = {"usuario_id": 7, "usuario": "u#0", "canal_id": 1, "itens": {}, "total_centavos": 500}
    assert ld.completar_pedido(antigo, "07") is True
    assert antigo["id"] == 7 and antigo["nome"] == "u#0" and antigo["status"] == "aguardando_pagamento"
    assert ld.completar_pedido(antigo, "07") is False          # idempotente


def test_completar_nao_sobrescreve_campos_existentes():
    p = pedido_modelo(3, status="entregue", nome="Fulano")
    assert ld.completar_pedido(p, "03") is False
    assert p["status"] == "entregue" and p["nome"] == "Fulano"


def test_antigos_so_locais_sao_completados_e_salvos(caminho_local):
    antigo = {"usuario_id": 1, "usuario": "u#0", "canal_id": 1, "itens": {}, "total_centavos": 500}
    json.dump({"ultimo_id": 1, "dashboard": None, "pedidos": {"01": antigo}}, open(caminho_local, "w"))
    a = ld.Armazenamento(caminho_local)
    a.completar_locais()
    assert json.load(open(caminho_local))["pedidos"]["01"]["status"] == "aguardando_pagamento"


# ---------------------------------------------------------------- Firebase: gravação
def test_grava_pedido_e_meta_no_firebase(arm_fb, bd):
    arm_fb.registrar(1, pedido_modelo(1))
    arm_fb.atualizar("01", status="paga")
    arm_fb.definir_dashboard(5, 6)
    drenar(arm_fb)
    assert bd.cols["loja_pedidos"]["01"]["status"] == "paga"
    meta = bd.cols["loja_meta"]["estado"]
    assert meta["ultimo_id"] == 1 and meta["dashboard"] == {"canal_id": 5, "mensagem_id": 6}


def test_o_que_vai_para_a_fila_nao_muda_depois(arm_fb, bd):
    p = pedido_modelo(1)
    arm_fb.registrar(1, p)
    arm_fb.atualizar("01", status="paga")
    drenar(arm_fb)
    assert bd.cols["loja_pedidos"]["01"]["status"] == "paga"


def test_ultima_gravacao_vence_na_ordem(arm_fb, bd):
    arm_fb.registrar(1, pedido_modelo(1))
    for n in range(200):
        arm_fb.atualizar("01", atualizado_por=f"v{n}")
    drenar(arm_fb)
    assert bd.cols["loja_pedidos"]["01"]["atualizado_por"] == "v199"


def test_retenta_apos_falhas_e_se_recupera(arm_fb, bd):
    bd.falhas = 2
    arm_fb.registrar(1, pedido_modelo(1))
    drenar(arm_fb)
    assert "01" in bd.cols["loja_pedidos"] and arm_fb.sincronizador.ultimo_erro is None


def test_firebase_fora_do_ar_nao_perde_a_compra_e_reenvia_depois(arm_fb, bd, monkeypatch):
    monkeypatch.setattr(ld, "INTERVALO_REENVIO", 0.2)
    bd.fora = True
    arm_fb.registrar(1, pedido_modelo(1))
    arm_fb.atualizar("01", status="paga")
    drenar(arm_fb)
    assert "01" in arm_fb.dados["pedidos"] and not bd.cols.get("loja_pedidos")
    assert arm_fb.sincronizador.pendentes >= 1 and arm_fb.sincronizador.ultimo_erro
    bd.fora = False
    for _ in range(60):
        threading.Event().wait(0.1)
        if not arm_fb.sincronizador.falhos:
            break
    assert bd.cols["loja_pedidos"]["01"]["status"] == "paga"
    assert arm_fb.sincronizador.pendentes == 0


# ---------------------------------------------------------------- Firebase: partida e conflitos
def _local(caminho, pedidos, ultimo_id=0, **extra):
    json.dump({"ultimo_id": ultimo_id, "dashboard": None, "pedidos": pedidos, **extra}, open(caminho, "w"))


def _abrir(bd, caminho):
    a = ld.Armazenamento(caminho, ld.FirestoreRemoto(bd))
    a.sincronizar_inicio()
    drenar(a)
    return a


def test_perder_o_arquivo_local_recupera_tudo_do_firebase(bd, tmp_path):
    a = _abrir(bd, str(tmp_path / "a.json"))
    a.registrar(1, pedido_modelo(1)); a.registrar(2, pedido_modelo(2))
    a.atualizar("01", status="paga")
    a.definir_dashboard(7, 8)
    drenar(a)
    b = _abrir(bd, str(tmp_path / "vazio.json"))
    assert len(b.dados["pedidos"]) == 2 and b.proximo_id() == 3
    assert b.dados["pedidos"]["01"]["status"] == "paga" and b.dados["dashboard"]["mensagem_id"] == 8


def test_conflito_vale_o_mais_recente_de_cada_pedido(bd, tmp_path):
    velho, novo = "2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"
    bd.cols = {"loja_pedidos": {"01": pedido_modelo(1, status="entregue", atualizado_em=novo),
                                "02": pedido_modelo(2, status="aguardando_pagamento", atualizado_em=velho)},
               "loja_meta": {"estado": {"ultimo_id": 2, "dashboard": None}}}
    c = str(tmp_path / "l.json")
    _local(c, {"01": pedido_modelo(1, status="paga", atualizado_em=velho),
               "02": pedido_modelo(2, status="paga", atualizado_em=novo),
               "03": pedido_modelo(3, atualizado_em=novo)}, ultimo_id=3)
    a = _abrir(bd, c)
    assert a.dados["pedidos"]["01"]["status"] == "entregue"
    assert a.dados["pedidos"]["02"]["status"] == "paga" and bd.cols["loja_pedidos"]["02"]["status"] == "paga"
    assert "03" in bd.cols["loja_pedidos"] and a.dados["ultimo_id"] == 3


def test_ultimo_id_nunca_regride(bd, tmp_path):
    bd.cols = {"loja_pedidos": {"05": pedido_modelo(5)}, "loja_meta": {"estado": {"ultimo_id": 2}}}
    a = _abrir(bd, str(tmp_path / "x.json"))
    assert a.dados["ultimo_id"] == 5 and a.proximo_id() == 6


def test_compra_feita_com_firebase_fora_sobe_na_proxima_partida(bd, tmp_path):
    c = str(tmp_path / "q.json")
    a = _abrir(bd, c)
    bd.fora = True
    a.registrar(1, pedido_modelo(1)); drenar(a)
    bd.fora = False
    _abrir(bd, c)
    assert "01" in bd.cols["loja_pedidos"] and bd.cols["loja_meta"]["estado"]["ultimo_id"] == 1


def test_firebase_inacessivel_na_partida_segue_com_o_arquivo_local(bd, tmp_path):
    c = str(tmp_path / "o.json")
    _local(c, {"01": pedido_modelo(1)}, ultimo_id=1)
    bd.fora = True
    a = ld.Armazenamento(c, ld.FirestoreRemoto(bd))
    a.sincronizar_inicio()
    assert a.status()["firebase_ok_na_partida"] is False and a.status()["pedidos"] == 1


def test_antigos_no_firebase_sao_completados_e_corrigidos_la_tambem(bd, tmp_path):
    antigo = lambda i: {"usuario_id": 100 + i, "usuario": f"u{i}#0", "canal_id": 900 + i, "itens": {"escolhidos": 1}, "total_centavos": 500}
    bd.cols = {"loja_pedidos": {"01": antigo(1), "02": antigo(2)}, "loja_meta": {"estado": {"ultimo_id": 2}}}
    a = _abrir(bd, str(tmp_path / "n.json"))
    assert a.dados["pedidos"]["02"]["status"] == "aguardando_pagamento"
    assert bd.cols["loja_pedidos"]["02"]["status"] == "aguardando_pagamento" and bd.cols["loja_pedidos"]["02"]["id"] == 2


# ---------------------------------------------------------------- época (banco zerado de propósito)
def test_banco_zerado_descarta_o_arquivo_local_antigo(bd, tmp_path):
    """Sem isto, o store_data.json antigo do servidor ressuscitaria os pedidos apagados."""
    c = str(tmp_path / "servidor.json")
    _local(c, {"01": pedido_modelo(1), "02": pedido_modelo(2)}, ultimo_id=2)          # arquivo do servidor (sem época)
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "dashboard": None, "epoca": "nova-1"}}}   # banco zerado
    a = _abrir(bd, c)
    assert a.dados["pedidos"] == {} and a.proximo_id() == 1 and a.dados["epoca"] == "nova-1"
    assert not bd.cols.get("loja_pedidos")                                            # nada ressuscitou
    assert json.load(open(c))["pedidos"] == {}                                        # e o arquivo local foi limpo


def test_mesma_epoca_continua_mesclando_normalmente(bd, tmp_path):
    c = str(tmp_path / "m.json")
    _local(c, {"01": pedido_modelo(1)}, ultimo_id=1, epoca="e1")
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = _abrir(bd, c)
    assert "01" in bd.cols["loja_pedidos"] and a.dados["epoca"] == "e1"


def test_epoca_e_enviada_junto_com_o_meta(bd, tmp_path):
    c = str(tmp_path / "e.json")
    _local(c, {}, epoca="e9")
    bd.cols = {"loja_meta": {"estado": {"epoca": "e9"}}}
    a = _abrir(bd, c)
    a.registrar(1, pedido_modelo(1)); drenar(a)
    assert bd.cols["loja_meta"]["estado"]["epoca"] == "e9"


def test_epoca_ignora_banco_sem_epoca(bd, tmp_path):
    c = str(tmp_path / "s.json")
    _local(c, {"01": pedido_modelo(1)}, ultimo_id=1, epoca="minha")
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0}}}       # banco sem época: não descarta nada
    a = _abrir(bd, c)
    assert "01" in a.dados["pedidos"] and a.dados["epoca"] == "minha"


def test_adotar_firebase_mantem_pedidos_do_banco_e_completa_antigos(bd, tmp_path):
    bd.cols = {"loja_pedidos": {"04": {"usuario_id": 1, "usuario": "x#0", "canal_id": 1, "itens": {}, "total_centavos": 500}},
               "loja_meta": {"estado": {"ultimo_id": 1, "epoca": "z", "dashboard": {"canal_id": 1, "mensagem_id": 2}}}}
    a = _abrir(bd, str(tmp_path / "z.json"))
    assert a.dados["ultimo_id"] == 4 and a.dados["pedidos"]["04"]["status"] == "aguardando_pagamento"
    assert a.dados["dashboard"] == {"canal_id": 1, "mensagem_id": 2}


# ---------------------------------------------------------------- criação e credenciais
def test_criar_sem_credenciais_usa_so_o_arquivo_local(caminho_local):
    a = asyncio.run(ld.Armazenamento.criar(caminho_local))
    assert a.remoto is None and a.status()["firebase"] is False


def test_credenciais_ausentes_lixo_e_incompletas_nao_derrubam_o_bot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FIREBASE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("FIREBASE_CREDENTIALS_BASE64", "")
    assert ld.carregar_credenciais() is None and ld.FirestoreRemoto.conectar() is None

    monkeypatch.setenv("FIREBASE_CREDENTIALS_BASE64", "isto-nao-e-base64!!")
    assert ld.FirestoreRemoto.conectar() is None

    monkeypatch.setenv("FIREBASE_CREDENTIALS_BASE64", base64.b64encode(b"[1,2,3]").decode())
    assert ld.FirestoreRemoto.conectar() is None

    monkeypatch.setenv("FIREBASE_CREDENTIALS_BASE64", base64.b64encode(json.dumps({"project_id": "x"}).encode()).decode())
    assert ld.carregar_credenciais() == {"project_id": "x"} and ld.FirestoreRemoto.conectar() is None


def test_credenciais_por_arquivo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FIREBASE_CREDENTIALS_BASE64", "")
    monkeypatch.delenv("FIREBASE_CREDENTIALS_FILE", raising=False)
    (tmp_path / "firebase-credentials.json").write_text('{"project_id": "p"}', encoding="utf-8")
    assert ld.carregar_credenciais() == {"project_id": "p"}


def test_status_reporta_o_estado(arm_fb, bd):
    s = arm_fb.status()
    assert s["firebase"] is True and s["firebase_ok_na_partida"] is True and s["pendentes"] == 0
    bd.fora = True
    arm_fb.registrar(1, pedido_modelo(1)); drenar(arm_fb)
    assert arm_fb.status()["ultimo_erro"] and arm_fb.status()["pendentes"] >= 1
