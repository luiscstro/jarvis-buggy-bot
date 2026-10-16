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
    import catalogo
    monkeypatch.setattr(catalogo, "COLECAO_ITENS", f"teste_itens_{sufixo}")
    monkeypatch.setattr(catalogo, "COLECAO_PAGINAS", f"teste_paginas_{sufixo}")
    cliente = firestore.Client(project=cred["project_id"], credentials=service_account.Credentials.from_service_account_info(cred))
    r = ld.FirestoreRemoto(cliente)
    yield r
    for col in (ld.COLECAO_PEDIDOS, ld.COLECAO_META, catalogo.COLECAO_ITENS, catalogo.COLECAO_PAGINAS):
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


# ---------------------------------------------------------------- catálogo ao vivo no Firestore real
def test_catalogo_semeia_e_acompanha_edicoes_ao_vivo_no_firestore_real(remoto, tmp_path):
    import catalogo
    aplicados = []
    sync = catalogo.CatalogoSync(remoto, lambda pag, itens, fonte: aplicados.append((pag, itens, fonte)), str(tmp_path / "c.json"))
    try:
        sync.iniciar(espera=30)
        assert sync.fonte == "firebase" and len(aplicados[-1][1]) == 12                      # semeou o padrão e já aplicou
        cliente = remoto.cliente
        doc = cliente.collection(catalogo.COLECAO_ITENS).document("escolhidos")
        dados = doc.get().to_dict(); dados["preco_centavos"] = 777
        antes = len(aplicados)
        doc.set(dados)                                                                          # a "edição do painel"
        for _ in range(100):
            if aplicados[-1][1]["escolhidos"]["preco"] == 777:
                break
            threading.Event().wait(0.1)
        assert aplicados[-1][1]["escolhidos"]["preco"] == 777 and len(aplicados) > antes       # chegou ao vivo, sem reiniciar
        doc.delete()
        for _ in range(100):
            if "escolhidos" not in aplicados[-1][1]:
                break
            threading.Event().wait(0.1)
        assert "escolhidos" not in aplicados[-1][1] and len(aplicados[-1][1]) == 11
        print(f"\n[integração] catálogo ao vivo: semeou 12 itens, preço e exclusão chegaram ao bot em tempo real")
    finally:
        sync.parar()


# ---------------------------------------------------------------- regras com usuários reais (sem gravar nas coleções reais da loja)
def _valor(x):
    """Python -> formato de valor da API REST do Firestore."""
    if x is None:
        return {"nullValue": None}
    if isinstance(x, bool):
        return {"booleanValue": x}
    if isinstance(x, int):
        return {"integerValue": str(x)}
    if isinstance(x, float):
        return {"doubleValue": x}
    if isinstance(x, str):
        return {"stringValue": x}
    if isinstance(x, dict):
        return {"mapValue": {"fields": {k: _valor(v) for k, v in x.items()}}}
    raise TypeError(x)


@pytest.fixture()
def usuarios(admin, cred, fs):
    import requests
    from google.auth import jwt as gjwt
    chave = re.search(r'"apiKey": "([^"]+)"', (RAIZ / "dashboard" / "firebase-config.js").read_text(encoding="utf-8")).group(1)
    sufixo = uuid.uuid4().hex[:6]
    contas = {                                                                                  # nome: (uid, e-mail, verificado, nível)
        "staff": (f"t-staff-{sufixo}", f"Teste.Staff.{sufixo}@Example.com", True, False),        # só visualiza (e-mail com maiúsculas)
        "editor": (f"t-edit-{sufixo}", f"teste.editor.{sufixo}@example.com", True, True),
        "fora": (f"t-fora-{sufixo}", f"teste.fora.{sufixo}@example.com", True, None),            # fora da lista
        "naoverif": (f"t-nv-{sufixo}", f"teste.naoverif.{sufixo}@example.com", False, True),     # na lista como editor, mas e-mail NÃO verificado
    }
    tokens, emails, customs = {}, {}, {}
    na_lista = []
    try:
        for nome, (uid, email, verificado, nivel) in contas.items():
            r = admin.post(f"{IDT}/v1/projects/{cred['project_id']}/accounts", json={"localId": uid, "email": email, "emailVerified": verificado}, timeout=30)
            assert r.status_code == 200, r.text[:200]
            agora = int(time.time())
            custom = gjwt.encode(admin.cred.signer, {"iss": cred["client_email"], "sub": cred["client_email"], "aud": f"{IDT}/google.identity.identitytoolkit.v1.IdentityToolkit",
                                                      "iat": agora, "exp": agora + 3000, "uid": uid})
            t = requests.post(f"{IDT}/v1/accounts:signInWithCustomToken?key={chave}", json={"token": custom.decode(), "returnSecureToken": True}, timeout=30)
            assert t.status_code == 200, t.text[:200]
            tokens[nome], emails[nome], customs[nome] = t.json()["idToken"], email.lower(), custom.decode()
            if nivel is not None:
                doc = emails[nome]
                admin.patch(f"{fs}/loja_staff/{doc}", json={"fields": {"email": _valor(doc), "editor": _valor(nivel)}}, timeout=30)
                na_lista.append(doc)
        yield {"tokens": tokens, "emails": emails, "custom": customs}
    finally:
        for e in na_lista:
            admin.delete(f"{fs}/loja_staff/{e}", timeout=30)
        for uid, _, _, _ in contas.values():
            admin.post(f"{IDT}/v1/projects/{cred['project_id']}/accounts:delete", json={"localId": uid}, timeout=30)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def test_regras_de_leitura_com_usuarios_reais(usuarios, fs):
    import requests
    tok, em = usuarios["tokens"], usuarios["emails"]
    DOC = "loja_pedidos/zz-nao-existe"          # não existe: permitido => 404, negado => 403 (nada é gravado)
    corpo = {"fields": {"x": {"stringValue": "y"}}}
    get = lambda caminho, quem=None: requests.get(f"{fs}/{caminho}", headers=_h(tok.get(quem)), timeout=30).status_code
    casos = [
        ("anônimo não lê", get(DOC), 403),
        ("fora da lista não lê", get(DOC, "fora"), 403),
        ("e-mail não verificado não lê, mesmo na lista", get(DOC, "naoverif"), 403),
        ("staff autorizado lê (e-mail com maiúsculas)", get(DOC, "staff"), 404),
        ("staff autorizado lista os pedidos", get("loja_pedidos", "staff"), 200),
        ("staff autorizado lê o meta", get("loja_meta/estado", "staff"), 200),
        ("fora da lista não lista", get("loja_pedidos", "fora"), 403),
        ("staff lê o catálogo", get("loja_itens", "staff"), 200),
        ("staff lê as páginas", get("loja_paginas", "staff"), 200),
        ("staff lê o histórico", get("loja_catalogo_log", "staff"), 200),
        ("fora da lista não lê o catálogo", get("loja_itens", "fora"), 403),
        ("anônimo não lê o catálogo", get("loja_itens"), 403),
        ("cada pessoa lê o PRÓPRIO registro de acesso", get(f"loja_staff/{em['staff']}", "staff"), 200),
        ("o editor lê o próprio registro", get(f"loja_staff/{em['editor']}", "editor"), 200),
        ("não lê o registro de OUTRA pessoa", get(f"loja_staff/{em['editor']}", "staff"), 403),
        ("a lista de e-mails autorizados fica fechada", get("loja_staff", "editor"), 403),
        ("quem está fora da lista não descobre se existe registro de outro", get(f"loja_staff/{em['staff']}", "fora"), 403),
        ("coleção desconhecida fechada", get("qualquer_coisa/x", "editor"), 403),
        ("staff não cria pedido", requests.post(f"{fs}/loja_pedidos?documentId=zz-novo", json=corpo, headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("EDITOR também não cria pedido", requests.post(f"{fs}/loja_pedidos?documentId=zz-novo", json=corpo, headers=_h(tok["editor"]), timeout=30).status_code, 403),
        ("editor não altera pedido", requests.patch(f"{fs}/{DOC}", json=corpo, headers=_h(tok["editor"]), timeout=30).status_code, 403),
        ("editor não apaga pedido", requests.delete(f"{fs}/{DOC}", headers=_h(tok["editor"]), timeout=30).status_code, 403),
        ("editor não altera o meta (contador, época)", requests.patch(f"{fs}/loja_meta/estado", json=corpo, headers=_h(tok["editor"]), timeout=30).status_code, 403),
        ("ninguém se autoriza sozinho", requests.post(f"{fs}/loja_staff?documentId=intruso@example.com", json=corpo, headers=_h(tok["fora"]), timeout=30).status_code, 403),
        ("viewer não se promove a editor", requests.patch(f"{fs}/loja_staff/{em['staff']}", json={"fields": {"editor": {"booleanValue": True}}}, headers=_h(tok["staff"]), timeout=30).status_code, 403),
        ("editor não autoriza ninguém", requests.post(f"{fs}/loja_staff?documentId=intruso@example.com", json=corpo, headers=_h(tok["editor"]), timeout=30).status_code, 403),
    ]
    erradas = [(n, o, e) for n, o, e in casos if o != e]
    assert not erradas, erradas
    print(f"\n[integração] regras de leitura/acesso com usuários reais: {len(casos)}/{len(casos)} corretas")


def _commit(cred, token, escritas):
    import requests
    base = f"projects/{cred['project_id']}/databases/(default)/documents"
    corpo = {"writes": [dict(e, **({"update": dict(e["update"], name=f"{base}/{e['update']['name']}")} if "update" in e else {"delete": f"{base}/{e['delete']}"})) for e in escritas]}
    return requests.post(f"https://firestore.googleapis.com/v1/{base}:commit", json=corpo, headers=_h(token), timeout=30).status_code


def _item(email, **mudar):
    """Documento de item válido (oculto e em página inexistente, para nunca aparecer na loja). `mudar` troca/remove campos."""
    campos = {"nome": "Item de teste", "preco_centavos": 500, "descricao": "teste automático", "pagina": "p5", "ordem": 1, "ativo": False, "atualizado_por": email}
    for k, v in mudar.items():
        if v is ...:
            campos.pop(k, None)
        else:
            campos[k] = v
    return campos


def _gravar_item(cred, token, id_, campos, tempo="servidor"):
    escrita = {"update": {"name": f"loja_itens/{id_}", "fields": {k: _valor(v) for k, v in campos.items()}}}
    if tempo == "servidor":
        escrita["updateTransforms"] = [{"fieldPath": "atualizado_em", "setToServerValue": "REQUEST_TIME"}]
    elif tempo == "cliente":                                             # a pessoa tenta escolher a hora
        escrita["update"]["fields"]["atualizado_em"] = {"timestampValue": "2020-01-01T00:00:00Z"}
    return _commit(cred, token, [escrita])


def test_regras_de_escrita_do_catalogo_com_usuarios_reais(usuarios, cred, admin, fs):
    tok, em = usuarios["tokens"], usuarios["emails"]
    sufixo = uuid.uuid4().hex[:6]
    criados = []

    def item_id(nome):
        i = f"zz-teste-{sufixo}-{nome}"[:40]
        criados.append(i)
        return i
    ed, ver = em["editor"], em["staff"]
    try:
        # ---- o caminho feliz: editor cria, edita e exclui
        base = item_id("ok")
        assert _gravar_item(cred, tok["editor"], base, _item(ed)) == 200, "editor deveria conseguir criar um item válido"
        assert _gravar_item(cred, tok["editor"], base, _item(ed, preco_centavos=750, nome="Novo nome", descricao="x" * 1500, ativo=False)) == 200
        guardado = admin.get(f"{fs}/loja_itens/{base}", timeout=30).json()["fields"]
        assert guardado["preco_centavos"]["integerValue"] == "750" and guardado["atualizado_por"]["stringValue"] == ed
        for limite in (1, 1_000_000):                                                     # extremos válidos do preço
            assert _gravar_item(cred, tok["editor"], base, _item(ed, preco_centavos=limite)) == 200
        assert _commit(cred, tok["editor"], [{"delete": f"loja_itens/{base}"}]) == 200

        # ---- tudo que é INVÁLIDO é recusado, mesmo para o editor
        invalidos = [
            ("preço zero", _item(ed, preco_centavos=0)), ("preço negativo", _item(ed, preco_centavos=-5)),
            ("preço acima do limite", _item(ed, preco_centavos=1_000_001)), ("preço decimal", _item(ed, preco_centavos=5.5)),
            ("preço em texto", _item(ed, preco_centavos="500")), ("sem preço", _item(ed, preco_centavos=...)),
            ("nome vazio", _item(ed, nome="")), ("nome com 61 caracteres", _item(ed, nome="n" * 61)), ("nome não é texto", _item(ed, nome=5)),
            ("descrição com 1501 caracteres", _item(ed, descricao="d" * 1501)), ("descrição não é texto", _item(ed, descricao=7)),
            ("página inexistente p9", _item(ed, pagina="p9")), ("página qualquer", _item(ed, pagina="x")), ("página com maiúscula", _item(ed, pagina="P1")),
            ("ordem negativa", _item(ed, ordem=-1)), ("ordem enorme", _item(ed, ordem=10_000)), ("ordem decimal", _item(ed, ordem=1.5)),
            ("ativo em texto", _item(ed, ativo="sim")), ("sem o campo ativo", _item(ed, ativo=...)),
            ("campo extra (tentando passar 'admin')", _item(ed, admin=True)), ("campo extra 'preco_antigo'", _item(ed, preco_antigo=1)),
            ("atualizado_por de OUTRA pessoa", _item(ed, atualizado_por=ver)), ("atualizado_por vazio", _item(ed, atualizado_por="")),
            ("sem atualizado_por", _item(ed, atualizado_por=...)),
        ]
        for nome, campos in invalidos:
            assert _gravar_item(cred, tok["editor"], item_id("inv"), campos) == 403, f"deveria recusar: {nome}"
        assert _gravar_item(cred, tok["editor"], item_id("hora"), _item(ed), tempo="cliente") == 403, "a pessoa não pode escolher a hora"
        assert _gravar_item(cred, tok["editor"], item_id("semhora"), _item(ed), tempo=None) == 403, "sem carimbo de hora do servidor"
        for id_ruim in ("Maiuscula", "com espaco", "x" * 41, "_comeca", "ç"):
            assert _gravar_item(cred, tok["editor"], id_ruim, _item(ed)) in (400, 403), f"id inválido aceito: {id_ruim!r}"
            criados.append(id_ruim)

        # ---- quem NÃO é editor não grava, apaga nem altera nada
        for quem, rotulo in (("staff", "só visualiza"), ("fora", "fora da lista"), ("naoverif", "e-mail não verificado"), (None, "anônimo")):
            e = ver if quem == "staff" else em.get(quem, "x@example.com")
            assert _gravar_item(cred, tok.get(quem), item_id("nao"), _item(e)) == 403, f"{rotulo} gravou um item!"
        alvo = item_id("alvo")
        assert _gravar_item(cred, tok["editor"], alvo, _item(ed)) == 200
        assert _commit(cred, tok["staff"], [{"delete": f"loja_itens/{alvo}"}]) == 403, "quem só visualiza apagou um item"
        assert _gravar_item(cred, tok["staff"], alvo, _item(ver, preco_centavos=1)) == 403, "quem só visualiza alterou um preço"
        assert _commit(cred, tok["naoverif"], [{"delete": f"loja_itens/{alvo}"}]) == 403
        assert admin.get(f"{fs}/loja_itens/{alvo}", timeout=30).status_code == 200, "o item deveria continuar existindo"
        assert admin.get(f"{fs}/loja_itens/{alvo}", timeout=30).json()["fields"]["preco_centavos"]["integerValue"] == "500"

        # ---- páginas: valores inválidos são recusados (o caso válido fica para o teste opcional abaixo, para não aparecer na loja)
        for nome, campos in (("página p9", ("loja_paginas/p9", {"introducao": "x", "ordem": 1, "atualizado_por": ed})),
                             ("intro gigante", ("loja_paginas/p5", {"introducao": "x" * 1501, "ordem": 1, "atualizado_por": ed})),
                             ("campo extra", ("loja_paginas/p5", {"introducao": "x", "ordem": 1, "atualizado_por": ed, "x": 1})),
                             ("ordem fora", ("loja_paginas/p5", {"introducao": "x", "ordem": 100, "atualizado_por": ed}))):
            caminho, dados = campos
            esc = {"update": {"name": caminho, "fields": {k: _valor(v) for k, v in dados.items()}}, "updateTransforms": [{"fieldPath": "atualizado_em", "setToServerValue": "REQUEST_TIME"}]}
            assert _commit(cred, tok["editor"], [esc]) == 403, f"deveria recusar a página: {nome}"
        esc = {"update": {"name": "loja_paginas/p5", "fields": {"introducao": _valor("x"), "ordem": _valor(1), "atualizado_por": _valor(ver)}}, "updateTransforms": [{"fieldPath": "atualizado_em", "setToServerValue": "REQUEST_TIME"}]}
        assert _commit(cred, tok["staff"], [esc]) == 403, "quem só visualiza criou uma página"
    finally:
        for i in criados:
            admin.delete(f"{fs}/loja_itens/{i}", timeout=30)
    print("\n[integração] regras de escrita do catálogo com usuários reais: caminho feliz, "
          f"{len(invalidos) + 2} gravações inválidas recusadas, permissões de quem não é editor")


def test_historico_so_se_acrescenta_com_usuarios_reais(usuarios, cred, admin, fs):
    tok, em = usuarios["tokens"], usuarios["emails"]
    ids, ed = [], em["editor"]

    def registro(quem_email, **mudar):
        campos = {"quem": quem_email, "acao": "editar", "alvo": "zz-teste", "resumo": "teste automático"}
        for k, v in mudar.items():
            campos.pop(k) if v is ... else campos.__setitem__(k, v)
        return campos

    def gravar(token, id_, campos, tempo=True):
        esc = {"update": {"name": f"loja_catalogo_log/{id_}", "fields": {k: _valor(v) for k, v in campos.items()}}}
        if tempo:
            esc["updateTransforms"] = [{"fieldPath": "quando", "setToServerValue": "REQUEST_TIME"}]
        return _commit(cred, token, [esc])
    try:
        uid = f"zz-teste-log-{uuid.uuid4().hex[:6]}"; ids.append(uid)
        assert gravar(tok["editor"], uid, registro(ed)) == 200, "o editor deveria conseguir registrar uma alteração"
        assert gravar(tok["editor"], uid, registro(ed, resumo="adulterado")) == 403, "o histórico não pode ser editado"
        assert _commit(cred, tok["editor"], [{"delete": f"loja_catalogo_log/{uid}"}]) == 403, "o histórico não pode ser apagado"
        assert admin.get(f"{fs}/loja_catalogo_log/{uid}", timeout=30).json()["fields"]["resumo"]["stringValue"] == "teste automático"
        for nome, campos, tempo in (("quem é outra pessoa", registro(em["staff"]), True), ("ação inventada", registro(ed, acao="apagar_tudo"), True),
                                    ("resumo gigante", registro(ed, resumo="r" * 301), True), ("alvo gigante", registro(ed, alvo="a" * 41), True),
                                    ("campo extra", registro(ed, extra="x"), True), ("sem resumo", registro(ed, resumo=...), True),
                                    ("hora escolhida pela pessoa", registro(ed), False)):
            i = f"zz-teste-log-{uuid.uuid4().hex[:6]}"; ids.append(i)
            assert gravar(tok["editor"], i, campos, tempo) == 403, f"deveria recusar o registro: {nome}"
        i = f"zz-teste-log-{uuid.uuid4().hex[:6]}"; ids.append(i)
        assert gravar(tok["staff"], i, registro(em["staff"])) == 403, "quem só visualiza não registra alterações"
    finally:
        for i in ids:
            admin.delete(f"{fs}/loja_catalogo_log/{i}", timeout=30)
    print("\n[integração] histórico de alterações: só se acrescenta, validado e imutável")


def test_pagina_valida_e_excluida_pelo_editor(usuarios, cred, admin, fs):
    """Grava e apaga uma página REAL (p5) por alguns instantes: se o bot estiver no ar, ela aparece na loja do Discord
    nesse intervalo. Por isso só roda com TESTE_ESCREVE_PAGINA=1 (ex.: antes de abrir a loja ao público)."""
    import os
    if os.environ.get("TESTE_ESCREVE_PAGINA") != "1":
        pytest.skip("grava uma página real por instantes; rode com TESTE_ESCREVE_PAGINA=1")
    tok, em = usuarios["tokens"], usuarios["emails"]
    if admin.get(f"{fs}/loja_paginas/p5", timeout=30).status_code == 200:
        pytest.skip("a página p5 já existe")
    esc = {"update": {"name": "loja_paginas/p5", "fields": {"introducao": _valor("teste"), "ordem": _valor(99), "atualizado_por": _valor(em["editor"])}},
           "updateTransforms": [{"fieldPath": "atualizado_em", "setToServerValue": "REQUEST_TIME"}]}
    try:
        assert _commit(cred, tok["editor"], [esc]) == 200
    finally:
        assert _commit(cred, tok["editor"], [{"delete": "loja_paginas/p5"}]) == 200
        admin.delete(f"{fs}/loja_paginas/p5", timeout=30)


def test_nada_dos_testes_ficou_nas_colecoes_reais(admin, fs):
    docs = admin.get(f"{fs}/loja_staff", timeout=30).json().get("documents") or []
    assert not [d for d in docs if "example.com" in d["name"]]
    for colecao in ("loja_itens", "loja_catalogo_log", "loja_pedidos", "loja_paginas"):
        nomes = [d["name"] for d in (admin.get(f"{fs}/{colecao}?pageSize=300", timeout=30).json().get("documents") or [])]
        assert not [n for n in nomes if "/zz-" in n], f"sobrou lixo de teste em {colecao}: {nomes}"
