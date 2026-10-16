"""O painel de verdade, no Chrome, logado como um EDITOR de teste, contra o Firebase REAL.

Cria, edita e exclui um item OCULTO pela interface e confere, pelo lado do servidor, o que chegou ao banco.
Também loga como quem só visualiza e tenta burlar a interface gravando direto no banco (precisa ser recusado).

Só roda com RUN_INTEGRATION=1. O item de teste fica oculto (nunca aparece na loja do Discord) e tudo é apagado no fim,
inclusive as linhas do histórico. Os usuários de teste do login são criados e apagados pelo fixture `usuarios`.
"""
import json
import subprocess
import uuid

import pytest
import requests

from conftest import RAIZ
from test_editor_catalogo import AJUDANTES
from test_integracao import _h, _valor, admin, cred, fs, usuarios  # noqa: F401  (fixtures e ajudantes)
from test_painel_web import ESPERAR, site  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.browser]

ENTRAR = r"""
const base = 'https://www.gstatic.com/firebasejs/11.10.0/';
const { getApp } = await import(base + 'firebase-app.js');
const A = await import(base + 'firebase-auth.js');
const F = await import(base + 'firebase-firestore.js');
await esperar(() => q('#stateBox button'), 30000);                       // tela de login
await A.signInWithCustomToken(A.getAuth(getApp()), __TOKEN__);
await esperar(() => !q('#tabs').hidden, 30000);
q('#tabLoja').click();
"""


def rodar(corpo, token, site_url, tmp_path, nome):
    arq = tmp_path / nome
    arq.write_text(ESPERAR + AJUDANTES + ENTRAR.replace("__TOKEN__", json.dumps(token)) + corpo, encoding="utf-8")
    saida = subprocess.run(["node", str(RAIZ / "tests" / "browser" / "cdp_eval.mjs"), site_url, str(arq), "1280", "900"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert saida.stdout.strip(), saida.stderr[-500:]
    r = json.loads(saida.stdout.strip().splitlines()[-1])
    assert not r["excecoes"] and not r["dialogos"], r
    return r["resultado"]


def _apagar_historico_de(admin, cred, fs, email):
    consulta = {"structuredQuery": {"from": [{"collectionId": "loja_catalogo_log"}],
                                    "where": {"fieldFilter": {"field": {"fieldPath": "quem"}, "op": "EQUAL", "value": {"stringValue": email}}}}}
    base = f"https://firestore.googleapis.com/v1/projects/{cred['project_id']}/databases/(default)/documents:runQuery"
    for linha in admin.post(base, json=consulta, timeout=30).json():
        if "document" in linha:
            admin.delete(f"https://firestore.googleapis.com/v1/{linha['document']['name']}", timeout=30)


def _campo(doc, nome):
    f = doc["fields"][nome]
    return next(iter(f.values()))


def test_editor_cria_edita_e_exclui_pela_interface_no_banco_real(usuarios, cred, admin, fs, site, tmp_path):
    token, email = usuarios["custom"]["editor"], usuarios["emails"]["editor"]
    codigo = f"zz-e2e-{uuid.uuid4().hex[:6]}"
    nome = f"ZZ E2E {codigo[-6:]}"
    try:
        # ---- 1) entra, vê o catálogo real e cria um item OCULTO
        r1 = rodar(f"""
          await esperar(() => q('#novoItem'), 30000);                    // só quem pode editar tem este botão
          const paginas = qa('[data-pagina]').map(s => s.dataset.pagina);
          const itensReais = qa('#appLoja tbody tr[data-id]').length;
          await abrirNovo('p3');
          const f = q('form', itemDlg());
          set(f, 'nome', {json.dumps(nome)}); set(f, 'preco', '12,50'); set(f, 'descricao', 'teste automático (E2E)'); f.elements.ativo.checked = false;
          f.requestSubmit();
          await esperar(() => !itemDlg().open || qa('[data-erros] li', itemDlg()).length, 30000);
          const erros = qa('[data-erros] li', itemDlg()).map(e => e.textContent);
          await esperar(() => q('tr[data-id={codigo}]'), 30000);       // a mudança volta ao vivo, pelo próprio banco
          const linha = linhas('p3').find(i => i.id === '{codigo}');
          return {{ paginas, itensReais, erros, linha, historico: historico().slice(0, 2) }};""", token, site, tmp_path, "e2e1.js")
        assert r1["paginas"] == ["p1", "p2", "p3"] and r1["itensReais"] >= 12                  # viu o catálogo real
        assert r1["erros"] == [] and r1["linha"]["preco"] == "R$ 12,50" and r1["linha"]["oculto"] is True
        doc = admin.get(f"{fs}/loja_itens/{codigo}", timeout=30).json()
        assert _campo(doc, "preco_centavos") == "1250" and _campo(doc, "ativo") is False and _campo(doc, "atualizado_por") == email
        assert _campo(doc, "pagina") == "p3" and _campo(doc, "nome") == nome and "atualizado_em" in doc["fields"]
        assert "timestampValue" in doc["fields"]["atualizado_em"]                              # carimbo de hora DO SERVIDOR

        # ---- 2) edita o preço pela interface
        r2 = rodar(f"""
          await esperar(() => q('tr[data-id={codigo}]'), 30000);
          await abrirEditar('{codigo}');
          const f = q('form', itemDlg()); set(f, 'preco', '6'); set(f, 'descricao', 'editado no E2E');
          f.requestSubmit();
          await esperar(() => !itemDlg().open, 30000);
          await esperar(() => linhas('p3').find(i => i.id === '{codigo}')?.preco === 'R$ 6,00', 30000);
          return {{ historico: historico().slice(0, 3) }};""", token, site, tmp_path, "e2e2.js")
        doc = admin.get(f"{fs}/loja_itens/{codigo}", timeout=30).json()
        assert _campo(doc, "preco_centavos") == "600" and _campo(doc, "descricao") == "editado no E2E" and _campo(doc, "ativo") is False
        assert any("preço R$ 12,50 → R$ 6,00" in h for h in r2["historico"])

        # ---- 3) exclui pela interface (com confirmação)
        r3 = rodar(f"""
          await esperar(() => q('tr[data-id={codigo}]'), 30000);
          q('tr[data-id={codigo}] button[aria-label^="Excluir"]').click(); await sleep(60);
          q('[data-ok]', confDlg()).click();
          await esperar(() => toasts().some(t => t.includes('excluído')), 30000);   // o aviso só sai DEPOIS de o servidor confirmar
          await esperar(() => !q('tr[data-id={codigo}]'), 30000);
          return {{ historico: historico().slice(0, 4) }};""", token, site, tmp_path, "e2e3.js")
        assert admin.get(f"{fs}/loja_itens/{codigo}", timeout=30).status_code == 404
        assert any("Excluiu" in h and nome in h for h in r3["historico"])

        # ---- o histórico no banco registrou as três ações, com o e-mail de quem fez
        logs = []
        consulta = {"structuredQuery": {"from": [{"collectionId": "loja_catalogo_log"}], "where": {"fieldFilter": {"field": {"fieldPath": "alvo"}, "op": "EQUAL", "value": {"stringValue": codigo}}}}}
        for linha in admin.post(f"https://firestore.googleapis.com/v1/projects/{cred['project_id']}/databases/(default)/documents:runQuery", json=consulta, timeout=30).json():
            if "document" in linha:
                logs.append(linha["document"])
        assert sorted(_campo(d, "acao") for d in logs) == ["criar", "editar", "excluir"] and {_campo(d, "quem") for d in logs} == {email}
        print("\n[integração] painel real: editor criou, editou o preço e excluiu um item pela interface; o banco e o histórico confirmam")
    finally:
        admin.delete(f"{fs}/loja_itens/{codigo}", timeout=30)
        _apagar_historico_de(admin, cred, fs, email)


def test_quem_so_visualiza_nao_edita_nem_burlando_a_interface(usuarios, cred, admin, fs, site, tmp_path):
    token, email = usuarios["custom"]["staff"], usuarios["emails"]["staff"]
    real = admin.get(f"{fs}/loja_itens/escolhidos", timeout=30).json()
    preco_real = _campo(real, "preco_centavos")
    r = rodar(f"""
      await esperar(() => q('#appLoja .aviso'), 30000);                      // o aviso de "só visualização"
      await esperar(() => qa('#appLoja tbody tr[data-id]').length >= 12, 30000);
      const db = F.getFirestore(getApp());
      const tentar = async (fn) => {{ try {{ await fn(); return 'GRAVOU!'; }} catch (e) {{ return e.code; }} }};
      const valido = {{ nome: 'x', preco_centavos: 1, descricao: '', pagina: 'p1', ordem: 1, ativo: true, atualizado_por: {json.dumps(email)}, atualizado_em: F.serverTimestamp() }};
      return {{
        aviso: q('#appLoja .aviso').textContent, botoes: !!q('#novoItem') || !!q('#appLoja button[aria-label^="Editar"]'),
        itens: qa('#appLoja tbody tr[data-id]').length,
        criar: await tentar(() => F.setDoc(F.doc(db, 'loja_itens', 'zz-bypass'), valido)),
        alterarPreco: await tentar(() => F.setDoc(F.doc(db, 'loja_itens', 'escolhidos'), {{ ...valido, nome: 'Escolhidos dos Mares', preco_centavos: 1 }})),
        apagar: await tentar(() => F.deleteDoc(F.doc(db, 'loja_itens', 'escolhidos'))),
        pedido: await tentar(() => F.setDoc(F.doc(db, 'loja_pedidos', 'zz-bypass'), {{ total_centavos: 1 }})),
        promover: await tentar(() => F.setDoc(F.doc(db, 'loja_staff', {json.dumps(email)}), {{ email: {json.dumps(email)}, editor: true }})),
        historico: await tentar(() => F.addDoc(F.collection(db, 'loja_catalogo_log'), {{ quando: F.serverTimestamp(), quem: {json.dumps(email)}, acao: 'editar', alvo: 'x', resumo: 'x' }})),
        leMeuRegistro: await tentar(() => F.getDoc(F.doc(db, 'loja_staff', {json.dumps(email)}))) === 'GRAVOU!',   // (leitura ok: o "tentar" devolve GRAVOU! quando não dá erro)
      }};""", token, site, tmp_path, "viewer.js")
    assert "não editar" in r["aviso"] and r["botoes"] is False and r["itens"] >= 12             # vê o catálogo, sem botões de edição
    assert {k: r[k] for k in ("criar", "alterarPreco", "apagar", "pedido", "promover", "historico")} == {k: "permission-denied" for k in ("criar", "alterarPreco", "apagar", "pedido", "promover", "historico")}
    assert r["leMeuRegistro"] is True                                                           # a leitura do próprio registro funciona
    real2 = admin.get(f"{fs}/loja_itens/escolhidos", timeout=30).json()
    assert _campo(real2, "preco_centavos") == preco_real                                       # o preço real não mudou
    assert admin.get(f"{fs}/loja_itens/zz-bypass", timeout=30).status_code == 404
    print("\n[integração] painel real: quem só visualiza não vê botões de edição e todas as tentativas de burlar a interface foram recusadas")
