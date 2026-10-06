"""Armazenamento dos pedidos da loja.

Os dados ficam em três lugares:
  - memória: todas as leituras do bot são feitas daqui (rápido, sem rede);
  - Firebase Firestore: cópia durável, sobrevive a redeploys da hospedagem;
  - store_data.json: cópia local, usada se o Firebase não estiver configurado
    ou estiver fora do ar.

Gravar no Firebase nunca trava nem derruba uma compra: as gravações vão para uma
fila processada por uma thread em segundo plano, com novas tentativas. O que falhar
de vez fica guardado e é reenviado a cada 5 minutos (ou na próxima gravação que der certo).
Ao iniciar, o bot junta o que está no Firebase com o arquivo local (vale o registro
mais recente de cada pedido), então nada se perde se um dos dois estiver defasado.

Credenciais (service account do Firebase), em ordem de prioridade:
  1. FIREBASE_CREDENTIALS_BASE64  — conteúdo do .json em base64, numa linha do .env
  2. FIREBASE_CREDENTIALS_FILE    — caminho do .json (padrão: firebase-credentials.json)
"""
import base64
import copy
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import asyncio

COLECAO_PEDIDOS = "loja_pedidos"
COLECAO_META = "loja_meta"
COLECAO_STAFF = "loja_staff"  # e-mails autorizados a abrir o painel web (id do documento = e-mail)
DOC_META = "estado"
TENTATIVAS = 4
INTERVALO_REENVIO = 300  # segundos entre reenvios do que falhou


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =============================================
#  FIREBASE
# =============================================
def carregar_credenciais() -> Optional[dict]:
    """Lê a service account do .env (base64) ou de um arquivo .json. None se não houver."""
    codificado = (os.getenv("FIREBASE_CREDENTIALS_BASE64") or "").strip()
    if codificado:
        return json.loads(base64.b64decode(codificado))
    caminho = os.getenv("FIREBASE_CREDENTIALS_FILE") or "firebase-credentials.json"
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


class FirestoreRemoto:
    """Camada fina sobre o cliente do Firestore (injetável nos testes)."""

    def __init__(self, cliente):
        self.cliente = cliente

    @classmethod
    def conectar(cls) -> Optional["FirestoreRemoto"]:
        """Cria o cliente ou devolve None (com um aviso claro) se não der."""
        try:
            info = carregar_credenciais()
        except (ValueError, OSError) as e:
            print(f"[Loja] Credenciais do Firebase ilegíveis ({type(e).__name__}). Usando só o arquivo local.")
            return None
        if not info:
            caminho = os.getenv("FIREBASE_CREDENTIALS_FILE") or "firebase-credentials.json"
            print(
                "[Loja] Firebase não configurado: não achei FIREBASE_CREDENTIALS_BASE64 no ambiente "
                f"nem o arquivo '{caminho}' em '{os.getcwd()}'. "
                "Os pedidos ficam só no arquivo local (store_data.json)."
            )
            return None
        try:
            from google.cloud import firestore
            from google.oauth2 import service_account

            credenciais = service_account.Credentials.from_service_account_info(info)
            return cls(firestore.Client(project=info["project_id"], credentials=credenciais))
        except Exception as e:  # biblioteca ausente, JSON sem os campos certos etc.
            print(f"[Loja] Não consegui iniciar o Firebase ({type(e).__name__}: {e}). Usando só o arquivo local.")
            return None

    def carregar(self) -> tuple[dict, dict[str, dict]]:
        """Devolve (meta, pedidos) como estão no Firestore."""
        doc = self.cliente.collection(COLECAO_META).document(DOC_META).get()
        meta = doc.to_dict() if doc.exists else {}
        pedidos = {d.id: d.to_dict() for d in self.cliente.collection(COLECAO_PEDIDOS).stream()}
        return meta or {}, pedidos

    # ---------- acesso ao painel web ----------
    def listar_staff(self) -> list[str]:
        return sorted(d.id for d in self.cliente.collection(COLECAO_STAFF).stream())

    def adicionar_staff(self, email: str, por: str):
        self.cliente.collection(COLECAO_STAFF).document(email).set(
            {"email": email, "adicionado_por": por, "adicionado_em": agora_iso()}
        )

    def remover_staff(self, email: str) -> bool:
        doc = self.cliente.collection(COLECAO_STAFF).document(email)
        existia = doc.get().exists
        doc.delete()
        return existia

    def salvar(self, tipo: str, chave: str, dados: dict):
        if tipo == "pedido":
            self.cliente.collection(COLECAO_PEDIDOS).document(chave).set(dados)
        else:
            self.cliente.collection(COLECAO_META).document(DOC_META).set(dados)


class Sincronizador:
    """Fila de gravações no Firebase, processada em segundo plano e em ordem."""

    def __init__(self, remoto: FirestoreRemoto):
        self.remoto = remoto
        self.fila: queue.Queue = queue.Queue()
        self.falhos: dict[tuple[str, str], dict] = {}  # (tipo, chave) -> versão mais recente que não subiu
        self.ultima_sync: Optional[str] = None
        self.ultimo_erro: Optional[str] = None
        threading.Thread(target=self._rodar, name="loja-firebase", daemon=True).start()

    def enviar(self, tipo: str, chave: str, dados: dict):
        # cópia profunda: o que vai para a fila não muda se o pedido mudar depois
        self.fila.put((tipo, chave, copy.deepcopy(dados)))

    @property
    def pendentes(self) -> int:
        return self.fila.qsize() + len(self.falhos)

    def _tentar(self, tipo: str, chave: str, dados: dict) -> bool:
        try:
            self.remoto.salvar(tipo, chave, dados)
        except Exception as e:
            self.ultimo_erro = f"{type(e).__name__}: {e}"
            return False
        self.ultima_sync, self.ultimo_erro = agora_iso(), None
        return True

    def _enviar_com_retentativas(self, tipo: str, chave: str, dados: dict) -> bool:
        for tentativa in range(TENTATIVAS):
            if self._tentar(tipo, chave, dados):
                return True
            print(f"[Loja] Falha ao gravar no Firebase (tentativa {tentativa + 1}/{TENTATIVAS}): {self.ultimo_erro}")
            if tentativa < TENTATIVAS - 1:
                time.sleep(2 ** tentativa)
        return False

    def _rodar(self):
        while True:
            try:
                item = self.fila.get(timeout=INTERVALO_REENVIO)
            except queue.Empty:
                item = None
            ok = True
            if item:
                tipo, chave, dados = item
                self.falhos.pop((tipo, chave), None)  # a versão nova substitui a falha antiga
                ok = self._enviar_com_retentativas(tipo, chave, dados)
                if not ok:
                    self.falhos[(tipo, chave)] = dados
                self.fila.task_done()
            if ok:  # o Firebase respondeu: aproveita para reenviar o que ficou para trás
                for (tipo, chave), dados in list(self.falhos.items()):
                    if not self._tentar(tipo, chave, dados):
                        break
                    del self.falhos[(tipo, chave)]


# =============================================
#  ARMAZENAMENTO
# =============================================
def _atualizado(pedido: dict) -> str:
    return pedido.get("atualizado_em") or pedido.get("criado_em") or ""


def completar_pedido(pedido: dict, chave: str) -> bool:
    """Preenche os campos que pedidos de versões antigas não tinham (status, nome, datas...).

    Devolve True se mudou alguma coisa. Sem isso, os botões da staff quebrariam em pedidos
    antigos, que só tinham canal, itens, total e usuário.
    """
    padroes = {
        "id": int(chave), "nome": pedido.get("usuario", ""), "status": "aguardando_pagamento",
        "pago_em": None, "entregue_em": None, "fechado_em": None, "atualizado_por": None,
    }
    mudou = False
    for campo, valor in padroes.items():
        if campo not in pedido:
            pedido[campo] = valor
            mudou = True
    return mudou


class Armazenamento:
    """Contador de compras e histórico de pedidos."""

    def __init__(self, caminho: str, remoto: Optional[FirestoreRemoto] = None):
        self.caminho = caminho
        self.dados = {"ultimo_id": 0, "pedidos": {}, "dashboard": None}
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                self.dados.update(json.load(f))
        self.remoto = remoto
        self.sincronizador = Sincronizador(remoto) if remoto else None
        self.firebase_ok_na_partida: Optional[bool] = None

    @classmethod
    async def criar(cls, caminho: str) -> "Armazenamento":
        """Abre o armazenamento e, se houver Firebase, junta os dados dele com os locais."""
        remoto = await asyncio.to_thread(FirestoreRemoto.conectar)
        armazenamento = cls(caminho, remoto)
        if remoto:
            await asyncio.to_thread(armazenamento.sincronizar_inicio)
        else:
            armazenamento.completar_locais()
        return armazenamento

    def completar_locais(self):
        """Completa pedidos antigos do arquivo local (quando não há Firebase para sincronizar)."""
        if any([completar_pedido(p, c) for c, p in self.dados["pedidos"].items()]):
            self._salvar_local()

    # ---------- sincronização na partida ----------
    def sincronizar_inicio(self):
        """Junta Firebase e arquivo local: para cada pedido vale o mais recente."""
        try:
            meta_r, pedidos_r = self.remoto.carregar()
        except Exception as e:
            self.firebase_ok_na_partida = False
            print(f"[Loja] Não consegui ler o Firebase na partida ({type(e).__name__}: {e}). Usando o arquivo local.")
            return
        self.firebase_ok_na_partida = True

        locais = self.dados["pedidos"]
        for chave, remoto in pedidos_r.items():
            local = locais.get(chave)
            if local is None or _atualizado(remoto) > _atualizado(local):
                locais[chave] = remoto
        completados = {c for c, p in locais.items() if completar_pedido(p, c)}  # pedidos de versões antigas
        for chave, local in locais.items():
            remoto = pedidos_r.get(chave)
            if chave in completados or remoto is None or _atualizado(local) > _atualizado(remoto):
                self.sincronizador.enviar("pedido", chave, local)

        maior_id = max([int(c) for c in locais] + [0])
        self.dados["ultimo_id"] = max(self.dados["ultimo_id"], meta_r.get("ultimo_id", 0), maior_id)
        if meta_r.get("dashboard"):
            self.dados["dashboard"] = meta_r["dashboard"]
        if meta_r.get("ultimo_id") != self.dados["ultimo_id"] or meta_r.get("dashboard") != self.dados["dashboard"]:
            self._enviar_meta()
        self._salvar_local()
        print(f"[Loja] Firebase conectado: {len(locais)} pedido(s), último ID {self.dados['ultimo_id']:02d}.")

    # ---------- gravação ----------
    def _salvar_local(self):
        tmp = self.caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.caminho)

    def _enviar_pedido(self, chave: str):
        if self.sincronizador:
            self.sincronizador.enviar("pedido", chave, self.dados["pedidos"][chave])

    def _enviar_meta(self):
        if self.sincronizador:
            self.sincronizador.enviar("meta", DOC_META, {
                "ultimo_id": self.dados["ultimo_id"],
                "dashboard": self.dados["dashboard"],
                "atualizado_em": agora_iso(),
            })

    def proximo_id(self) -> int:
        return self.dados["ultimo_id"] + 1

    def registrar(self, compra_id: int, pedido: dict):
        chave = f"{compra_id:02d}"
        pedido.setdefault("atualizado_em", pedido.get("criado_em") or agora_iso())
        self.dados["ultimo_id"] = compra_id
        self.dados["pedidos"][chave] = pedido
        self._salvar_local()
        self._enviar_pedido(chave)
        self._enviar_meta()

    def atualizar(self, chave: str, **campos):
        self.dados["pedidos"][chave].update(campos, atualizado_em=agora_iso())
        self._salvar_local()
        self._enviar_pedido(chave)

    def por_canal(self, canal_id: int) -> Optional[tuple[str, dict]]:
        for chave, pedido in self.dados["pedidos"].items():
            if pedido.get("canal_id") == canal_id:
                return chave, pedido
        return None

    def todos(self) -> list[dict]:
        """Todos os pedidos, do mais recente para o mais antigo."""
        return [self.dados["pedidos"][c] for c in sorted(self.dados["pedidos"], key=int, reverse=True)]

    def definir_dashboard(self, canal_id: Optional[int], mensagem_id: Optional[int] = None):
        """Guarda onde está o painel ao vivo (None esquece o painel)."""
        self.dados["dashboard"] = {"canal_id": canal_id, "mensagem_id": mensagem_id} if canal_id else None
        self._salvar_local()
        self._enviar_meta()

    # ---------- diagnóstico ----------
    def status(self) -> dict:
        s = self.sincronizador
        return {
            "firebase": self.remoto is not None,
            "firebase_ok_na_partida": self.firebase_ok_na_partida,
            "pedidos": len(self.dados["pedidos"]),
            "ultimo_id": self.dados["ultimo_id"],
            "pendentes": s.pendentes if s else 0,
            "ultima_sync": s.ultima_sync if s else None,
            "ultimo_erro": s.ultimo_erro if s else None,
        }
