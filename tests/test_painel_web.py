"""Painel web no Chrome de verdade: injeção de código, CSV, filtros, celular, acessibilidade e desempenho."""
import functools
import http.server
import json
import subprocess
import threading
from pathlib import Path

import pytest

from conftest import RAIZ

pytestmark = pytest.mark.browser
HELPER = RAIZ / "tests" / "browser" / "cdp_eval.mjs"

# espera (até ~8s) o painel desenhar pelo menos N linhas
ESPERAR = """
const esperar = async (cond, ms = 8000) => { const t0 = performance.now(); while (!cond()) { if (performance.now() - t0 > ms) throw new Error('timeout esperando a página'); await new Promise(r => setTimeout(r, 25)); } };
"""


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RAIZ / "dashboard"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


def abrir(url, js, tmp_path, largura=1280, altura=900, nome="t.js"):
    arq = tmp_path / nome
    arq.write_text(ESPERAR + js, encoding="utf-8")
    r = subprocess.run(["node", str(HELPER), url, str(arq), str(largura), str(altura)], capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert r.stdout.strip(), f"sem saída do Chrome: {r.stderr[:500]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_demo_sem_erros_e_sem_dialogos(site, tmp_path):
    r = abrir(site + "#demo", "await esperar(() => document.querySelectorAll('#rows tr').length >= 6); return document.querySelectorAll('#rows tr').length;", tmp_path)
    assert r["resultado"] == 6 and not r["excecoes"] and not r["dialogos"]
    assert [e for e in r["erros"] if "favicon" not in e] == []


def test_nick_malicioso_vira_texto_e_nao_executa(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    const nomes = [...document.querySelectorAll('#rows .name')].map(n => n.textContent);
    return { imgs: document.querySelectorAll('#rows img, #cards img, #stateBox img').length,
             scripts: document.querySelectorAll('#rows script').length,
             onerror: document.querySelectorAll('[onerror]').length,
             nomes };
    """
    r = abrir(site + "#demo", js, tmp_path)["resultado"]
    assert r["imgs"] == 0 and r["scripts"] == 0 and r["onerror"] == 0
    assert any("<img src=x onerror=alert(1)>" in n for n in r["nomes"])          # aparece escrito, não interpretado


def test_nenhum_alert_foi_disparado_pelo_payload(site, tmp_path):
    r = abrir(site + "#demo", "await esperar(() => document.querySelectorAll('#rows tr').length >= 6); await new Promise(r => setTimeout(r, 800)); return 1;", tmp_path)
    assert r["dialogos"] == []


def test_csv_protegido_contra_formula_e_bem_formado(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    let blob; URL.createObjectURL = (b) => { blob = b; return 'blob:teste'; };
    document.getElementById('csv').click();
    await esperar(() => blob);
    return { bom: [...new Uint8Array(await blob.slice(0, 3).arrayBuffer())], texto: await blob.text(), tipo: blob.type };
    """
    r = abrir(site + "#demo", js, tmp_path)["resultado"]
    assert r["bom"] == [0xEF, 0xBB, 0xBF] and r["tipo"].startswith("text/csv")      # UTF-8 com BOM: o Excel abre os acentos certos
    texto = r["texto"]
    assert texto.startswith('"ID da compra";')
    linhas = texto.split("\r\n")
    assert len(linhas) == 7                                                      # cabeçalho + 6 compras
    assert "\"'=SOMA(1+1)\"" in texto and "\"'=hack#0\"" in texto                # fórmulas neutralizadas
    celulas = [c for l in linhas[1:] for c in l.split('";"')]
    assert not any(c.lstrip('"').startswith(("=", "+", "@")) for c in celulas)
    assert linhas[1].startswith('"01";') and linhas[-1].startswith('"06";')      # do mais antigo ao mais novo


def test_busca_filtros_e_ordenacao(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    const linhas = () => [...document.querySelectorAll('#rows tr')];
    const ids = () => linhas().map(l => l.querySelector('.id')?.textContent);
    const digitar = (v) => { const q = document.getElementById('q'); q.value = v; q.dispatchEvent(new Event('input')); };
    const sel = (id, v) => { const s = document.getElementById(id); s.value = v; s.dispatchEvent(new Event('change')); };
    const out = { total: linhas().length, ordemInicial: ids() };
    digitar('zoro'); out.busca_nome = linhas().length;
    digitar('1100000000000000005'); out.busca_id_usuario = ids();
    digitar('lunariano'); out.busca_item = ids();
    digitar('#02'); out.busca_numero = ids();
    digitar('nao existe'); out.vazio = document.querySelector('#rows .empty')?.textContent;
    digitar('');
    sel('fStatus', 'cancelada'); out.cancelada = ids();
    sel('fStatus', 'aguardando_pagamento'); out.aguardando = ids();
    sel('fStatus', '');
    sel('fPeriodo', '1'); out.hoje = ids();
    sel('fPeriodo', '0');
    const th = [...document.querySelectorAll('th button')].find(b => b.textContent.startsWith('Total'));
    th.click(); out.porTotalDesc = ids(); th.click(); out.porTotalAsc = ids();
    out.contador = document.getElementById('count').textContent;
    return out;
    """
    r = abrir(site + "#demo", js, tmp_path)["resultado"]
    assert r["total"] == 6 and r["ordemInicial"] == ["#06", "#05", "#04", "#03", "#02", "#01"]
    assert r["busca_nome"] == 1 and r["busca_id_usuario"] == ["#05"] and r["busca_item"] == ["#05"] and r["busca_numero"] == ["#02"]
    assert "Nenhuma compra com esses filtros" in r["vazio"]
    assert r["cancelada"] == ["#02"] and r["aguardando"] == ["#06", "#05"] and r["hoje"] == ["#06", "#05"]
    assert r["porTotalDesc"][0] == "#03" and r["porTotalAsc"][-1] == "#03"       # #03 (R$ 40) é o maior
    assert "Mostrando 6 de 6" in r["contador"]


def test_resumo_bate_com_a_tabela(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    const num = (t) => Number(t.replace(/[^0-9,]/g, '').replace(',', '.'));
    const cards = Object.fromEntries([...document.querySelectorAll('.card')].map(c => [c.querySelector('.label').textContent.replace(/^\\S+\\s/, ''), [c.querySelector('.value').textContent, c.querySelector('.sub').textContent]]));
    const linhas = [...document.querySelectorAll('#rows tr')].map(l => ({ total: num(l.querySelector('td[data-label="Total"]').textContent), st: l.querySelector('.badge').className.replace('badge b-', '') }));
    const soma = (f) => linhas.filter(f).reduce((s, l) => s + l.total, 0);
    return { cards, confirmado: soma(l => l.st === 'paga' || l.st === 'entregue'), aguardando: soma(l => l.st === 'aguardando_pagamento'), n: linhas.length };
    """
    r = abrir(site + "#demo", js, tmp_path)["resultado"]
    brl = lambda t: float(t.replace("R$", "").replace("\xa0", "").replace(".", "").replace(",", ".").strip())
    assert brl(r["cards"]["Faturamento confirmado"][0]) == r["confirmado"]
    assert r["cards"]["Compras"][0] == str(r["n"])
    assert brl(r["cards"]["Aguardando pagamento"][1]) == r["aguardando"]


def test_celular_sem_rolagem_horizontal(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    const el = document.documentElement;
    return { rolagem: el.scrollWidth, janela: window.innerWidth, cabecalhoOculto: getComputedStyle(document.querySelector('thead')).display === 'none',
             cardsSaindo: [...document.querySelectorAll('.card, .chart, .tablebox, .filters')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).length };
    """
    for largura in (360, 390, 768):
        r = abrir(site + "#demo", js, tmp_path, largura=largura, altura=900)["resultado"]
        assert r["rolagem"] <= r["janela"] and r["cardsSaindo"] == 0, f"largura {largura}: {r}"
    assert abrir(site + "#demo", js, tmp_path, largura=390)["resultado"]["cabecalhoOculto"] is True


def test_acessibilidade_basica(site, tmp_path):
    js = """
    await esperar(() => document.querySelectorAll('#rows tr').length >= 6);
    const sem = (sel) => [...document.querySelectorAll(sel)].filter(e => !(e.textContent.trim() || e.getAttribute('aria-label') || e.getAttribute('title'))).length;
    const ids = [...document.querySelectorAll('[id]')].map(e => e.id);
    return { lang: document.documentElement.lang, botoesSemNome: sem('button'), inputsSemRotulo: [...document.querySelectorAll('input, select')].filter(e => !e.getAttribute('aria-label') && !(e.labels && e.labels.length)).length,
             idsDuplicados: ids.length - new Set(ids).size, titulo: document.title, svgRole: document.getElementById('chart').getAttribute('role'), viewport: !!document.querySelector('meta[name=viewport]') };
    """
    r = abrir(site + "#demo", js, tmp_path)["resultado"]
    assert r == {"lang": "pt-BR", "botoesSemNome": 0, "inputsSemRotulo": 0, "idsDuplicados": 0, "titulo": "Painel de Compras", "svgRole": "img", "viewport": True}


def test_sem_configuracao_o_painel_explica_o_que_fazer(tmp_path):
    """Copia o painel com a config de exemplo (COLE_AQUI) e confere a mensagem de ajuda."""
    import shutil
    pasta = tmp_path / "site"; shutil.copytree(RAIZ / "dashboard", pasta)
    (pasta / "firebase-config.js").write_text('export const firebaseConfig = {apiKey:"COLE_AQUI",authDomain:"COLE_AQUI",projectId:"COLE_AQUI",appId:"COLE_AQUI"};', encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pasta)); handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = abrir(f"http://127.0.0.1:{srv.server_address[1]}/index.html", "await esperar(() => document.querySelector('#stateBox h2')); return document.querySelector('#stateBox').innerText;", tmp_path)
        assert "Falta configurar o Firebase" in r["resultado"]
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- desempenho com muitas compras
@pytest.mark.carga
@pytest.mark.parametrize("n,limite_s", [(1000, 2), (5000, 3), (20000, 6)])
def test_desempenho_com_muitas_compras(site, tmp_path, n, limite_s):
    js = f"""
    await esperar(() => document.querySelectorAll('#rows tr').length >= 100, 30000);
    const tempoAteDesenhar = performance.now();
    const linhas = () => document.querySelectorAll('#rows tr').length;
    const q = document.getElementById('q');
    const medir = (f) => {{ const t = performance.now(); f(); return performance.now() - t; }};
    const inicial = linhas(), contador = document.getElementById('count').textContent;
    const mais = medir(() => document.getElementById('mais').click()); const depoisDeMais = linhas();
    const buscar = medir(() => {{ q.value = 'jogador 7'; q.dispatchEvent(new Event('input')); }});
    const limpar = medir(() => {{ q.value = ''; q.dispatchEvent(new Event('input')); }});
    const sel = document.getElementById('fStatus');
    const filtrar = medir(() => {{ sel.value = 'paga'; sel.dispatchEvent(new Event('change')); }});
    const ordenar = medir(() => [...document.querySelectorAll('th button')].find(b => b.textContent.startsWith('Total')).click());
    const card = document.querySelector('.card .value').textContent;
    return {{ inicial, depoisDeMais, contador, tempoAteDesenhar, mais, buscar, limpar, filtrar, ordenar, nos: document.getElementsByTagName('*').length, card,
             memoriaMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null }};
    """
    r = abrir(site + f"#demo={n}", js, tmp_path)
    m = r["resultado"]
    print(f"\n[carga/web] {n} compras: abre em {m['tempoAteDesenhar'] / 1000:.2f}s | mostrar mais {m['mais']:.0f} ms | busca {m['buscar']:.0f} ms | "
          f"limpar {m['limpar']:.0f} ms | filtro {m['filtrar']:.0f} ms | ordenar {m['ordenar']:.0f} ms | {m['nos']} nós | {m['memoriaMB']} MB")
    assert not r["excecoes"] and not r["dialogos"]
    assert m["inicial"] == 100 and m["depoisDeMais"] == 200                     # 100 por vez; "Mostrar mais" soma outras 100
    assert f"Mostrando 100 de {n}" in m["contador"]
    assert m["tempoAteDesenhar"] / 1000 < limite_s
    assert max(m["mais"], m["buscar"], m["limpar"], m["filtrar"], m["ordenar"]) < 1500


def test_mostrar_mais_resumo_e_csv_cobrem_todas_as_compras(site, tmp_path):
    js = r"""
    await esperar(() => document.querySelectorAll('#rows tr').length >= 100, 30000);
    const linhas = () => document.querySelectorAll('#rows tr').length;
    const out = { primeira: linhas(), compras: document.querySelectorAll('.card .value')[1].textContent, botao: document.getElementById('mais').textContent };
    let blob; URL.createObjectURL = (b) => { blob = b; return 'blob:x'; };
    document.getElementById('csv').click(); await esperar(() => blob);
    out.linhasCsv = (await blob.text()).split('\r\n').length - 1;
    for (let i = 0; i < 4; i++) document.getElementById('mais').click();
    out.depois = linhas(); out.botaoSumiu = document.getElementById('mais').hidden;
    const q = document.getElementById('q'); q.value = '#250'; q.dispatchEvent(new Event('input'));
    out.achouPorNumero = [...document.querySelectorAll('#rows .id')].map(e => e.textContent);
    return out;
    """
    r = abrir(site + "#demo=450", js, tmp_path)["resultado"]
    assert r["primeira"] == 100 and r["compras"] == "450" and "faltam 350" in r["botao"]
    assert r["linhasCsv"] == 450                                              # o CSV tem TODAS, não só as 100 visíveis
    assert r["depois"] == 450 and r["botaoSumiu"] is True
    assert r["achouPorNumero"] == ["#250"]


# ---------------------------------------------------------------- site real (precisa de internet)
@pytest.mark.integration
def test_site_real_mostra_a_tela_de_login(tmp_path):
    import re
    cfg = (RAIZ / "dashboard" / "firebase-config.js").read_text(encoding="utf-8")
    projeto = re.search(r'"projectId": "([^"]+)"', cfg).group(1)
    js = "await esperar(() => document.querySelector('#stateBox button'), 20000); return { estado: document.getElementById('stateBox').innerText, live: document.getElementById('liveText').textContent, appVisivel: !document.getElementById('app').hidden };"
    r = abrir(f"https://{projeto}.web.app/", js, tmp_path)
    assert "Acesso restrito" in r["resultado"]["estado"] and "Entrar com Google" in r["resultado"]["estado"]
    assert r["resultado"]["appVisivel"] is False and not r["excecoes"]
    assert [e for e in r["erros"] if "favicon" not in e] == []
