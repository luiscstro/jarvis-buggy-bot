"""Catálogo da loja (páginas, itens, preços e descrições) que a staff edita pelo painel web.

Onde os dados moram
  - Firebase: coleções `loja_paginas` e `loja_itens` (o painel grava; o bot só lê).
  - Se o Firebase estiver fora do ar na partida, vale a última cópia local (catalogo_cache.json);
    sem ela, o catálogo padrão do código (catalogo_padrao.py).

O bot acompanha o Firebase AO VIVO (on_snapshot): editou no painel, a loja do Discord muda em segundos.

Tudo que vem do banco é tratado como NÃO CONFIÁVEL: cada documento é validado e normalizado
(`normalizar_item`/`normalizar_pagina`) e o que estiver fora do formato é ignorado, nunca quebra a loja.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from catalogo_padrao import ITENS_PADRAO, PAGINAS_PADRAO

COLECAO_ITENS = "loja_itens"
COLECAO_PAGINAS = "loja_paginas"
COLECAO_LOG = "loja_catalogo_log"
DOC_SEMEADO = "catalogo_semeado"          # documento em loja_meta: o catálogo padrão já foi enviado ao Firebase
ARQUIVO_CACHE = "catalogo_cache.json"

MAX_PAGINAS = 5            # os botões de página cabem numa linha do Discord
MAX_ITENS = 25             # um menu do Discord aceita no máximo 25 opções
MAX_NOME = 60
MAX_DESCRICAO = 1500
MAX_INTRO = 1500
MAX_TEXTO_PAGINA = 4000    # o limite de um embed é 4096
PRECO_MAX = 1_000_000      # R$ 10.000,00
MARCADOR = "🔷"

ID_ITEM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ID_PAGINA_RE = re.compile(r"^p[1-9]$")


def brl(centavos: int) -> str:
    return f"R$ {centavos // 100},{centavos % 100:02d}"


def preco_txt(centavos: int) -> str:
    """Preço para mostrar a quem compra: o item de R$ 0,00 aparece como 'Grátis'."""
    return "Grátis" if centavos == 0 else brl(centavos)


# Opção FIXA da loja ("Indicação", R$ 0,00). Não mora no banco (por isso o painel não edita nem apaga) e é sempre
# acrescentada ao catálogo. Nunca entra nos VIPs à escolha dos planos mensais.
CHAVE_INDICACAO = "indicacao"
ITEM_INDICACAO = {
    "key": CHAVE_INDICACAO, "nome": "Indicação", "preco": 0, "ativo": True, "fixo": True,
    "descricao": "Indicou alguém para o servidor (ou veio por indicação)? Escolha esta opção, sem custo: "
                 "o canal da compra abre para você informar o nome de quem indicou e a staff conferir.",
}


def com_item_fixo(itens: dict[str, dict]) -> dict[str, dict]:
    """Catálogo + a opção fixa de indicação (ela vence qualquer item do banco com a mesma chave)."""
    return {**{k: v for k, v in itens.items() if k != CHAVE_INDICACAO}, CHAVE_INDICACAO: dict(ITEM_INDICACAO)}


def _inteiro(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------- validação
def normalizar_item(chave: str, d: dict) -> Optional[dict]:
    """Valida um documento de item. Devolve o item normalizado ou None se for inválido."""
    if not isinstance(chave, str) or not ID_ITEM_RE.match(chave) or not isinstance(d, dict):
        return None
    nome = d.get("nome")
    preco = d.get("preco_centavos")
    if not isinstance(nome, str) or not (1 <= len(nome.strip()) <= MAX_NOME):
        return None
    if not _inteiro(preco) or not (1 <= preco <= PRECO_MAX):
        return None
    descricao = d.get("descricao")
    pagina = d.get("pagina")
    ordem = d.get("ordem")
    return {
        "key": chave,
        "nome": nome.strip(),
        "preco": preco,
        "descricao": (descricao if isinstance(descricao, str) else "").strip()[:MAX_DESCRICAO],
        "pagina": pagina if isinstance(pagina, str) else "",
        "ordem": ordem if _inteiro(ordem) else 0,
        "ativo": d.get("ativo", True) is True,
    }


def normalizar_pagina(pid: str, d: dict) -> Optional[dict]:
    if not isinstance(pid, str) or not ID_PAGINA_RE.match(pid) or not isinstance(d, dict):
        return None
    intro = d.get("introducao")
    ordem = d.get("ordem")
    return {
        "id": pid,
        "ordem": ordem if _inteiro(ordem) else 0,
        "introducao": (intro if isinstance(intro, str) else "").strip()[:MAX_INTRO],
    }


# ---------------------------------------------------------------- montagem
def render_descricao(introducao: str, itens: list[dict]) -> str:
    """Texto da página no Discord: introdução + um bloco (título com nome e preço, depois a descrição) por item."""
    partes = [introducao] if introducao else []
    for i in itens:
        bloco = f"## {MARCADOR} {i['nome']} - {brl(i['preco'])}"
        if i["descricao"]:
            bloco += "\n" + i["descricao"]
        partes.append(bloco)
    texto = "\n\n".join(partes)
    if len(texto) > MAX_TEXTO_PAGINA:
        aviso = "\n…(texto cortado: página muito longa)"
        texto = texto[:MAX_TEXTO_PAGINA - len(aviso)] + aviso
    return texto


def montar_catalogo(paginas_docs: dict, itens_docs: dict) -> tuple[list[dict], dict[str, dict]]:
    """Transforma os documentos do banco em (lista de páginas, dicionário de itens visíveis).

    Só entram itens válidos, visíveis e de uma página que exista; no máximo MAX_PAGINAS páginas e
    MAX_ITENS itens, na ordem escolhida pela staff.
    """
    paginas = [p for pid, d in paginas_docs.items() if (p := normalizar_pagina(pid, d))]
    paginas.sort(key=lambda p: (p["ordem"], p["id"]))
    paginas = paginas[:MAX_PAGINAS]
    posicao = {p["id"]: n for n, p in enumerate(paginas)}
    itens = [i for k, d in itens_docs.items() if (i := normalizar_item(k, d)) and i["ativo"] and i["pagina"] in posicao]
    itens.sort(key=lambda i: (posicao[i["pagina"]], i["ordem"], i["key"]))
    itens = itens[:MAX_ITENS]
    for p in paginas:
        p["itens"] = [i for i in itens if i["pagina"] == p["id"]]
        p["descricao"] = render_descricao(p["introducao"], p["itens"])
    return paginas, {i["key"]: i for i in itens}


def assinatura_precos(itens: dict[str, dict]) -> tuple:
    """Muda quando um preço muda ou um item entra/sai (usada para avisar quem está com o carrinho aberto)."""
    return tuple(sorted((k, v["preco"]) for k, v in itens.items()))


def catalogo_padrao() -> tuple[list[dict], dict[str, dict]]:
    return montar_catalogo({p["id"]: p for p in PAGINAS_PADRAO}, {i["key"]: i for i in ITENS_PADRAO})


# ---------------------------------------------------------------- cópia local
def salvar_cache(paginas_docs: dict, itens_docs: dict, caminho: str = ARQUIVO_CACHE):
    def limpo(d):  # timestamps do Firestore não vão para o JSON
        return {k: v for k, v in d.items() if isinstance(v, (str, int, bool, float, type(None)))}
    dados = {"paginas": {k: limpo(v) for k, v in paginas_docs.items()}, "itens": {k: limpo(v) for k, v in itens_docs.items()}}
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, caminho)


def carregar_cache(caminho: str = ARQUIVO_CACHE) -> Optional[tuple[dict, dict]]:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados["paginas"], dados["itens"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- semear o Firebase
def semear(cliente, forcar: bool = False) -> bool:
    """Envia o catálogo padrão ao Firebase, uma única vez. Devolve True se enviou.

    Só semeia se as coleções estiverem vazias e o catálogo nunca tiver sido semeado (para um
    reinício do bot não "ressuscitar" itens que a staff apagou de propósito).
    """
    import loja_dados as ld
    marca = cliente.collection(ld.COLECAO_META).document(DOC_SEMEADO)
    if not forcar and marca.get().exists:
        return False
    vazio = not list(cliente.collection(COLECAO_ITENS).stream()) and not list(cliente.collection(COLECAO_PAGINAS).stream())
    if vazio or forcar:
        agora = datetime.now(timezone.utc)
        for p in PAGINAS_PADRAO:
            cliente.collection(COLECAO_PAGINAS).document(p["id"]).set(
                {"introducao": p["introducao"], "ordem": p["ordem"], "atualizado_por": "sistema", "atualizado_em": agora})
        for i in ITENS_PADRAO:
            cliente.collection(COLECAO_ITENS).document(i["key"]).set({
                "nome": i["nome"], "preco_centavos": i["preco_centavos"], "descricao": i["descricao"], "pagina": i["pagina"],
                "ordem": i["ordem"], "ativo": i["ativo"], "atualizado_por": "sistema", "atualizado_em": agora})
    marca.set({"em": ld.agora_iso()})
    return vazio or forcar


# ---------------------------------------------------------------- sincronização ao vivo
class CatalogoSync:
    """Mantém o catálogo do bot igual ao do Firebase, ao vivo, com plano B local."""

    def __init__(self, remoto, aplicar: Callable[[list, dict, str], None], arquivo_cache: str = ARQUIVO_CACHE):
        self.remoto = remoto
        self.aplicar = aplicar
        self.arquivo_cache = arquivo_cache
        self.fonte = "padrao"                 # "firebase" | "cache" | "padrao"
        self.ultimo_erro: Optional[str] = None
        self._estado: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pronto = threading.Event()
        self._watches: list = []

    def iniciar(self, espera: float = 15.0):
        """Bloqueante (rode em thread): semeia se preciso, escuta o Firebase e espera a primeira carga."""
        if self.remoto:
            try:
                semear(self.remoto.cliente)
                c = self.remoto.cliente
                self._watches = [
                    c.collection(COLECAO_PAGINAS).on_snapshot(lambda docs, mud, t: self._ao_mudar(COLECAO_PAGINAS, docs)),
                    c.collection(COLECAO_ITENS).on_snapshot(lambda docs, mud, t: self._ao_mudar(COLECAO_ITENS, docs)),
                ]
                if self._pronto.wait(espera):
                    return
                self.ultimo_erro = "o Firebase não respondeu a tempo"
                print("[Loja] O catálogo do Firebase não respondeu a tempo; usando a cópia local ou o padrão até ele chegar.")
            except Exception as e:
                self.ultimo_erro = f"{type(e).__name__}: {e}"
                print(f"[Loja] Não consegui acompanhar o catálogo no Firebase ({self.ultimo_erro}).")
        self._usar_cache_ou_padrao()

    def parar(self):
        for w in self._watches:
            try:
                w.unsubscribe()
            except Exception:
                pass
        self._watches = []

    def _usar_cache_ou_padrao(self):
        cache = carregar_cache(self.arquivo_cache)
        if cache:
            paginas, itens = montar_catalogo(*cache)
            self.fonte = "cache"
            self.aplicar(paginas, itens, "cache")
            print(f"[Loja] Catálogo da cópia local: {len(itens)} item(ns) em {len(paginas)} página(s).")
        else:
            paginas, itens = catalogo_padrao()
            self.fonte = "padrao"
            self.aplicar(paginas, itens, "padrao")
            print("[Loja] Catálogo padrão do código em uso (sem Firebase e sem cópia local).")

    def _ao_mudar(self, colecao: str, docs):
        """Chamado pela biblioteca do Firebase (em outra thread) a cada mudança de uma coleção."""
        try:
            with self._lock:
                self._estado[colecao] = {d.id: d.to_dict() for d in docs}
                if COLECAO_ITENS not in self._estado or COLECAO_PAGINAS not in self._estado:
                    return                                    # espera as duas coleções chegarem
                paginas_docs, itens_docs = self._estado[COLECAO_PAGINAS], self._estado[COLECAO_ITENS]
                paginas, itens = montar_catalogo(paginas_docs, itens_docs)
                self.fonte, self.ultimo_erro = "firebase", None
                self.aplicar(paginas, itens, "firebase")
                try:
                    salvar_cache(paginas_docs, itens_docs, self.arquivo_cache)
                except OSError:
                    pass
            print(f"[Loja] Catálogo atualizado: {len(itens)} item(ns) em {len(paginas)} página(s).")
            self._pronto.set()
        except Exception as e:                                # nunca derruba a thread do Firebase
            self.ultimo_erro = f"{type(e).__name__}: {e}"
            print(f"[Loja] Erro ao aplicar o catálogo: {self.ultimo_erro}")
