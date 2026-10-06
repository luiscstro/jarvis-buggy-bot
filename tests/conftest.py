"""Base compartilhada dos testes: ambiente isolado, Firestore falso e objetos falsos do Discord.

IMPORTANTE: os testes normais NUNCA falam com o Firebase real nem leem o seu .env.
Os que falam (marcados `integration`) só rodam com RUN_INTEGRATION=1 e limpam tudo o que criam.
"""
import asyncio
import copy
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# ---- ambiente isolado: definido ANTES de importar o bot, que lê isto ao importar ----
BASE_PIX_TESTE = (
    "00020126330014BR.GOV.BCB.PIX0111123456789015204000053039865802BR"
    "5913Loja de Teste6009SAO PAULO62070503***6304"
)


def _crc16(texto: str) -> int:
    crc = 0xFFFF
    for byte in texto.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


PIX_TESTE = BASE_PIX_TESTE + f"{_crc16(BASE_PIX_TESTE):04X}"
os.environ["PIX_COPIA_COLA"] = PIX_TESTE
os.environ["LOJA_CATEGORIA_ID"] = ""
os.environ["LOJA_MAX_ABERTAS"] = "3"
os.environ["FIREBASE_CREDENTIALS_BASE64"] = ""
os.environ["FIREBASE_CREDENTIALS_FILE"] = str(RAIZ / "nao-existe" / "firebase.json")

import discord  # noqa: E402
import pytest  # noqa: E402

import loja  # noqa: E402
import loja_dados as ld  # noqa: E402


# =============================================================== Firestore falso
class _Doc:
    def __init__(self, id_, dados):
        self.id, self._d = id_, dados

    exists = property(lambda s: s._d is not None)

    def to_dict(self):
        return copy.deepcopy(self._d)


class _DocRef:
    def __init__(self, bd, col, id_):
        self.bd, self.col, self.id = bd, col, id_

    def set(self, dados):
        self.bd._checar("escrita")
        self.bd.escritas += 1
        if self.bd.latencia:
            time.sleep(self.bd.latencia)
        self.bd.cols.setdefault(self.col, {})[self.id] = copy.deepcopy(dados)

    def get(self):
        self.bd._checar("leitura")
        return _Doc(self.id, self.bd.cols.get(self.col, {}).get(self.id))

    def delete(self):
        self.bd._checar("escrita")
        self.bd.cols.get(self.col, {}).pop(self.id, None)


class _ColRef:
    def __init__(self, bd, nome):
        self.bd, self.nome = bd, nome

    def document(self, id_):
        return _DocRef(self.bd, self.nome, id_)

    def stream(self):
        self.bd._checar("leitura")
        return [_Doc(i, d) for i, d in self.bd.cols.get(self.nome, {}).items()]


class FakeFirestore:
    """Imita a parte do cliente do Firestore que o bot usa, com falhas e latência injetáveis."""

    def __init__(self):
        self.cols: dict = {}
        self.fora = False       # True: tudo falha (Firebase fora do ar)
        self.falhas = 0         # as próximas N escritas falham
        self.latencia = 0.0     # segundos por escrita
        self.escritas = 0

    def _checar(self, tipo):
        if self.fora or (tipo == "escrita" and self.falhas > 0):
            if tipo == "escrita" and not self.fora:
                self.falhas -= 1
            raise ConnectionError("Firebase fora do ar")

    def collection(self, nome):
        return _ColRef(self, nome)


# =============================================================== Discord falso
def membro(uid=100, nome="Jogador", username=None, cargos=()):
    m = MagicMock(spec=discord.Member)
    m.id = uid
    m.display_name = nome
    m.mention = f"<@{uid}>"
    username = username or f"{nome.lower().replace(' ', '')}#0"
    m.__str__ = lambda s: username
    roles = []
    for c in cargos:
        r = MagicMock()
        r.name = c
        roles.append(r)
    m.roles = roles
    return m


def staff(uid=1, nome="Staff"):
    return membro(uid, nome, "staff#0", cargos=("Moderação",))


def guild_falso(sleep=False):
    g = MagicMock()
    g.roles = []
    g.default_role = MagicMock(name="default_role")
    g.me = MagicMock(name="bot")
    g.get_role.return_value = None
    g.get_channel.return_value = None
    contador = {"n": 0}

    async def criar(nome, **kwargs):
        if sleep:
            await asyncio.sleep(0)  # força a intercalação que acontece de verdade na rede
        contador["n"] += 1
        canal = MagicMock()
        canal.id = 5000 + contador["n"]
        canal.name = nome
        canal.mention = f"<#{canal.id}>"
        canal.send = AsyncMock()
        canal.delete = AsyncMock()
        g.canais.append(canal)
        return canal

    g.canais = []
    g.create_text_channel = AsyncMock(side_effect=criar)
    return g


def interacao(user, guild, sleep=False):
    i = MagicMock()
    i.guild, i.user = guild, user

    async def adiar(*a, **k):
        if sleep:
            await asyncio.sleep(0)

    i.response.defer = AsyncMock(side_effect=adiar)
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.send_modal = AsyncMock()
    i.edit_original_response = AsyncMock()
    i.followup.send = AsyncMock()
    return i


async def comprar(cog, user, carrinho=None, guild=None, sleep=False):
    """Simula o jogador finalizando uma compra. Devolve (interação, canal criado ou None)."""
    guild = guild or guild_falso(sleep)
    view = loja.LojaView(cog, user)
    view.carrinho = dict(carrinho or {"escolhidos": 1})
    view.montar()
    inter = interacao(user, guild, sleep)
    antes = len(guild.canais)
    await cog.criar_compra(inter, view)
    return inter, (guild.canais[-1] if len(guild.canais) > antes else None)


def interacao_staff_no_canal(canal_id, user=None, embed=None):
    user = user or staff()
    i = MagicMock()
    i.user = user
    i.channel = MagicMock()
    i.channel.id = canal_id
    i.channel.send = AsyncMock()
    i.channel.delete = AsyncMock()
    i.message.embeds = [embed or discord.Embed(title="x").add_field(name="Status", value="y")]
    i.response.edit_message = AsyncMock()
    i.response.send_message = AsyncMock()
    return i


# =============================================================== fixtures
@pytest.fixture(autouse=True)
def sem_esperas(monkeypatch):
    """Tira as esperas do reenvio para os testes não demorarem."""
    monkeypatch.setattr(ld, "time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(loja.asyncio, "sleep", _sleep_rapido(loja.asyncio.sleep))


def _sleep_rapido(original):
    async def sleep(segundos, *a, **k):
        return await original(0 if segundos >= 1 else segundos, *a, **k)
    return sleep


@pytest.fixture
def bd():
    return FakeFirestore()


@pytest.fixture
def caminho_local(tmp_path):
    return str(tmp_path / "store_data.json")


@pytest.fixture
def arm(caminho_local):
    """Armazenamento só local (sem Firebase)."""
    return ld.Armazenamento(caminho_local)


@pytest.fixture
def arm_fb(bd, caminho_local):
    """Armazenamento com um Firestore falso."""
    a = ld.Armazenamento(caminho_local, ld.FirestoreRemoto(bd))
    a.sincronizar_inicio()
    return a


def drenar(arm):
    """Espera a fila de gravações no Firebase esvaziar."""
    arm.sincronizador.fila.join()


@pytest.fixture
def cog(arm_fb):
    c = loja.LojaCog(MagicMock(), ["Developer", "Moderação"], arm_fb)
    return c


@pytest.fixture
def cog_local(arm):
    return loja.LojaCog(MagicMock(), ["Developer", "Moderação"], arm)


def pedido_modelo(i, **extra):
    p = {
        "id": i, "usuario_id": 1000 + i, "usuario": f"u{i}#0", "nome": f"Jogador {i}", "canal_id": 9000 + i,
        "itens": {"escolhidos": 1}, "itens_detalhe": [{"nome": "Escolhidos dos Mares", "qtd": 1, "preco_centavos": 500}],
        "total_centavos": 500, "criado_em": ld.agora_iso(), "status": "aguardando_pagamento",
        "pago_em": None, "entregue_em": None, "fechado_em": None, "atualizado_por": None,
    }
    p.update(extra)
    return p


# =============================================================== marcadores opcionais
def _tem_navegador():
    import shutil
    chrome = os.environ.get("CHROME_PATH") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    return bool(shutil.which("node")) and (os.path.exists(chrome) or bool(shutil.which("google-chrome") or shutil.which("chromium")))


def pytest_collection_modifyitems(config, items):
    sem_integracao = pytest.mark.skip(reason="fala com o Firebase real: rode com RUN_INTEGRATION=1")
    sem_navegador = pytest.mark.skip(reason="precisa de Node e do Google Chrome")
    for item in items:
        if "integration" in item.keywords and os.environ.get("RUN_INTEGRATION") != "1":
            item.add_marker(sem_integracao)
        if "browser" in item.keywords and not _tem_navegador():
            item.add_marker(sem_navegador)
