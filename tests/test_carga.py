"""Pico de uso: muita gente comprando ao mesmo tempo, cliques repetidos e volume de dados.

Os números medidos são impressos com `pytest -s tests/test_carga.py` (e ficam no relatório).
"""
import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

import loja
import loja_dados as ld
from conftest import FakeFirestore, comprar, drenar, guild_falso, interacao, interacao_staff_no_canal, membro, pedido_modelo, staff

pytestmark = pytest.mark.carga


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- muita gente comprando junto
def test_200_compras_simultaneas_ids_unicos_e_nada_se_perde(cog, bd, monkeypatch):
    monkeypatch.setattr(loja, "LOJA_MAX_ABERTAS", 0)
    N = 200

    async def pico():
        g = guild_falso(sleep=True)
        inicio = time.perf_counter()
        await asyncio.gather(*[comprar(cog, membro(1000 + i, f"Jogador {i}"), {"escolhidos": 1 + i % 3}, g, sleep=True) for i in range(N)])
        return g, time.perf_counter() - inicio

    g, dt = run(pico())
    drenar(cog.armazenamento)
    ids = sorted(int(c) for c in cog.armazenamento.dados["pedidos"])
    assert ids == list(range(1, N + 1))                                    # 1..200, sem buracos nem repetição
    assert len({c.name for c in g.canais}) == N                            # nomes de canal únicos
    assert len({p["canal_id"] for p in cog.armazenamento.dados["pedidos"].values()}) == N
    assert cog.armazenamento.dados["ultimo_id"] == N and len(bd.cols["loja_pedidos"]) == N
    assert json.load(open(cog.armazenamento.caminho))["ultimo_id"] == N    # o arquivo local ficou íntegro
    print(f"\n[carga] {N} compras simultâneas em {dt:.2f}s ({N / dt:.0f}/s)")
    assert dt < 20


def test_mesma_pessoa_clicando_20x_em_abas_diferentes_respeita_o_limite(cog):
    """Corrida real: o limite de compras abertas precisa valer mesmo com 20 pedidos chegando ao mesmo tempo."""
    user = membro(77, "Insistente")

    async def spam():
        g = guild_falso(sleep=True)
        await asyncio.gather(*[comprar(cog, user, {"escolhidos": 1}, g, sleep=True) for _ in range(20)])
        return g

    g = run(spam())
    assert len(cog.armazenamento.dados["pedidos"]) == loja.LOJA_MAX_ABERTAS == 3
    assert len(g.canais) == 3                                              # e só 3 canais foram criados no servidor


def test_varias_pessoas_cada_uma_no_seu_limite_ao_mesmo_tempo(cog):
    async def pico():
        g = guild_falso(sleep=True)
        await asyncio.gather(*[comprar(cog, membro(2000 + u), {"aviso": 1}, g, sleep=True) for u in range(10) for _ in range(6)])
        return g
    g = run(pico())
    por_usuario = {}
    for p in cog.armazenamento.dados["pedidos"].values():
        por_usuario[p["usuario_id"]] = por_usuario.get(p["usuario_id"], 0) + 1
    assert set(por_usuario.values()) == {3} and len(por_usuario) == 10 and len(g.canais) == 30


def test_50_cliques_simultaneos_em_marcar_como_pago_so_um_vence(cog):
    run(comprar(cog, membro(5), {"escolhidos": 1}))
    cid = cog.armazenamento.dados["pedidos"]["01"]["canal_id"]

    async def cliques():
        inters = [interacao_staff_no_canal(cid, staff(100 + i)) for i in range(50)]
        async def ceder(*a, **k):
            await asyncio.sleep(0)
        for i in inters:
            i.response.edit_message = AsyncMock(side_effect=ceder)
        await asyncio.gather(*[cog.mudar_status(i, "paga") for i in inters])
        return inters

    inters = run(cliques())
    vencedores = [i for i in inters if i.response.edit_message.await_count == 1]
    assert len(vencedores) == 1 and cog.armazenamento.dados["pedidos"]["01"]["status"] == "paga"
    assert sum(i.channel.send.await_count for i in inters) == 1            # o aviso no canal sai uma vez só


def test_mudar_status_de_300_pedidos_ao_mesmo_tempo(cog, monkeypatch):
    monkeypatch.setattr(loja, "LOJA_MAX_ABERTAS", 0)
    g = guild_falso()
    for i in range(300):
        run(comprar(cog, membro(3000 + i), {"escolhidos": 1}, g))

    async def todos():
        await asyncio.gather(*[cog.mudar_status(interacao_staff_no_canal(c.id), "paga") for c in g.canais])
    t0 = time.perf_counter(); run(todos()); dt = time.perf_counter() - t0
    drenar(cog.armazenamento)
    assert all(p["status"] == "paga" for p in cog.armazenamento.dados["pedidos"].values())
    print(f"\n[carga] 300 confirmações de pagamento simultâneas em {dt:.2f}s")


# ---------------------------------------------------------------- fila de gravação no Firebase
def test_fila_do_firebase_aguenta_2000_gravacoes_sem_perder_nenhuma(bd, caminho_local):
    bd.latencia = 0.0005                                                    # 0,5 ms por escrita, como uma rede boa
    a = ld.Armazenamento(caminho_local, ld.FirestoreRemoto(bd)); a.sincronizar_inicio()
    t0 = time.perf_counter()
    for i in range(1, 501):
        a.registrar(i, pedido_modelo(i))
    enfileirado = time.perf_counter() - t0
    drenar(a)
    total = time.perf_counter() - t0
    assert len(bd.cols["loja_pedidos"]) == 500 and bd.cols["loja_meta"]["estado"]["ultimo_id"] == 500
    assert a.sincronizador.pendentes == 0
    print(f"\n[carga] 500 registros: fila em {enfileirado:.2f}s (bloqueia o bot), tudo no Firebase em {total:.2f}s | {bd.escritas} escritas")


def test_firebase_instavel_durante_o_pico_nao_perde_dados(bd, caminho_local, monkeypatch):
    monkeypatch.setattr(ld, "INTERVALO_REENVIO", 0.2)
    a = ld.Armazenamento(caminho_local, ld.FirestoreRemoto(bd)); a.sincronizar_inicio()
    bd.falhas = 30                                                          # as primeiras 30 escritas falham
    for i in range(1, 101):
        a.registrar(i, pedido_modelo(i))
    drenar(a)
    for _ in range(100):
        if not a.sincronizador.falhos:
            break
        threading.Event().wait(0.1)
    assert len(bd.cols["loja_pedidos"]) == 100 and a.sincronizador.pendentes == 0


# ---------------------------------------------------------------- volume de dados
@pytest.mark.parametrize("n,limite_ms", [(1_000, 60), (5_000, 250)])
def test_custo_de_gravar_com_muitos_pedidos(arm, n, limite_ms):
    """Cada gravação reescreve o arquivo local inteiro: mede quanto isso custa conforme a loja cresce."""
    arm.dados["pedidos"] = {f"{i:02d}": pedido_modelo(i) for i in range(1, n + 1)}
    arm.dados["ultimo_id"] = n
    amostras = []
    for k in range(5):
        t0 = time.perf_counter()
        arm.atualizar("01", atualizado_por=f"v{k}")
        amostras.append((time.perf_counter() - t0) * 1000)
    ms = sorted(amostras)[len(amostras) // 2]
    tamanho = len(json.dumps(arm.dados)) / 1024
    print(f"\n[carga] {n} pedidos ({tamanho:.0f} KB): {ms:.1f} ms por gravação")
    assert ms < limite_ms


def test_startup_com_5000_pedidos_e_firebase(bd, caminho_local):
    bd.cols["loja_pedidos"] = {f"{i:02d}": pedido_modelo(i) for i in range(1, 5001)}
    bd.cols["loja_meta"] = {"estado": {"ultimo_id": 5000}}
    t0 = time.perf_counter()
    a = ld.Armazenamento(caminho_local, ld.FirestoreRemoto(bd)); a.sincronizar_inicio(); drenar(a)
    dt = time.perf_counter() - t0
    assert len(a.dados["pedidos"]) == 5000 and a.proximo_id() == 5001
    print(f"\n[carga] partida com 5000 pedidos: {dt:.2f}s")
    assert dt < 10
