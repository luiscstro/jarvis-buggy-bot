"""Zerar o banco precisa "pegar" mesmo com um bot desatualizado rodando (incidente real: pedido apagado ressuscitou).

O que aconteceu: o banco foi zerado (nova época), mas o bot do servidor ainda não tinha reiniciado e guardava a época
antiga e os pedidos antigos. Na compra seguinte ele gravou o estado e SOBRESCREVEU a época nova; no reinício seguinte o
arquivo local antigo mesclou de volta o pedido apagado.
"""
import json

import pytest

import loja_dados as ld
from conftest import drenar, pedido_modelo


def zerar_como_a_ferramenta(bd, epoca):
    """O que tools/limpar_banco.py faz: apaga os pedidos e grava o estado zerado com uma época nova."""
    for chave in list(bd.cols.get("loja_pedidos", {})):
        bd.collection("loja_pedidos").document(chave).delete()
    bd.collection("loja_meta").document("estado").set({"ultimo_id": 0, "dashboard": None, "epoca": epoca, "atualizado_em": ld.agora_iso()})


def abrir(bd, caminho, vigiar=False):
    a = ld.Armazenamento(caminho, ld.FirestoreRemoto(bd))
    a.sincronizar_inicio()
    if vigiar:
        a.vigiar_epoca()
    drenar(a)
    return a


# ---------------------------------------------------------------- o bot nunca escreve a época
def test_o_estado_gravado_pelo_bot_nao_leva_a_epoca(bd, tmp_path):
    a = abrir(bd, str(tmp_path / "a.json"))
    a.registrar(1, pedido_modelo(1)); drenar(a)
    assert "epoca" not in bd.cols["loja_meta"]["estado"] and bd.cols["loja_meta"]["estado"]["ultimo_id"] == 1


def test_epoca_do_banco_e_preservada_nas_gravacoes_do_bot(bd, tmp_path):
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e9"}}}
    a = abrir(bd, str(tmp_path / "a.json"))
    a.registrar(1, pedido_modelo(1)); a.definir_dashboard(5, 6); drenar(a)
    assert bd.cols["loja_meta"]["estado"]["epoca"] == "e9" and bd.cols["loja_meta"]["estado"]["dashboard"] == {"canal_id": 5, "mensagem_id": 6}


def test_reproduz_o_incidente_bot_desatualizado_nao_desfaz_o_reset(bd, tmp_path):
    local = str(tmp_path / "servidor.json")
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    velho = abrir(bd, local)                                   # o bot do servidor, na época e1 (sem vigia: é o código antigo)
    velho.registrar(1, pedido_modelo(1, nome="teste antigo")); drenar(velho)

    zerar_como_a_ferramenta(bd, "e2")                          # a staff zera o banco
    assert not bd.cols.get("loja_pedidos")

    velho.registrar(2, pedido_modelo(2, nome="compra nova")); drenar(velho)       # ...e o bot que NÃO reiniciou recebe uma compra
    assert bd.cols["loja_meta"]["estado"]["epoca"] == "e2"                        # antes: virava None e a proteção caía
    assert bd.cols["loja_meta"]["estado"]["ultimo_id"] == 2

    reiniciado = abrir(bd, local)                              # o bot reinicia com o arquivo local antigo (tem o 01 e o 02)
    assert set(reiniciado.dados["pedidos"]) == {"02"}          # só a compra nova; o teste antigo NÃO ressuscitou
    assert set(bd.cols["loja_pedidos"]) == {"02"} and reiniciado.proximo_id() == 3
    assert json.load(open(local))["pedidos"].keys() == {"02"}  # e o arquivo local foi limpo


# ---------------------------------------------------------------- o bot percebe o reset ao vivo
def test_reset_e_percebido_ao_vivo_sem_reiniciar(bd, tmp_path):
    local = str(tmp_path / "a.json")
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, local, vigiar=True)
    a.registrar(1, pedido_modelo(1)); a.registrar(2, pedido_modelo(2)); drenar(a)
    assert len(a.dados["pedidos"]) == 2 and a.proximo_id() == 3

    zerar_como_a_ferramenta(bd, "e2")                          # zerado com o bot rodando
    assert a.dados["pedidos"] == {} and a.proximo_id() == 1 and a.dados["epoca"] == "e2"
    assert json.load(open(local))["pedidos"] == {}

    a.registrar(1, pedido_modelo(1, nome="nova")); drenar(a)   # a primeira compra depois do reset é a 01 e nada antigo volta
    assert set(bd.cols["loja_pedidos"]) == {"01"} and bd.cols["loja_pedidos"]["01"]["nome"] == "nova"
    assert bd.cols["loja_meta"]["estado"]["epoca"] == "e2"


def test_gravacoes_pendentes_antigas_nao_ressuscitam_depois_do_reset(bd, tmp_path):
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    bd.fora = True                                             # o Firebase cai e três compras ficam só no bot
    for i in (1, 2, 3):
        a.registrar(i, pedido_modelo(i))
    drenar(a)
    assert a.sincronizador.pendentes >= 3
    bd.fora = False
    zerar_como_a_ferramenta(bd, "e2")                          # volta e é zerado antes de o bot reenviar
    assert a.sincronizador.pendentes == 0 and a.dados["pedidos"] == {}
    a.registrar(1, pedido_modelo(1, nome="nova")); drenar(a)   # uma gravação nova faria o bot reenviar o que sobrou na fila
    assert set(bd.cols["loja_pedidos"]) == {"01"} and bd.cols["loja_pedidos"]["01"]["nome"] == "nova"


def test_o_proprio_trafego_do_bot_nao_dispara_reset(bd, tmp_path):
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    for i in range(1, 6):
        a.registrar(i, pedido_modelo(i)); a.atualizar(f"{i:02d}", status="paga")
    drenar(a)
    assert len(a.dados["pedidos"]) == 5 and a.dados["epoca"] == "e1"


def test_mudancas_sem_nova_epoca_sao_ignoradas(bd, tmp_path):
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    a.registrar(1, pedido_modelo(1)); drenar(a)
    bd.collection("loja_meta").document("estado").set({"ultimo_id": 5}, merge=True)        # mexe no contador, não na época
    assert set(a.dados["pedidos"]) == {"01"}
    bd.collection("loja_meta").document("estado").delete()                                  # estado apagado: não é um reset
    assert set(a.dados["pedidos"]) == {"01"}


def test_parar_a_vigia_cancela_a_escuta(bd, tmp_path):
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    a.registrar(1, pedido_modelo(1)); drenar(a)
    a.parar_vigia()
    assert bd.escutas_doc == []
    zerar_como_a_ferramenta(bd, "e2")
    assert set(a.dados["pedidos"]) == {"01"}                                                # sem vigia, não percebe


def test_falha_ao_ler_o_banco_durante_o_reset_tenta_de_novo_e_acaba_adotando(bd, tmp_path):
    import threading
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    a.atraso_retentativa = 0.05                                 # na vida real são 15 s, 30 s...
    a.registrar(1, pedido_modelo(1)); drenar(a)
    bd.cols["loja_pedidos"].clear()                             # a ferramenta apagou os pedidos...
    bd.fora = True                                              # ...mas o Firebase falha justo na hora de o bot ler
    bd.cols["loja_meta"]["estado"] = {"ultimo_id": 0, "epoca": "e2"}
    bd._notificar_doc("loja_meta", "estado")
    assert set(a.dados["pedidos"]) == {"01"}                    # a primeira tentativa falhou: continua de pé com o que tinha
    bd.fora = False                                             # o Firebase volta
    for _ in range(60):
        if a.dados.get("epoca") == "e2":
            break
        threading.Event().wait(0.05)
    assert a.dados["pedidos"] == {} and a.dados["epoca"] == "e2"


def test_desiste_depois_de_cinco_tentativas_sem_derrubar_o_bot(bd, tmp_path):
    import threading
    bd.cols = {"loja_meta": {"estado": {"ultimo_id": 0, "epoca": "e1"}}}
    a = abrir(bd, str(tmp_path / "a.json"), vigiar=True)
    a.atraso_retentativa = 0.01
    a.registrar(1, pedido_modelo(1)); drenar(a)
    bd.fora = True
    bd.cols["loja_meta"]["estado"] = {"ultimo_id": 0, "epoca": "e2"}
    bd._notificar_doc("loja_meta", "estado")
    threading.Event().wait(0.6)                                 # tempo de sobra para as 5 tentativas
    assert set(a.dados["pedidos"]) == {"01"} and a.dados["epoca"] == "e1"     # desistiu, mas o bot segue funcionando
    a.registrar(2, pedido_modelo(2))                           # (e a próxima reinicialização resolve de vez)
    assert "02" in a.dados["pedidos"]
