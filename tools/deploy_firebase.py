"""Publica o painel web de compras no Firebase (sem precisar do firebase-tools).

Uso (na pasta do bot, com o firebase-credentials.json ou o .env configurado):

    python tools/deploy_firebase.py             # app web + regras + hospedagem
    python tools/deploy_firebase.py --so-regras # só as regras de segurança
    python tools/deploy_firebase.py --so-site   # só a página

O que faz, nesta ordem (tudo é idempotente, pode rodar de novo à vontade):
  1. garante que existe um app web no projeto e grava os dados dele em
     dashboard/firebase-config.js (não são segredos);
  2. publica firestore.rules;
  3. publica a pasta dashboard/ na hospedagem do Firebase (https://<projeto>.web.app).
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / ".env")

from google.auth.transport.requests import AuthorizedSession  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

import loja_dados  # noqa: E402

PASTA_SITE = RAIZ / "dashboard"
ARQ_REGRAS = RAIZ / "firestore.rules"
ARQ_CONFIG = PASTA_SITE / "firebase-config.js"
NOME_APP = "Painel de Compras"

CABECALHOS = [
    {"glob": "**", "headers": {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    }},
]


def sessao():
    info = loja_dados.carregar_credenciais()
    if not info:
        sys.exit("Não achei as credenciais do Firebase (firebase-credentials.json ou FIREBASE_CREDENTIALS_BASE64).")
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/firebase"]
    )
    return AuthorizedSession(cred), info["project_id"]


def checar(resp, o_que):
    if resp.status_code >= 300:
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        sys.exit(f"Falhou ao {o_que}: HTTP {resp.status_code} — {msg[:300]}")
    return resp.json() if resp.content else {}


def esperar_operacao(s, nome, base):
    """Espera uma operação de longa duração do Google terminar."""
    for _ in range(60):
        op = checar(s.get(f"{base}/{nome}", timeout=30), "consultar a operação")
        if op.get("done"):
            if "error" in op:
                sys.exit(f"A operação falhou: {op['error']}")
            return op.get("response", {})
        time.sleep(2)
    sys.exit("A operação demorou demais.")


# ---------------------------------------------------------------- 1) app web
def garantir_app_web(s, projeto) -> dict:
    api = "https://firebase.googleapis.com/v1beta1"
    apps = checar(s.get(f"{api}/projects/{projeto}/webApps", timeout=30), "listar os apps web").get("apps", [])
    if apps:
        app = apps[0]
        print(f"• App web já existe: {app.get('displayName')} ({app['appId']})")
    else:
        print("• Criando o app web…")
        op = checar(s.post(f"{api}/projects/{projeto}/webApps", json={"displayName": NOME_APP}, timeout=30), "criar o app web")
        app = esperar_operacao(s, op["name"], api)
        print(f"  criado: {app['appId']}")
    cfg = checar(s.get(f"{api}/projects/{projeto}/webApps/{app['appId']}/config", timeout=30), "ler a config do app web")
    config = {k: cfg[k] for k in ("apiKey", "authDomain", "projectId", "appId") if k in cfg}
    ARQ_CONFIG.write_text(
        "// Configuração do app web do Firebase. NÃO são segredos: identificam o projeto; quem protege os\n"
        "// dados são o login do Google e as regras do Firestore (firestore.rules).\n"
        "// Gerado por tools/deploy_firebase.py.\n"
        f"export const firebaseConfig = {json.dumps(config, indent=2)};\n",
        encoding="utf-8",
    )
    print(f"  gravado em {ARQ_CONFIG.relative_to(RAIZ)}")
    return config


# ---------------------------------------------------------------- 2) regras
def publicar_regras(s, projeto):
    api = "https://firebaserules.googleapis.com/v1"
    conteudo = ARQ_REGRAS.read_text(encoding="utf-8")
    atual = checar(s.get(f"{api}/projects/{projeto}/releases/cloud.firestore", timeout=30), "ler as regras atuais")
    ruleset_atual = checar(s.get(f"{api}/{atual['rulesetName']}", timeout=30), "ler o conjunto de regras atual")
    if any(f.get("content", "").strip() == conteudo.strip() for f in ruleset_atual.get("source", {}).get("files", [])):
        print("• Regras do Firestore já estão atualizadas.")
        return
    print("• Publicando as regras do Firestore…")
    novo = checar(
        s.post(f"{api}/projects/{projeto}/rulesets", json={"source": {"files": [{"name": "firestore.rules", "content": conteudo}]}}, timeout=30),
        "criar o conjunto de regras (verifique se a sintaxe está correta)",
    )
    checar(
        s.patch(f"{api}/projects/{projeto}/releases/cloud.firestore", json={"release": {"name": f"projects/{projeto}/releases/cloud.firestore", "rulesetName": novo["name"]}}, timeout=30),
        "publicar as regras",
    )
    print("  regras publicadas.")


# ---------------------------------------------------------------- 3) hospedagem
def publicar_site(s, projeto):
    api = "https://firebasehosting.googleapis.com/v1beta1"
    site = f"projects/{projeto}/sites/{projeto}"
    arquivos = {}
    for caminho in sorted(PASTA_SITE.rglob("*")):
        if caminho.is_file():
            comprimido = gzip.compress(caminho.read_bytes(), mtime=0)
            arquivos["/" + caminho.relative_to(PASTA_SITE).as_posix()] = (comprimido, hashlib.sha256(comprimido).hexdigest())
    print(f"• Publicando {len(arquivos)} arquivo(s) em {projeto}.web.app…")

    versao = checar(s.post(f"{api}/{site}/versions", json={"config": {"headers": CABECALHOS}}, timeout=30), "criar a versão do site")["name"]
    pedido = checar(
        s.post(f"{api}/{versao}:populateFiles", json={"files": {c: h for c, (_, h) in arquivos.items()}}, timeout=30),
        "enviar a lista de arquivos",
    )
    por_hash = {h: dados for dados, h in arquivos.values()}
    for h in pedido.get("uploadRequiredHashes", []):
        r = s.post(f"{pedido['uploadUrl']}/{h}", data=por_hash[h], headers={"Content-Type": "application/octet-stream"}, timeout=60)
        if r.status_code >= 300:
            sys.exit(f"Falhou ao enviar um arquivo: HTTP {r.status_code}")
    checar(s.patch(f"{api}/{versao}?update_mask=status", json={"status": "FINALIZED"}, timeout=30), "finalizar a versão")
    checar(s.post(f"{api}/{site}/releases?versionName={versao}", json={"message": "deploy via tools/deploy_firebase.py"}, timeout=30), "publicar a versão")
    print(f"  no ar: https://{projeto}.web.app")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so-regras", action="store_true")
    ap.add_argument("--so-site", action="store_true")
    args = ap.parse_args()
    s, projeto = sessao()
    print(f"Projeto: {projeto}")
    if not args.so_regras:
        garantir_app_web(s, projeto)
    if not args.so_site:
        publicar_regras(s, projeto)
    if not args.so_regras:
        publicar_site(s, projeto)


if __name__ == "__main__":
    main()
