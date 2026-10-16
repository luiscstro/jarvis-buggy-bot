// Aba "Loja" do painel: editar itens (nome, preço, descrição), adicionar, remover, ocultar, reordenar e
// editar o texto de cada página. Tudo que vem do banco entra na página só como TEXTO (nunca como HTML).
//
// Dados: coleções loja_itens, loja_paginas e loja_catalogo_log. Quem grava é o navegador de quem tem a
// permissão de editor (veja firestore.rules); o bot enxerga as mudanças ao vivo e atualiza a loja do Discord.

export const LIMITES = { nome: 60, descricao: 1500, introducao: 1500, textoPagina: 4000, itens: 25, paginas: 5, precoMax: 1_000_000 };
const MARCADOR = "🔷";
const IDS_PAGINA = ["p1", "p2", "p3", "p4", "p5"];

const el = (tag, props = {}, ...filhos) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const f of filhos) n.append(f);
  return n;
};

// ---------------------------------------------------------------- funções puras (testáveis)
export const fmtPreco = (c) => `R$ ${Math.floor(c / 100)},${String(c % 100).padStart(2, "0")}`;

/** "5", "5,5", "5.50", "R$ 1.234,56" -> centavos (inteiro) ou null se for inválido/fora do limite. */
export function parsePreco(texto) {
  let t = String(texto ?? "").replace(/R\$/gi, "").replace(/\s/g, "");
  if (!/^[0-9.,]+$/.test(t) || t === "") return null;
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");      // 1.234,56 -> 1234.56
  else if ((t.match(/\./g) || []).length > 1) return null;              // 1.234.567 é ambíguo
  const v = Number(t);
  if (!Number.isFinite(v)) return null;
  const c = Math.round(v * 100);
  return c >= 1 && c <= LIMITES.precoMax ? c : null;
}

/** Código do item (id do documento) a partir do nome: minúsculas, sem acento, único. */
export function slugId(nome, existentes) {
  let base = String(nome).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 30) || "item";
  let id = base, n = 2;
  while (existentes.has(id)) id = `${base}-${n++}`.slice(0, 40);
  return id;
}

/** Texto da página como o bot monta no Discord (introdução + um bloco por item visível). */
export function textoPagina(introducao, itensVisiveis) {
  const partes = [];
  if ((introducao || "").trim()) partes.push(introducao.trim());
  for (const i of itensVisiveis) partes.push(`## ${MARCADOR} ${i.nome} - ${fmtPreco(i.preco_centavos)}` + (i.descricao ? `\n${i.descricao}` : ""));
  return partes.join("\n\n");
}

const resumoItem = (i) => ({ nome: i.nome, preco_centavos: i.preco_centavos, pagina: i.pagina, ordem: i.ordem, ativo: i.ativo });

export function descreverMudanca(antes, depois) {
  if (!antes) return `Criou "${depois.nome}" (${fmtPreco(depois.preco_centavos)}) na ${depois.pagina.replace("p", "página ")}`;
  const m = [];
  if (antes.nome !== depois.nome) m.push(`nome "${antes.nome}" → "${depois.nome}"`);
  if (antes.preco_centavos !== depois.preco_centavos) m.push(`preço ${fmtPreco(antes.preco_centavos)} → ${fmtPreco(depois.preco_centavos)}`);
  if ((antes.descricao || "") !== (depois.descricao || "")) m.push("descrição alterada");
  if (antes.pagina !== depois.pagina) m.push(`movido para a ${depois.pagina.replace("p", "página ")}`);
  if (antes.ativo !== depois.ativo) m.push(depois.ativo ? "voltou a aparecer na loja" : "ocultado da loja");
  return `Editou "${depois.nome}": ${m.join("; ") || "sem mudanças visíveis"}`.slice(0, 300);
}

// ---------------------------------------------------------------- camada de dados
/** Firestore real. `F` é o módulo firebase-firestore e `email` o e-mail (minúsculo) de quem edita. */
export function criarRepoFirestore(F, db, email) {
  const ts = () => F.serverTimestamp();
  const campos = (i) => ({ nome: i.nome, preco_centavos: i.preco_centavos, descricao: i.descricao, pagina: i.pagina, ordem: i.ordem, ativo: i.ativo, atualizado_por: email, atualizado_em: ts() });
  const registrar = (lote, acao, alvo, resumo, antes, depois) => lote.set(F.doc(F.collection(db, "loja_catalogo_log")), {
    quando: ts(), quem: email, acao, alvo, resumo: String(resumo).slice(0, 300), ...(antes ? { antes } : {}), ...(depois ? { depois } : {}),
  });
  return {
    async salvarItem(item, antes) {
      const l = F.writeBatch(db);
      l.set(F.doc(db, "loja_itens", item.id), campos(item));
      registrar(l, antes ? "editar" : "criar", item.id, descreverMudanca(antes, item), antes && resumoItem(antes), resumoItem(item));
      await l.commit();
    },
    async excluirItem(item) {
      const l = F.writeBatch(db);
      l.delete(F.doc(db, "loja_itens", item.id));
      registrar(l, "excluir", item.id, `Excluiu "${item.nome}" (${fmtPreco(item.preco_centavos)})`, resumoItem(item), null);
      await l.commit();
    },
    async reordenar(itens, resumo) {
      const l = F.writeBatch(db);
      for (const i of itens) l.set(F.doc(db, "loja_itens", i.id), campos(i));
      registrar(l, "mover", itens[0].id, resumo, null, null);
      await l.commit();
    },
    async salvarPagina(pagina, antes) {
      const l = F.writeBatch(db);
      l.set(F.doc(db, "loja_paginas", pagina.id), { introducao: pagina.introducao, ordem: pagina.ordem, atualizado_por: email, atualizado_em: ts() });
      registrar(l, "pagina", pagina.id, antes ? `Editou o texto da ${pagina.id.replace("p", "página ")}` : `Criou a ${pagina.id.replace("p", "página ")}`, null, null);
      await l.commit();
    },
    async excluirPagina(pagina) {
      const l = F.writeBatch(db);
      l.delete(F.doc(db, "loja_paginas", pagina.id));
      registrar(l, "pagina", pagina.id, `Excluiu a ${pagina.id.replace("p", "página ")}`, null, null);
      await l.commit();
    },
  };
}

/** Versão em memória, para o modo demonstração e para os testes. `dados` = { itens: Map, paginas: Map, log: [] }. */
export function criarRepoDemo(dados, aoMudar, email = "demo@exemplo.com") {
  const agora = () => new Date();
  const log = (acao, alvo, resumo) => dados.log.unshift({ id: String(Math.random()), quando: agora(), quem: email, acao, alvo, resumo });
  const gravar = (i) => dados.itens.set(i.id, { ...i, atualizado_por: email, atualizado_em: agora() });
  return {
    async salvarItem(item, antes) { gravar(item); log(antes ? "editar" : "criar", item.id, descreverMudanca(antes, item)); aoMudar(); },
    async excluirItem(item) { dados.itens.delete(item.id); log("excluir", item.id, `Excluiu "${item.nome}" (${fmtPreco(item.preco_centavos)})`); aoMudar(); },
    async reordenar(itens, resumo) { itens.forEach(gravar); log("mover", itens[0].id, resumo); aoMudar(); },
    async salvarPagina(p, antes) { dados.paginas.set(p.id, { ...p, atualizado_por: email, atualizado_em: agora() }); log("pagina", p.id, antes ? `Editou o texto da ${p.id.replace("p", "página ")}` : `Criou a ${p.id.replace("p", "página ")}`); aoMudar(); },
    async excluirPagina(p) { dados.paginas.delete(p.id); log("pagina", p.id, `Excluiu a ${p.id.replace("p", "página ")}`); aoMudar(); },
  };
}

// ---------------------------------------------------------------- tela
const S = { raiz: null, repo: null, podeEditar: false, carregado: false, itens: new Map(), paginas: new Map(), log: [], toasts: null, dlgs: {} };

const quando = (v) => { const d = v?.toDate ? v.toDate() : v ? new Date(v) : null; return d && !isNaN(d) ? d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "agora"; };
const paginasOrdenadas = () => [...S.paginas.values()].sort((a, b) => (a.ordem - b.ordem) || a.id.localeCompare(b.id));
const itensDaPagina = (pid) => [...S.itens.values()].filter((i) => i.pagina === pid).sort((a, b) => (a.ordem - b.ordem) || a.id.localeCompare(b.id));
const visiveis = (pid) => itensDaPagina(pid).filter((i) => i.ativo);
/** Itens visíveis da página `pid` como ficariam depois de gravar `item` (que pode ser novo, editado ou vindo de outra página). */
const visiveisCom = (pid, item) => {
  const resto = itensDaPagina(pid).filter((i) => i.id !== item.id);
  if (item.pagina === pid) resto.push(item);
  return resto.filter((i) => i.ativo);
};

function toast(msg, tipo = "ok") {
  const t = el("div", { class: `toast ${tipo}`, role: tipo === "erro" ? "alert" : "status", text: msg });
  S.toasts.append(t);
  setTimeout(() => t.remove(), tipo === "erro" ? 9000 : 4000);
}
const erroLegivel = (e) => e?.code === "permission-denied"
  ? "Sem permissão para editar. Peça à staff, no Discord: buggy!loja_acesso editor <seu e-mail>"
  : `Não consegui salvar (${e?.code || e?.message || e}).`;

async function executar(fn, sucesso) {
  try { await fn(); if (sucesso) toast(sucesso); return true; } catch (e) { toast(erroLegivel(e), "erro"); return false; }
}

function confirmar(titulo, texto, rotulo) {
  return new Promise((resolve) => {
    const dlg = S.dlgs.confirmar;
    dlg.querySelector("h3").textContent = titulo;
    dlg.querySelector("p").textContent = texto;
    const ok = dlg.querySelector("[data-ok]"); ok.textContent = rotulo;
    const fim = (v) => { dlg.close(); resolve(v); };
    ok.onclick = () => fim(true);
    dlg.querySelector("[data-cancelar]").onclick = () => fim(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}

// ----- janela de item -----
function abrirItem(item, paginaId) {
  const dlg = S.dlgs.item, f = dlg.querySelector("form");
  const novo = !item;
  const base = item || { id: "", nome: "", preco_centavos: 0, descricao: "", pagina: paginaId || paginasOrdenadas()[0]?.id, ordem: 0, ativo: true };
  dlg.querySelector("h3").textContent = novo ? "Novo item" : `Editar: ${base.nome}`;
  f.nome.value = base.nome;
  f.preco.value = base.preco_centavos ? (base.preco_centavos / 100).toFixed(2).replace(".", ",") : "";
  f.descricao.value = base.descricao || "";
  f.ativo.checked = base.ativo;
  f.pagina.replaceChildren(...paginasOrdenadas().map((p, n) => el("option", { value: p.id, text: `Página ${n + 1}` })));
  f.pagina.value = base.pagina;
  dlg.querySelector("[data-codigo]").textContent = novo ? "O código do item é gerado a partir do nome." : `Código: ${base.id}`;
  const erros = dlg.querySelector("[data-erros]"); erros.replaceChildren();

  const atualizar = () => {
    dlg.querySelector("[data-cont-desc]").textContent = `${f.descricao.value.length}/${LIMITES.descricao}`;
    const c = parsePreco(f.preco.value);
    const previa = `## ${MARCADOR} ${f.nome.value.trim() || "Nome do item"} - ${c ? fmtPreco(c) : "R$ ?"}` + (f.descricao.value.trim() ? `\n${f.descricao.value.trim()}` : "");
    dlg.querySelector("[data-previa]").textContent = previa;
  };
  for (const campo of [f.nome, f.preco, f.descricao]) campo.oninput = atualizar;
  atualizar();

  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const problemas = [];
    const nome = f.nome.value.trim(), descricao = f.descricao.value.trim(), preco = parsePreco(f.preco.value);
    if (!nome) problemas.push("Dê um nome ao item.");
    if (nome.length > LIMITES.nome) problemas.push(`O nome pode ter no máximo ${LIMITES.nome} caracteres.`);
    if (preco === null) problemas.push(`Preço inválido. Use um valor entre R$ 0,01 e ${fmtPreco(LIMITES.precoMax)} (ex.: 5 ou 12,50).`);
    if (descricao.length > LIMITES.descricao) problemas.push(`A descrição pode ter no máximo ${LIMITES.descricao} caracteres.`);
    if (!S.paginas.has(f.pagina.value)) problemas.push("Escolha uma página.");
    const id = novo ? slugId(nome, S.itens) : base.id;
    const pagina = f.pagina.value;
    const ordem = novo || pagina !== base.pagina ? Math.max(0, ...itensDaPagina(pagina).map((i) => i.ordem)) + 10 : base.ordem;
    const depois = { id, nome, preco_centavos: preco, descricao, pagina, ordem, ativo: f.ativo.checked };
    if (!problemas.length) {
      const visiveisTotal = [...S.itens.values()].filter((i) => i.ativo && i.id !== id).length + (depois.ativo ? 1 : 0);
      if (visiveisTotal > LIMITES.itens) problemas.push(`A loja comporta no máximo ${LIMITES.itens} itens visíveis (o menu do Discord tem esse limite). Oculte ou exclua algum item.`);
      const tamanho = textoPagina(S.paginas.get(pagina).introducao, visiveisCom(pagina, depois)).length;
      if (tamanho > LIMITES.textoPagina) problemas.push(`A página ficaria com ${tamanho} caracteres e o limite do Discord é ${LIMITES.textoPagina}. Reduza a descrição ou mova o item para outra página.`);
    }
    erros.replaceChildren(...problemas.map((p) => el("li", { text: p })));
    if (problemas.length) return;
    const botao = f.querySelector("[type=submit]"); botao.disabled = true;
    const ok = await executar(() => S.repo.salvarItem(depois, novo ? null : S.itens.get(id)), novo ? `"${nome}" adicionado.` : `"${nome}" salvo.`);
    botao.disabled = false;
    if (ok) dlg.close();
  };
  dlg.showModal();
  f.nome.focus();
}

// ----- janela de página -----
function abrirPagina(pagina) {
  const dlg = S.dlgs.pagina, f = dlg.querySelector("form");
  const n = paginasOrdenadas().findIndex((p) => p.id === pagina.id) + 1;
  dlg.querySelector("h3").textContent = `Texto da página ${n}`;
  f.introducao.value = pagina.introducao || "";
  const erros = dlg.querySelector("[data-erros]"); erros.replaceChildren();
  const atualizar = () => {
    const tam = textoPagina(f.introducao.value, visiveis(pagina.id)).length;
    dlg.querySelector("[data-cont]").textContent = `Introdução: ${f.introducao.value.length}/${LIMITES.introducao} · página inteira na loja: ${tam}/${LIMITES.textoPagina}`;
  };
  f.introducao.oninput = atualizar; atualizar();
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const intro = f.introducao.value.trim(), problemas = [];
    if (intro.length > LIMITES.introducao) problemas.push(`A introdução pode ter no máximo ${LIMITES.introducao} caracteres.`);
    if (textoPagina(intro, visiveis(pagina.id)).length > LIMITES.textoPagina) problemas.push(`A página inteira passaria de ${LIMITES.textoPagina} caracteres (limite do Discord).`);
    erros.replaceChildren(...problemas.map((p) => el("li", { text: p })));
    if (problemas.length) return;
    const ok = await executar(() => S.repo.salvarPagina({ ...pagina, introducao: intro }, pagina), `Texto da página ${n} salvo.`);
    if (ok) dlg.close();
  };
  dlg.showModal();
  f.introducao.focus();
}

// ----- ações -----
async function mover(item, delta) {
  const lista = itensDaPagina(item.pagina), i = lista.findIndex((x) => x.id === item.id), j = i + delta;
  if (j < 0 || j >= lista.length) return;
  [lista[i], lista[j]] = [lista[j], lista[i]];
  const alterados = lista.map((x, k) => ({ ...x, ordem: (k + 1) * 10 })).filter((x, k) => S.itens.get(x.id).ordem !== x.ordem);
  if (!alterados.length) return;
  await executar(() => S.repo.reordenar(alterados, `Moveu "${item.nome}" ${delta < 0 ? "para cima" : "para baixo"}`));
}

async function alternarVisivel(item, ativo) {
  const depois = { ...item, ativo };
  if (ativo) {
    const total = [...S.itens.values()].filter((i) => i.ativo && i.id !== item.id).length + 1;
    if (total > LIMITES.itens) return toast(`A loja comporta no máximo ${LIMITES.itens} itens visíveis.`, "erro");
    const tam = textoPagina(S.paginas.get(item.pagina)?.introducao, visiveisCom(item.pagina, depois)).length;
    if (tam > LIMITES.textoPagina) return toast(`Não cabe: a página ficaria com ${tam} caracteres (limite ${LIMITES.textoPagina}).`, "erro");
  }
  await executar(() => S.repo.salvarItem(depois, item), ativo ? `"${item.nome}" voltou para a loja.` : `"${item.nome}" ocultado da loja.`);
}

async function excluirItem(item) {
  if (await confirmar("Excluir item?", `"${item.nome}" será removido da loja para sempre. As compras já feitas continuam no histórico. Para só tirar da loja por um tempo, use "ocultar".`, "Excluir"))
    await executar(() => S.repo.excluirItem(item), `"${item.nome}" excluído.`);
}

async function novaPagina() {
  const livre = IDS_PAGINA.find((id) => !S.paginas.has(id));
  if (!livre || S.paginas.size >= LIMITES.paginas) return toast(`A loja comporta no máximo ${LIMITES.paginas} páginas (cabem na linha de botões do Discord).`, "erro");
  const ordem = Math.max(0, ...[...S.paginas.values()].map((p) => p.ordem)) + 1;
  await executar(() => S.repo.salvarPagina({ id: livre, introducao: "", ordem }, null), "Página criada.");
}

async function excluirPagina(p) {
  if (itensDaPagina(p.id).length) return toast("Mova ou exclua os itens desta página antes de excluí-la.", "erro");
  if (await confirmar("Excluir página?", "Esta página vazia será removida da loja.", "Excluir")) await executar(() => S.repo.excluirPagina(p), "Página excluída.");
}

// ----- desenho -----
function botao(texto, acao, props = {}) {
  const b = el("button", { type: "button", class: "btn pequeno", ...props, text: texto });
  b.addEventListener("click", acao);
  return b;
}

function desenhar() {
  const raiz = S.raiz.querySelector("[data-conteudo]");
  if (!S.carregado) { raiz.replaceChildren(el("p", { class: "muted", role: "status", text: "Carregando o catálogo…" })); return; }
  const editar = S.podeEditar;
  const blocos = [];
  const nItens = [...S.itens.values()].filter((i) => i.ativo).length;

  blocos.push(el("div", { class: "ed-topo" },
    el("div", {}, el("h2", { text: "Catálogo da loja" }),
      el("p", { class: "muted", text: `${nItens} de ${LIMITES.itens} itens visíveis · ${S.paginas.size} de ${LIMITES.paginas} páginas. As mudanças aparecem na loja do Discord em poucos segundos.` })),
    editar ? el("div", { class: "ed-acoes" },
      botao("➕ Novo item", () => abrirItem(null), { class: "btn primary", id: "novoItem" }),
      botao("➕ Nova página", novaPagina, { id: "novaPagina" })) : ""));
  if (!editar) blocos.push(el("p", { class: "aviso", role: "note", text: "Você pode ver o catálogo, mas não editar. Peça a permissão à staff, no Discord: buggy!loja_acesso editor <seu e-mail>" }));

  paginasOrdenadas().forEach((p, n) => {
    const lista = itensDaPagina(p.id);
    const tam = textoPagina(p.introducao, lista.filter((i) => i.ativo)).length;
    const cheio = tam > LIMITES.textoPagina ? "erro" : tam > LIMITES.textoPagina * 0.9 ? "aviso-tam" : "";
    const cab = el("div", { class: "ed-pagina-cab" },
      el("div", {}, el("h3", { text: `Página ${n + 1}` }),
        el("span", { class: `contador ${cheio}`, text: `texto na loja: ${tam}/${LIMITES.textoPagina} caracteres` })),
      editar ? el("div", { class: "ed-acoes" },
        botao("✏️ Texto da página", () => abrirPagina(p), { "aria-label": `Editar o texto da página ${n + 1}` }),
        botao("➕ Item aqui", () => abrirItem(null, p.id), { "aria-label": `Novo item na página ${n + 1}` }),
        botao("🗑️ Excluir página", () => excluirPagina(p), { "aria-label": `Excluir a página ${n + 1}`, ...(lista.length ? { disabled: "", title: "Só dá para excluir páginas vazias" } : {}) })) : "");
    const intro = p.introducao ? el("p", { class: "intro muted", text: p.introducao.length > 180 ? p.introducao.slice(0, 177) + "…" : p.introducao }) : "";

    const corpo = el("tbody");
    if (!lista.length) corpo.append(el("tr", {}, el("td", { class: "vazio", colspan: "5", text: "Nenhum item nesta página." })));
    lista.forEach((i, k) => {
      const nome = el("div", {}, el("strong", { class: "it-nome", text: i.nome }), el("div", { class: "muted pequeno-txt", text: i.id }));
      if (!i.ativo) nome.append(el("span", { class: "badge b-cancelada", text: "oculto" }));
      const vis = el("input", { type: "checkbox", "aria-label": `Visível na loja: ${i.nome}`, ...(i.ativo ? { checked: "" } : {}), ...(editar ? {} : { disabled: "" }) });
      vis.checked = i.ativo; vis.addEventListener("change", () => alternarVisivel(i, vis.checked));
      corpo.append(el("tr", { "data-id": i.id, class: i.ativo ? "" : "oculto" },
        el("td", { "data-label": "Item" }, nome),
        el("td", { "data-label": "Preço", class: "num" }, el("strong", { text: fmtPreco(i.preco_centavos) })),
        el("td", { "data-label": "Visível" }, vis),
        el("td", { "data-label": "Atualizado", class: "muted" }, `${quando(i.atualizado_em)}${i.atualizado_por ? " · " + String(i.atualizado_por).split("@")[0] : ""}`),
        el("td", { "data-label": "Ações" }, editar ? el("div", { class: "ed-acoes" },
          botao("▲", () => mover(i, -1), { "aria-label": `Subir ${i.nome}`, ...(k === 0 ? { disabled: "" } : {}) }),
          botao("▼", () => mover(i, +1), { "aria-label": `Descer ${i.nome}`, ...(k === lista.length - 1 ? { disabled: "" } : {}) }),
          botao("✏️ Editar", () => abrirItem(i), { "aria-label": `Editar ${i.nome}` }),
          botao("🗑️", () => excluirItem(i), { "aria-label": `Excluir ${i.nome}`, title: "Excluir" })) : "")));
    });
    const tabela = el("div", { class: "tablebox" }, el("table", { class: "ed-tabela" },
      el("thead", {}, el("tr", {}, ...["Item", "Preço", "Visível", "Atualizado", "Ações"].map((t) => el("th", { text: t })))), corpo));
    blocos.push(el("section", { class: "ed-pagina", "data-pagina": p.id }, cab, intro, tabela));
  });

  const hist = el("ul", { class: "ed-historico" });
  if (!S.log.length) hist.append(el("li", { class: "muted", text: "Nenhuma alteração registrada ainda." }));
  for (const e of S.log.slice(0, 30)) hist.append(el("li", {}, el("span", { class: "muted", text: `${quando(e.quando)} · ${String(e.quem).split("@")[0]}` }), el("span", { text: ` — ${e.resumo}` })));
  blocos.push(el("section", { class: "ed-pagina" }, el("h3", { text: "Últimas alterações" }), hist));

  raiz.replaceChildren(...blocos);
}

// ---------------------------------------------------------------- API do módulo
export const editor = {
  /** Cria a aba dentro de `raiz`. `repo` = criarRepoFirestore(...) ou criarRepoDemo(...). */
  montar(raiz, { repo, podeEditar }) {
    S.raiz = raiz; S.repo = repo; S.podeEditar = !!podeEditar; S.carregado = false;
    raiz.replaceChildren(el("div", { "data-conteudo": "" }));
    S.toasts = el("div", { class: "toasts", "aria-live": "polite" });
    const dlgItem = el("dialog", { class: "ed-dialog", "aria-labelledby": "tituloItem" }, el("form", { novalidate: "" },
      el("h3", { id: "tituloItem" }),
      el("p", { class: "muted pequeno-txt", "data-codigo": "" }),
      el("label", {}, "Nome", el("input", { name: "nome", type: "text", maxlength: String(LIMITES.nome), required: "", autocomplete: "off" })),
      el("div", { class: "ed-linha" },
        el("label", {}, "Preço (R$)", el("input", { name: "preco", type: "text", inputmode: "decimal", placeholder: "ex.: 5 ou 12,50", autocomplete: "off" })),
        el("label", {}, "Página", el("select", { name: "pagina" }))),
      el("label", {}, "Descrição (aparece abaixo do nome e do preço)", el("textarea", { name: "descricao", rows: "8", maxlength: String(LIMITES.descricao) }),
        el("span", { class: "muted pequeno-txt", "data-cont-desc": "" })),
      el("p", { class: "muted pequeno-txt", text: "Use a formatação do Discord: **negrito**, __sublinhado__, listas com “-”, canais <#id>, cargos <@&id>. O nome e o preço já saem como título, não repita." }),
      el("label", { class: "ed-check" }, el("input", { name: "ativo", type: "checkbox" }), " Visível na loja"),
      el("div", {}, el("strong", { text: "Prévia (texto como será enviado ao Discord)" }), el("pre", { class: "previa", "data-previa": "" })),
      el("ul", { class: "erros", role: "alert", "data-erros": "" }),
      el("div", { class: "ed-acoes fim" }, el("button", { type: "button", class: "btn", "data-cancelar": "", text: "Cancelar" }), el("button", { type: "submit", class: "btn primary", text: "Salvar" }))));
    dlgItem.querySelector("[data-cancelar]").addEventListener("click", () => dlgItem.close());
    const dlgPagina = el("dialog", { class: "ed-dialog", "aria-labelledby": "tituloPagina" }, el("form", { novalidate: "" },
      el("h3", { id: "tituloPagina" }),
      el("label", {}, "Texto de introdução (aparece no topo da página, antes dos itens)", el("textarea", { name: "introducao", rows: "8", maxlength: String(LIMITES.introducao) })),
      el("p", { class: "muted pequeno-txt", "data-cont": "" }),
      el("ul", { class: "erros", role: "alert", "data-erros": "" }),
      el("div", { class: "ed-acoes fim" }, el("button", { type: "button", class: "btn", "data-cancelar": "", text: "Cancelar" }), el("button", { type: "submit", class: "btn primary", text: "Salvar" }))));
    dlgPagina.querySelector("[data-cancelar]").addEventListener("click", () => dlgPagina.close());
    const dlgConfirmar = el("dialog", { class: "ed-dialog estreito", "aria-labelledby": "tituloConfirmar" }, el("div", {},
      el("h3", { id: "tituloConfirmar" }), el("p", {}),
      el("div", { class: "ed-acoes fim" }, el("button", { type: "button", class: "btn", "data-cancelar": "", text: "Cancelar" }), el("button", { type: "button", class: "btn perigo", "data-ok": "", text: "Confirmar" }))));
    S.dlgs = { item: dlgItem, pagina: dlgPagina, confirmar: dlgConfirmar };
    raiz.append(dlgItem, dlgPagina, dlgConfirmar, S.toasts);
    desenhar();
  },
  /** Chamado a cada atualização do banco. */
  definir({ itens, paginas, log }) {
    if (!S.raiz) return;
    S.carregado = true;
    S.itens = new Map(itens.map((i) => [i.id, i]));
    S.paginas = new Map(paginas.map((p) => [p.id, p]));
    S.log = log || [];
    desenhar();
  },
  /** Mostra um aviso (toast) fora do módulo, ex.: erro ao ler o catálogo. */
  avisar(msg, tipo = "erro") { if (S.toasts) toast(msg, tipo); },
  limpar() {
    S.itens = new Map(); S.paginas = new Map(); S.log = []; S.carregado = false;
    if (S.raiz) { for (const d of Object.values(S.dlgs)) if (d.open) d.close(); S.raiz.replaceChildren(); }
    S.raiz = null;
  },
};
