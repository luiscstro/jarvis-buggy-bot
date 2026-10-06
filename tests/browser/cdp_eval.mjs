// Abre uma URL no Chrome (sem janela), roda um script dentro da página e imprime o resultado em JSON.
//   node cdp_eval.mjs <url> <arquivo.js> [largura=1280] [altura=900] [tema=]
// O arquivo é o CORPO de uma função async; o que ele devolver (JSON) vai em "resultado".
// Também devolve erros do console, exceções e diálogos (alert/confirm) que a página tenha aberto.
import { spawn } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, arquivo, largura = "1280", altura = "900"] = process.argv.slice(2);
const chrome = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const porta = 9400 + Math.floor(Math.random() * 500);
const perfil = mkdtempSync(join(tmpdir(), "cdp-eval-"));
const proc = spawn(chrome, [
  "--headless=new", "--disable-gpu", `--remote-debugging-port=${porta}`, `--user-data-dir=${perfil}`,
  "--no-first-run", "--disable-extensions", `--window-size=${largura},${altura}`, "about:blank",
], { stdio: "ignore" });

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
const saida = { resultado: null, erros: [], excecoes: [], dialogos: [] };
async function terminar(codigo) {
  console.log(JSON.stringify(saida));
  try { proc.kill(); } catch {}
  await dormir(300);
  try { rmSync(perfil, { recursive: true, force: true }); } catch {}
  process.exit(codigo);
}
setTimeout(() => { saida.excecoes.push("TIMEOUT do teste (60s)"); terminar(2); }, 60000);

let alvo;
for (let i = 0; i < 60 && !alvo; i++) {
  try { alvo = (await (await fetch(`http://127.0.0.1:${porta}/json`)).json()).find((t) => t.type === "page"); } catch { await dormir(250); }
}
if (!alvo) { saida.excecoes.push("Chrome não abriu"); await terminar(2); }

const ws = new WebSocket(alvo.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pend = new Map(); let carregou;
const aguardaLoad = new Promise((r) => (carregou = r));
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pend.has(d.id)) { pend.get(d.id)(d.result ?? { erro: d.error }); pend.delete(d.id); return; }
  if (d.method === "Page.loadEventFired") carregou();
  else if (d.method === "Runtime.exceptionThrown") saida.excecoes.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
  else if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") saida.erros.push(d.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
  else if (d.method === "Log.entryAdded" && d.params.entry.level === "error") saida.erros.push(`${d.params.entry.text} ${d.params.entry.url || ""}`);
  else if (d.method === "Page.javascriptDialogOpening") { saida.dialogos.push(d.params.message); cdp("Page.handleJavaScriptDialog", { accept: true }); }
};
const cdp = (method, params = {}) => new Promise((r) => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

await cdp("Runtime.enable"); await cdp("Log.enable"); await cdp("Page.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: +largura, height: +altura, deviceScaleFactor: 1, mobile: false });
await cdp("Page.navigate", { url });
await Promise.race([aguardaLoad, dormir(20000)]);
const corpo = readFileSync(arquivo, "utf-8");
const r = await cdp("Runtime.evaluate", { awaitPromise: true, returnByValue: true, expression: `(async () => { ${corpo} })()` });
if (r.exceptionDetails) saida.excecoes.push(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
saida.resultado = r.result?.value ?? null;
await terminar(0);
