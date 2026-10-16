"""Aba "Loja" do painel (editor do catálogo) no Chrome de verdade, em modo demonstração (não toca na loja real)."""
import json

import pytest

import catalogo as cat
from test_painel_web import abrir, site  # noqa: F401  (o fixture `site` serve o painel numa porta local)

pytestmark = pytest.mark.browser

# ajudantes de JavaScript usados em todos os testes
AJUDANTES = r"""
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const q = (s, r = document) => r.querySelector(s);
const qa = (s, r = document) => [...r.querySelectorAll(s)];
const dlg = (t) => q(`dialog[aria-labelledby=${t}]`);
const itemDlg = () => dlg('tituloItem'), paginaDlg = () => dlg('tituloPagina'), confDlg = () => dlg('tituloConfirmar');
const linhas = (pid) => qa(`[data-pagina=${pid}] tbody tr[data-id]`).map(l => ({
  id: l.dataset.id, nome: q('.it-nome', l).textContent, preco: q('td[data-label=Preço]', l).textContent.trim(),
  oculto: l.classList.contains('oculto'), marcado: q('input[type=checkbox]', l).checked }));
const historico = () => qa('.ed-historico li').map(l => l.textContent);
const toasts = () => qa('.toast').map(t => t.textContent);
const titulo = () => q('.ed-topo p').textContent;
const set = (f, nome, v) => { const c = f.elements[nome]; c.value = v; c.dispatchEvent(new Event('input')); };
async function salvarItem(d) {
  const f = q('form', itemDlg());
  for (const [k, v] of Object.entries(d)) { if (k === 'ativo') f.elements.ativo.checked = v; else set(f, k, v); }
  f.requestSubmit(); await sleep(80);
  return { aberto: itemDlg().open, erros: qa('[data-erros] li', itemDlg()).map(e => e.textContent) };
}
const abrirNovo = async (pid) => { (pid ? q(`[data-pagina=${pid}] button[aria-label^="Novo item"]`) : q('#novoItem')).click(); await sleep(30); };
const abrirEditar = async (id) => { q(`tr[data-id=${id}] button[aria-label^="Editar"]`).click(); await sleep(30); };
"""
PRE = AJUDANTES + r"""
await esperar(() => q('#tabLoja') && !q('#tabs').hidden);
q('#tabLoja').click();
await esperar(() => q('#appLoja [data-pagina]'));
"""


def js(corpo, tmp_path, site_url, hash_="#demo"):
    r = abrir(site_url + hash_, PRE + corpo, tmp_path)
    assert not r["excecoes"] and not r["dialogos"], r
    return r["resultado"]


def test_aba_mostra_paginas_itens_precos_e_historico(site, tmp_path):
    r = js("""return { tabs: qa('[role=tab]').map(t => t.textContent), sel: qa('[role=tab]').map(t => t.getAttribute('aria-selected')),
      p1: linhas('p1'), p2: linhas('p2'), titulo: titulo(), historico: historico(), compras: !q('#app').hidden };""", tmp_path, site)
    assert r["tabs"] == ["🧾 Compras", "🛠️ Loja"] and r["sel"] == ["false", "true"] and r["compras"] is False
    assert [i["nome"] for i in r["p1"]] == ["Escolhidos dos Mares", "Mudança Pequena", "Renascimento"]
    assert [i["preco"] for i in r["p1"]] == ["R$ 5,00", "R$ 5,00", "R$ 15,00"]
    assert r["p2"][1]["oculto"] is True and r["p2"][1]["marcado"] is False and r["p2"][0]["marcado"] is True
    assert "4 de 25 itens visíveis · 2 de 5 páginas" in r["titulo"] and "ocultado da loja" in r["historico"][0]


def test_trocar_de_aba_mostra_so_uma_secao(site, tmp_path):
    r = js("""const a = { compras: q('#app').hidden, loja: q('#appLoja').hidden };
      q('#tabCompras').click(); await sleep(50);
      const b = { compras: q('#app').hidden, loja: q('#appLoja').hidden, linhas: qa('#rows tr').length, grafico: q('#chart').getBoundingClientRect().width > 100 };
      return { a, b };""", tmp_path, site)
    assert r["a"] == {"compras": True, "loja": False}
    assert r["b"] == {"compras": False, "loja": True, "linhas": 6, "grafico": True}


def test_criar_item_aparece_na_pagina_com_preco_historico_e_aviso(site, tmp_path):
    r = js("""await abrirNovo('p2');
      const estado = await salvarItem({ nome: 'Poção de Teste', preco: '12,5', descricao: 'Linha 1\\nLinha 2', ativo: true });
      await sleep(50);
      return { estado, p2: linhas('p2'), historico: historico()[0], toasts: toasts(), titulo: titulo() };""", tmp_path, site)
    assert r["estado"] == {"aberto": False, "erros": []}
    novo = r["p2"][-1]
    assert novo["nome"] == "Poção de Teste" and novo["preco"] == "R$ 12,50" and novo["id"] == "pocao-de-teste" and novo["marcado"] is True
    assert "Criou" in r["historico"] and "R$ 12,50" in r["historico"] and any("adicionado" in t for t in r["toasts"])
    assert "5 de 25 itens visíveis" in r["titulo"]


@pytest.mark.parametrize("preco,centavos", [("5", 500), ("5,5", 550), ("5.50", 550), ("12,34", 1234), ("R$ 1.234,56", 123456), (" 7 ", 700), ("0,01", 1), ("10000", 1000000)])
def test_formatos_de_preco_aceitos_no_formulario(site, tmp_path, preco, centavos):
    r = js(f"""await abrirNovo('p1'); const e = await salvarItem({{ nome: 'Preço', preco: {json.dumps(preco)} }}); await sleep(50);
      return {{ e, preco: linhas('p1').at(-1).preco }};""", tmp_path, site)
    assert r["e"]["erros"] == [] and r["preco"] == f"R$ {centavos // 100},{centavos % 100:02d}"


@pytest.mark.parametrize("preco", ["", "abc", "0", "0,001", "-5", "10000,01", "10001", "1.234.567", "5,50,5", "R$", "1e3", "5 reais"])
def test_precos_invalidos_sao_recusados_e_nada_e_criado(site, tmp_path, preco):
    r = js(f"""await abrirNovo('p1'); const e = await salvarItem({{ nome: 'Item', preco: {json.dumps(preco)} }});
      return {{ e, total: linhas('p1').length, aberto: itemDlg().open }};""", tmp_path, site)
    assert r["e"]["aberto"] is True and any("Preço inválido" in x for x in r["e"]["erros"]) and r["total"] == 3


def test_validacoes_de_nome_e_descricao(site, tmp_path):
    r = js("""await abrirNovo('p1');
      const vazio = await salvarItem({ nome: '   ', preco: '5' });
      const nomeGrande = await salvarItem({ nome: 'n'.repeat(61), preco: '5' });
      const descGrande = await salvarItem({ nome: 'Ok', preco: '5', descricao: 'd'.repeat(1501) });
      return { vazio, nomeGrande, descGrande, total: linhas('p1').length };""", tmp_path, site)
    assert any("nome" in e for e in r["vazio"]["erros"]) and any("60" in e for e in r["nomeGrande"]["erros"])
    assert any("1500" in e for e in r["descGrande"]["erros"]) and r["total"] == 3


def test_pagina_nao_pode_passar_do_limite_de_caracteres_do_discord(site, tmp_path):
    r = js("""const textos = [];
      for (let i = 0; i < 4; i++) { await abrirNovo('p1'); textos.push(await salvarItem({ nome: 'Grande ' + i, preco: '5', descricao: 'x'.repeat(1400) })); }
      return { textos, total: linhas('p1').length, contador: q('[data-pagina=p1] .contador').textContent };""", tmp_path, site)
    assert [bool(t["erros"]) for t in r["textos"]] == [False, False, True, True]               # 304 + 2×~1450 cabem; o 3º estoura
    assert "4000" in r["textos"][2]["erros"][0] and r["total"] == 5


def test_editar_preco_nome_e_descricao_e_historico_mostra_o_antes_e_depois(site, tmp_path):
    r = js("""await abrirEditar('escolhidos');
      const e = await salvarItem({ preco: '6', nome: 'Escolhidos Renomeado', descricao: 'Nova descrição' }); await sleep(50);
      return { e, linha: linhas('p1')[0], historico: historico()[0] };""", tmp_path, site)
    assert r["e"] == {"aberto": False, "erros": []}
    assert r["linha"]["preco"] == "R$ 6,00" and r["linha"]["nome"] == "Escolhidos Renomeado" and r["linha"]["id"] == "escolhidos"    # o código do item não muda
    assert 'nome "Escolhidos dos Mares" → "Escolhidos Renomeado"' in r["historico"] and "preço R$ 5,00 → R$ 6,00" in r["historico"] and "descrição alterada" in r["historico"]


def test_mover_item_para_outra_pagina(site, tmp_path):
    r = js("""await abrirEditar('mudanca'); const e = await salvarItem({ pagina: 'p2' }); await sleep(50);
      return { e, p1: linhas('p1').map(i => i.id), p2: linhas('p2').map(i => i.id), historico: historico()[0] };""", tmp_path, site)
    assert r["p1"] == ["escolhidos", "renascimento"] and r["p2"] == ["manobra", "aviso", "mudanca"] and "movido para a página 2" in r["historico"]


def test_ocultar_e_reexibir_pelo_interruptor(site, tmp_path):
    r = js("""const cx = () => q('tr[data-id=escolhidos] input[type=checkbox]');
      cx().checked = false; cx().dispatchEvent(new Event('change')); await sleep(80);
      const oculto = { linha: linhas('p1')[0], titulo: titulo(), historico: historico()[0] };
      cx().checked = true; cx().dispatchEvent(new Event('change')); await sleep(80);
      return { oculto, volta: linhas('p1')[0], titulo2: titulo(), historico2: historico()[0] };""", tmp_path, site)
    assert r["oculto"]["linha"]["oculto"] is True and "3 de 25" in r["oculto"]["titulo"] and "ocultado da loja" in r["oculto"]["historico"]
    assert r["volta"]["oculto"] is False and "4 de 25" in r["titulo2"] and "voltou a aparecer" in r["historico2"]


def test_reordenar_com_as_setas(site, tmp_path):
    r = js("""const ordem = () => linhas('p1').map(i => i.id);
      const antes = ordem();
      q('tr[data-id=escolhidos] button[aria-label^="Descer"]').click(); await sleep(80);
      const depois = ordem();
      q('tr[data-id=renascimento] button[aria-label^="Subir"]').click(); await sleep(80);
      return { antes, depois, fim: ordem(),
        subirPrimeiro: q('tr[data-id]:first-child button[aria-label^="Subir"]', q('[data-pagina=p1]')).disabled,
        descerUltimo: qa('tr[data-id]', q('[data-pagina=p1]')).at(-1).querySelector('button[aria-label^="Descer"]').disabled };""", tmp_path, site)
    assert r["antes"] == ["escolhidos", "mudanca", "renascimento"] and r["depois"] == ["mudanca", "escolhidos", "renascimento"]
    assert r["fim"] == ["mudanca", "renascimento", "escolhidos"] and r["subirPrimeiro"] is True and r["descerUltimo"] is True


def test_excluir_item_pede_confirmacao_e_cancelar_nao_apaga(site, tmp_path):
    r = js("""q('tr[data-id=mudanca] button[aria-label^="Excluir"]').click(); await sleep(40);
      const aberto = confDlg().open, texto = q('p', confDlg()).textContent;
      q('[data-cancelar]', confDlg()).click(); await sleep(40);
      const depoisCancelar = linhas('p1').length;
      q('tr[data-id=mudanca] button[aria-label^="Excluir"]').click(); await sleep(40);
      q('[data-ok]', confDlg()).click(); await sleep(100);
      return { aberto, texto, depoisCancelar, depoisExcluir: linhas('p1').map(i => i.id), historico: historico()[0], toasts: toasts() };""", tmp_path, site)
    assert r["aberto"] is True and "Mudança Pequena" in r["texto"] and "histórico" in r["texto"] and r["depoisCancelar"] == 3
    assert r["depoisExcluir"] == ["escolhidos", "renascimento"] and "Excluiu" in r["historico"] and any("excluído" in t for t in r["toasts"])


def test_paginas_criar_editar_texto_e_excluir_vazia(site, tmp_path):
    r = js("""q('#novaPagina').click(); await sleep(80);
      const nova = qa('[data-pagina]').map(s => s.dataset.pagina);
      const excluirComItens = q('[data-pagina=p1] button[aria-label^="Excluir a página"]').disabled;
      const excluirVazia = q('[data-pagina=p3] button[aria-label^="Excluir a página"]').disabled;
      q('[data-pagina=p3] button[aria-label^="Editar o texto"]').click(); await sleep(40);
      const f = q('form', paginaDlg()); f.elements.introducao.value = 'Em breve: **novidades**!'; f.elements.introducao.dispatchEvent(new Event('input'));
      const cont = q('[data-cont]', paginaDlg()).textContent;
      f.requestSubmit(); await sleep(80);
      const intro = q('[data-pagina=p3] .intro').textContent;
      q('[data-pagina=p3] button[aria-label^="Excluir a página"]').click(); await sleep(40); q('[data-ok]', confDlg()).click(); await sleep(80);
      return { nova, excluirComItens, excluirVazia, cont, intro, depois: qa('[data-pagina]').map(s => s.dataset.pagina), historico: historico().slice(0, 3) };""", tmp_path, site)
    assert r["nova"] == ["p1", "p2", "p3"] and r["excluirComItens"] is True and r["excluirVazia"] is False
    assert "página inteira na loja" in r["cont"] and r["intro"] == "Em breve: **novidades**!" and r["depois"] == ["p1", "p2"]
    assert any("Excluiu a página 3" in h for h in r["historico"]) and any("Editou o texto da página 3" in h for h in r["historico"])


def test_limite_de_cinco_paginas(site, tmp_path):
    r = js("""for (let i = 0; i < 4; i++) { q('#novaPagina').click(); await sleep(80); }
      return { paginas: qa('[data-pagina]').length, erros: toasts().filter(t => t.includes('no máximo 5')).length };""", tmp_path, site)
    assert r["paginas"] == 5 and r["erros"] >= 1


def test_limite_de_25_itens_visiveis(site, tmp_path):
    """A demonstração começa com 4 itens visíveis: 21 novos chegam a 25 e o 22º precisa ser recusado."""
    r = js("""const resultados = [];
      for (let i = 0; i < 22; i++) { await abrirNovo('p' + (1 + i % 2)); resultados.push(await salvarItem({ nome: 'Extra ' + i, preco: '1', descricao: 'curto' })); }
      const fechar = itemDlg().open; if (fechar) q('[data-cancelar]', itemDlg()).click();
      return { resultados, visiveis: titulo(), total: qa('#appLoja tbody tr[data-id]').length };""", tmp_path, site)
    assert all(not x["erros"] and not x["aberto"] for x in r["resultados"][:21])
    ultimo = r["resultados"][21]
    assert ultimo["aberto"] is True and any("no máximo 25 itens" in e for e in ultimo["erros"])
    assert "25 de 25 itens visíveis" in r["visiveis"] and r["total"] == 5 + 21        # 5 da demo (1 oculto) + 21 criados


def test_codigo_do_item_e_unico_mesmo_com_nomes_iguais(site, tmp_path):
    r = js("""const ids = [];
      for (let i = 0; i < 3; i++) { await abrirNovo('p1'); await salvarItem({ nome: 'Repetido', preco: '5' }); }
      return { ids: linhas('p1').map(i => i.id).filter(i => i.startsWith('repetido')) };""", tmp_path, site)
    assert r["ids"] == ["repetido", "repetido-2", "repetido-3"]


def test_previa_mostra_exatamente_o_texto_que_vai_para_o_discord(site, tmp_path):
    r = js("""await abrirNovo('p1');
      const f = q('form', itemDlg()); set(f, 'nome', 'Poção'); set(f, 'preco', '12,5'); set(f, 'descricao', 'Cura **muito**.');
      return { previa: q('[data-previa]', itemDlg()).textContent, contador: q('[data-cont-desc]', itemDlg()).textContent };""", tmp_path, site)
    assert r["previa"] == "## 🔷 Poção - R$ 12,50\nCura **muito**." and r["contador"] == "15/1500"


def test_nome_e_descricao_maliciosos_viram_texto_e_nao_executam(site, tmp_path):
    r = js("""await abrirNovo('p1');
      await salvarItem({ nome: '<img src=x onerror=alert(1)>', preco: '5', descricao: '<script>alert(2)</script> <b>x</b>' }); await sleep(80);
      return { imgs: qa('#appLoja img').length, scripts: qa('#appLoja script').length, negritos: qa('#appLoja b').length, onerror: qa('#appLoja [onerror]').length,
               nome: linhas('p1').at(-1).nome, historico: historico()[0] };""", tmp_path, site)
    assert r["imgs"] == 0 and r["scripts"] == 0 and r["negritos"] == 0 and r["onerror"] == 0
    assert r["nome"] == "<img src=x onerror=alert(1)>" and "<img src=x onerror=alert(1)>" in r["historico"]


def test_quem_so_visualiza_nao_tem_botoes_de_edicao(site, tmp_path):
    r = js("""return { novo: !!q('#novoItem'), novaPagina: !!q('#novaPagina'), botoesEditar: qa('#appLoja button').filter(b => /Editar|Excluir|Subir|Descer|Item aqui|Texto da página/.test(b.textContent + (b.getAttribute('aria-label') || ''))).length,
      interruptores: qa('#appLoja tbody input[type=checkbox]').map(c => c.disabled), aviso: q('#appLoja .aviso')?.textContent, itens: linhas('p1').length };""", tmp_path, site, "#demo&visualizar")
    assert r["novo"] is False and r["novaPagina"] is False and r["botoesEditar"] == 0 and all(r["interruptores"]) and r["itens"] == 3
    assert "não editar" in r["aviso"] and "buggy!loja_acesso editor" in r["aviso"]


def test_catalogo_vazio_e_nova_pagina_sem_itens(site, tmp_path):
    r = js("""for (const id of ['mudanca', 'escolhidos', 'renascimento']) {
        q(`tr[data-id=${id}] button[aria-label^="Excluir"]`).click(); await sleep(40); q('[data-ok]', confDlg()).click(); await sleep(80); }
      return { p1: qa('[data-pagina=p1] tbody td.vazio').map(t => t.textContent), pode: !q('[data-pagina=p1] button[aria-label^="Excluir a página"]').disabled };""", tmp_path, site)
    assert r["p1"] == ["Nenhum item nesta página."] and r["pode"] is True


def test_mobile_aba_sem_rolagem_horizontal_e_dialogo_cabe_na_tela(site, tmp_path):
    corpo = """await abrirEditar('escolhidos');
      const d = itemDlg().getBoundingClientRect(), el = document.documentElement;
      return { rolagem: el.scrollWidth, janela: innerWidth, dialogoDentro: d.left >= 0 && d.right <= innerWidth + 1, cabecalhoOculto: getComputedStyle(q('#appLoja thead')).display === 'none' };"""
    for largura in (360, 390):
        arq = tmp_path / f"m{largura}.js"
        arq.write_text("""const esperar = async (c, ms = 8000) => { const t0 = performance.now(); while (!c()) { if (performance.now() - t0 > ms) throw new Error('timeout'); await new Promise(r => setTimeout(r, 25)); } };""" + PRE + corpo, encoding="utf-8")
        import subprocess
        from conftest import RAIZ
        out = subprocess.run(["node", str(RAIZ / "tests" / "browser" / "cdp_eval.mjs"), site + "#demo", str(arq), str(largura), "900"], capture_output=True, text=True, encoding="utf-8", timeout=90)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        assert not r["excecoes"], r["excecoes"]
        res = r["resultado"]
        assert res["rolagem"] <= res["janela"] and res["dialogoDentro"] and res["cabecalhoOculto"], (largura, res)


# ---------------------------------------------------------------- as funções puras do editor
def test_parse_preco_e_slug_do_editor(site, tmp_path):
    r = js("""const m = await import('./editor.js');
      const precos = ['5', '5,5', '5.50', 'R$ 1.234,56', '', 'abc', '0', '0,004', '10000', '10000,01', '1.234.567', '-5', '1e3', '1,5,5'].map(m.parsePreco);
      const slugs = [['Poção de Teste', []], ['  ', []], ['ÇÃO!!', []], ['x'.repeat(80), []], ['Repetido', ['repetido', 'repetido-2']], ['🤡🤡', []]].map(([n, e]) => m.slugId(n, new Set(e)));
      return { precos, slugs, fmt: [1, 99, 100, 12345].map(m.fmtPreco) };""", tmp_path, site)
    assert r["precos"] == [500, 550, 550, 123456, None, None, None, None, 1000000, None, None, None, None, None]
    assert r["slugs"] == ["pocao-de-teste", "item", "cao", "x" * 30, "repetido-3", "item"]
    assert r["fmt"] == ["R$ 0,01", "R$ 0,99", "R$ 1,00", "R$ 123,45"]


def test_texto_da_pagina_do_editor_e_identico_ao_do_bot(site, tmp_path):
    """O contador de caracteres do painel precisa bater com o texto que o bot realmente envia ao Discord."""
    casos = [
        ("", []),
        ("Só introdução", []),
        ("# __Título__ <:berry:123>\nTexto", [{"nome": "A", "preco": 500, "descricao": ""}]),
        ("", [{"nome": "Poção 🤡", "preco": 12345, "descricao": "linha 1\nlinha 2\n\n⚠️ **aviso**"}, {"nome": "B", "preco": 1, "descricao": "x"}]),
        ("intro", [{"nome": "Sem descrição", "preco": 100, "descricao": ""}, {"nome": "Com", "preco": 99, "descricao": "d"}]),
    ]
    esperado = [cat.render_descricao(intro, [dict(i) for i in itens]) for intro, itens in casos]
    entrada = [{"intro": intro, "itens": [{"nome": i["nome"], "preco_centavos": i["preco"], "descricao": i["descricao"]} for i in itens]} for intro, itens in casos]
    r = js("const m = await import('./editor.js'); return __CASOS__.map(c => m.textoPagina(c.intro, c.itens));".replace("__CASOS__", json.dumps(entrada)), tmp_path, site)
    assert r == esperado
