"""Testes contra o Firebase REAL. Só rodam com RUN_INTEGRATION=1:

    RUN_INTEGRATION=1 python -m pytest tests/test_integracao.py -s

Cuidados:
  - a carga e o ciclo de vida usam coleções de TESTE (prefixo teste_), nunca loja_pedidos/loja_meta;
  - as regras são verificadas sem gravar nada nas coleções reais (negado = 403; permitido num documento
    que não existe = 404);
  - o único documento temporário nas coleções reais é um e-mail de teste em loja_staff, apagado no fim;
  - usuários temporários do Firebase Auth são criados e apagados no fim, mesmo se o teste falhar.
"""
import asyncio
import base64
import json
import re
import threading
import time
import uuid

import pytest

import loja_dados as ld
from conftest import RAIZ, drenar, pedido_modelo

pytestmark = pytest.mark.integration

IDT = "https://identitytoolkit.googleapis.com"


@pytest.fixture(scope="module")
def cred():
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
    import os
    os.environ.pop("FIREBASE_CREDENTIALS_FILE", None)
    arq = RAIZ / "firebase-credentials.json"
    if not arq.exists():
        pytest.skip("sem firebase-credentials.json")
    return json.loads(arq.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def admin(cred):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    c = service_account.Credentials.from_service_account_info(cred, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = AuthorizedSession(c)
    s.cred = c
    return s


@pytest.fixture(scope="module")
def fs(cred):
    return f"https://firestore.googleapis.com/v1/projects/{cred['project_id']}/databases/(default)/documents"


@pytest.fixture()
def remoto(cred, monkeypatch):
    """Cliente real do Firestore apontando para coleções de TESTE com nome único."""
    from google.cloud import firestore
    from google.oauth2 import service_account
    sufixo = uuid.uuid4().hex[:8]
    monkeypatch.setattr(ld, "COLECAO_PEDIDOS", f"teste_pedidos_{sufixo}")
    monkeypatch.setattr(ld, "COLECAO_META", f"teste_meta_{sufixo}")
    cliente = firestore.Client(project=cred["project_id"], credentials=service_account.Credentials.from_service_account_info(cred))
    r = ld.FirestoreRemoto(cliente)
    yield r
    for col in (ld.COLECAO_PEDIDOS, ld.COLECAO_META):
        for d in cliente.collection(col).stream():
            d.reference.delete()


def _vazio(remoto):
    return all(not list(remoto.cliente.collection(c).stream()) for c in (ld.COLECAO_PEDIDOS, ld.COLECAO_META))


# ---------------------------------------------------------------- ciclo de vida real
def test_ciclo_completo_no_firestore_real(remoto, tmp_path):
    a = ld.Armazenamento(str(tmp_path / "a.json"), remoto); a.sincronizar_inicio()
    a.registrar(1, pedido_modelo(1, usuario_id=1500000000000000001, nome="Percy 🤡"))
    a.registrar(2, pedido_modelo(2))
    a.atualizar("01", status="paga", pago_em=ld.agora_iso(), atualizado_por="staff#0 (1)")
    a.definir_dashboard(1234567890123456789, 987654321098765432)
    drenar(a)
    assert a.sincronizador.ultimo_erro is None

    # perde o arquivo local: tudo volta do Firestore, com os tipos certos (IDs gigantes, emoji, None)
    b = ld.Armazenamento(str(tmp_path / "vazio.json"), remoto); b.sincronizar_inicio(); drenar(b)
    p = b.dados["pedidos"]["01"]
    assert len(b.dados["pedidos"]) == 2 and b.proximo_id() == 3
    assert p["usuario_id"] == 1500000000000000001 and p["nome"] == "Percy 🤡" and p["status"] == "paga" and p["fechado_em"] is None
    assert b.dados["dashboard"] == {"canal_id": 1234567890123456789, "mensagem_id": 987654321098765432}


def test_zerar_o_banco_nao_ressuscita_arquivo_local_antigo(remoto, tmp_path):
    antigo = str(tmp_path / "servidor.json")
    a = ld.Armazenamento(antigo, remoto); a.sincronizar_inicio()
    a.registrar(1, pedido_modelo(1)); a.registrar(2, pedido_modelo(2)); drenar(a)

    # "zera" o banco como o tools/limpar_banco.py: apaga os pedidos e troca a época
    for d in list(remoto.cliente.collection(ld.COLECAO_PEDIDOS).stream()):
        d.reference.delete()
    remoto.cliente.collection(ld.COLECAO_META).document(ld.DOC_META).set({"ultimo_id": 0, "dashboard": None, "epoca": "nova"})

    b = ld.Armazenamento(antigo, remoto); b.sincronizar_inicio(); drenar(b)       # reabre com o arquivo antigo do servidor
    assert b.dados["pedidos"] == {} and b.proximo_id() == 1 and b.dados["epoca"] == "nova"
    assert list(remoto.cliente.collection(ld.COLECAO_PEDIDOS).stream()) == []      # nada voltou para o banco


# ---------------------------------------------------------------- carga real
def test_carga_real_de_escrita_e_leitura(remoto, tmp_path):
    N = 200
    a = ld.Armazenamento(str(tmp_path / "c.json"), remoto); a.sincronizar_inicio()
    t0 = time.perf_counter()
    for i in range(1, N + 1):
        a.registrar(i, pedido_modelo(i))
    drenar(a)
    escrita = time.perf_counter() - t0
    assert a.sincronizador.ultimo_erro is None and a.sincronizador.pendentes == 0

    docs = list(remoto.cliente.collection(ld.COLECAO_PEDIDOS).stream())
    assert len(docs) == N and remoto.cliente.collection(ld.COLECAO_META).document(ld.DOC_META).get().to_dict()["ultimo_id"] == N

    # 10 painéis abertos ao mesmo tempo lendo tudo
    tempos, erros = [], []
    def leitor():
        try:
            t = time.perf_counter(); n = len(list(remoto.cliente.collection(ld.COLECAO_PEDIDOS).stream())); tempos.append(time.perf_counter() - t); assert n == N
        except Exception as e:
            erros.append(e)
    ths = [threading.Thread(target=leitor) for _ in range(10)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert not erros
    print(f"\n[integração] {N} compras gravadas no Firestore real em {escrita:.1f}s ({(2 * N + 1) / escrita:.0f} escritas/s); "
          f"10 leituras simultâneas de {N} docs: média {sum(tempos) / len(tempos):.2f}s, pior {max(tempos):.2f}s")
    assert escrita < 60 and max(tempos) < 15


def test_atualizacoes_em_sequencia_chegam_na_ordem(remoto, tmp_path):
    a = ld.Armazenamento(str(tmp_path / "o.json"), remoto); a.sincronizar_inicio()
    a.registrar(1, pedido_modelo(1))
    for n in range(40):
        a.atualizar("01", atualizado_por=f"v{n}")
    drenar(a)
    assert remoto.cliente.collection(ld.COLECAO_PEDIDOS).document("01").get().to_dict()["atualizado_por"] == "v39"


# ---------------------------------------------------------------- regras com usuários reais (sem gravar nas coleções da loja)
@pytest.fixture()
def usuarios(admin, cred, fs):
    import requests
    from google.auth import jwt as gjwt
    chave = re.search(r'"apiKey": "([^"]+)"', (RAIZ / "dashboard" / "firebase-config.js").read_text(encoding="utf-8")).group(1)
    sufixo = uuid.uuid4().hex[:6]
    contas = {
        "staff": (f"t-staff-{sufixo}", f"Teste.Staff.{sufixo}@Example.com", True),
        "fora": (f"t-fora-{sufixo}", f"teste.fora.{sufixo}@example.com", True),
        "naoverif": (f"t-nv-{sufixo}", f"teste.naoverif.{sufixo}@example.com", False),
    }
    emails_na_lista = [f"teste.staff.{sufixo}@example.com", f"teste.naoverif.{sufixo}@example.com"]
    tokens = {}
    try:
        for nome, (uid, email, verificado) in contas.items():
            r = admin.post(f"{IDT}/v1/projects/{cred['project_id']}/accounts", json={"localId": uid, "email": email, "emailVerified": verificado}, timeout=30)
            assert r.status_code == 200, r.text[:200]
            agora = int(time.time())
            custom = gjwt.encode(admin.cred.signer, {"iss": cred["client_email"], "sub": cred["client_email"], "aud": f"{IDT}/google.identity.identitytoolkit.v1.IdentityToolkit",
                                                      "iat": agora, "exp": agora + 3000, "uid": uid})
            t = requests.post(f"{IDT}/v1/accounts:signInWithCustomToken?key={chave}", json={"token": custom.decode(), "returnSecureToken": True}, timeout=30)
            assert t.status_code == 200, t.text[:200]
            tokens[nome] = t.json()["idToken"]
        for e in emails_na_lista:
            admin.patch(f"{fs}/loja_staff/{e}", json={"fields": {"email": {"stringValue": e}}}, timeout=30)
        yield tokens
    finally:
        for e in emails_na_lista:
            admin.delete(f"{fs}/loja_staff/{e}", timeout=30)
        for uid, _, _ in contas.values():
            admin.post(f"{IDT}/v1/projects/{cred['project_id']}/accounts:delete", json={"localId": uid}, timeout=30)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def test_regras_com_usuarios_reais(usuarios, fs):
    import requests
    DOC = "loja_pedidos/zz-nao-existe"          # não existe: permitido => 404, negado => 403 (nada é gravado)
    tok = usuarios
    corpo = {"fields": {"x": {"stringValue": "y"}}}
    casos = [
        ("anônimo não lê", requests.get(f"{fs}/{DOC}", timeout=30).status_code, 403),
        ("fora da lista não lê", requests.get(f"{fs}/{DOC}", headers=_h(tok["fora"]), timeout=30).status_code, 403),
        ("e-mail não verificado não lê, mesmo na lista", requests.get(f"{fs}/{DOC}", headers=_h(tok["naoverif"]), timeout=30).status_code, 403),
        ("staff autorizado lê (e-mail com maiúsculas)", requests.get(f"{fs}/{DOC}", headers=_h(tok["staff"]), timeout=30).status_code, 404),
        ("staff autorizado lista os pedidos", requests.get(f"{fs}/loja_pedidos", headers=_h(tok["staff"]), timeout=30).status_code, 200),
        ("staff autorizado lê o meta", requests.get(f"{fs}/loja_meta/estado", headers=_h(tok["staff"]), timeout=30).status_code, 200),
        ("fora da lista não lista", requests.get(f"{fs}/loja_pedidos", headers=_h(tok["fora"]), timeout=30).status_code, 403),
        ("staff não cria pedido", requests.post(f"{fs}/loja_pedidos?documentId=zz-novo", json=corpo, headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("staff não altera pedido", requests.patch(f"{fs}/{DOC}", json=corpo, headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("staff não apaga pedido", requests.delete(f"{fs}/{DOC}", headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("staff não altera o meta", requests.patch(f"{fs}/loja_meta/estado", json=corpo, headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("staff não lê a lista de e-mails", requests.get(f"{fs}/loja_staff", headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("ninguém se autoriza sozinho", requests.post(f"{fs}/loja_staff?documentId=intruso@example.com", json=corpo, headers=_h(tok["fora"]), timeout=30).status_code, 403),
        ("coleção desconhecida fechada", requests.get(f"{fs}/qualquer_coisa/x", headers=_h(tok["staff"]), timeout=30).status_code, 403),
    ]
    erradas = [(n, o, e) for n, o, e in casos if o != e]
    assert not erradas, erradas
    print(f"\n[integração] regras com usuários reais: {len(casos)}/{len(casos)} corretas")


def test_nada_dos_testes_ficou_nas_colecoes_reais(admin, fs):
    docs = admin.get(f"{fs}/loja_staff", timeout=30).json().get("documents") or []
    assert not [d for d in docs if "example.com" in d["name"]]
    assert admin.get(f"{fs}/loja_pedidos/zz-novo", timeout=30).status_code == 404
