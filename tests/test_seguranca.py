"""Segurança: permissões, entradas maliciosas, vazamento de segredos e proteção do painel web/banco."""
import asyncio
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import loja
from conftest import RAIZ, interacao_staff_no_canal, membro, staff


def run(coro):
    return asyncio.run(coro)


def _ctx(autor):
    c = MagicMock(); c.author = autor; c.send = AsyncMock(); c.message.delete = AsyncMock()
    return c


# ---------------------------------------------------------------- quem pode o quê
INTRUSOS = [
    pytest.param(lambda: membro(2, "Jogador"), id="membro comum"),
    pytest.param(lambda: membro(3, "Quase", cargos=("moderação",)), id="cargo com nome parecido"),
    pytest.param(lambda: membro(4, "VIP", cargos=("VIP", "Apoiador")), id="cargo irrelevante"),
    pytest.param(lambda: MagicMock(spec=[]), id="não é membro do servidor"),
]


@pytest.mark.parametrize("quem", INTRUSOS)
def test_comandos_da_staff_negam_quem_nao_e_staff(cog, bd, quem):
    for nome, args in [("loja_painel", ()), ("loja_acesso", ("adicionar", "intruso@gmail.com")),
                       ("loja_acesso", ("listar",)), ("loja_armazenamento", ())]:
        ctx = _ctx(quem())
        run(getattr(cog, nome).callback(cog, ctx, *args))
        assert "Sem permissão" in ctx.send.call_args.args[0], nome
    assert "loja_staff" not in bd.cols            # nada foi gravado


@pytest.mark.parametrize("quem", INTRUSOS)
@pytest.mark.parametrize("acao", ["paga", "entregue", "fechar"])
def test_botoes_do_canal_negam_quem_nao_e_staff(cog, quem, acao):
    from conftest import comprar
    run(comprar(cog, membro(70), {"escolhidos": 1}))
    cid = cog.armazenamento.dados["pedidos"]["01"]["canal_id"]
    i = interacao_staff_no_canal(cid, quem())
    run(cog.fechar_compra(i) if acao == "fechar" else cog.mudar_status(i, acao))
    assert "Só a staff" in i.response.send_message.call_args.args[0]
    assert cog.armazenamento.dados["pedidos"]["01"]["status"] == "aguardando_pagamento"
    assert not cog.armazenamento.dados["pedidos"]["01"]["fechado_em"] and i.channel.delete.await_count == 0


# ---------------------------------------------------------------- e-mails em loja_acesso
EMAILS_MALICIOSOS = [
    "a/b@c.com", "../x@y.com", "x@y.com/../../loja_pedidos", "a@b.com\nb@c.com", "a b@c.com", "a@b@c.com",
    "@c.com", "a@", "a@b", "a@b.", "a@.com", "<script>@x.com", 'a"@x.com', "a\\@x.com", "a\x00@x.com",
    "a" * 300 + "@x.com", "é@x.com", "a@x.c", "a@-x.com", "a@x..com", "a,b@x.com", "a;b@x.com", "a@x.com;b@y.com",
    "__.__/__@x.com", "..@x.com", ".@x.com", "a@x.com.", "a@[127.0.0.1]",
]


@pytest.mark.parametrize("ruim", EMAILS_MALICIOSOS)
def test_loja_acesso_recusa_emails_perigosos(cog, bd, ruim):
    ctx = _ctx(staff())
    run(cog.loja_acesso.callback(cog, ctx, "adicionar", ruim))
    assert not bd.cols.get("loja_staff"), f"gravou: {list(bd.cols.get('loja_staff', {}))}"
    assert "Use:" in ctx.send.call_args.args[0]


# a'--@x.com é sintaxe válida (apóstrofo existe em e-mails reais) e inofensivo: o banco não é SQL.
@pytest.mark.parametrize("bom", ["ana@gmail.com", "ana.silva@gmail.com", "ana+loja@gmail.com", "a_b-c@dominio.com.br", "ANA@GMAIL.COM", "o'neil@x.com"])
def test_loja_acesso_aceita_emails_normais_em_minusculas(cog, bd, bom):
    run(cog.loja_acesso.callback(cog, _ctx(staff()), "adicionar", bom))
    assert list(bd.cols["loja_staff"]) == [bom.lower()]


def test_loja_acesso_acao_desconhecida_e_ignorada(cog, bd):
    for acao in ("banir", "DROP", "", "adicionar;remover", "../adicionar"):
        run(cog.loja_acesso.callback(cog, _ctx(staff()), acao, "ok@x.com"))
    assert not bd.cols.get("loja_staff")


# ---------------------------------------------------------------- entradas maliciosas na loja
def test_catalogo_nao_aceita_chave_de_item_inexistente_na_janela_de_quantidade():
    """A chave vem do menu do Discord, mas nunca confiamos: o código só opera sobre ITENS."""
    from conftest import interacao
    v = loja.LojaView(None, membro())
    with pytest.raises(KeyError):
        loja.QuantidadesModal(v, ["item_que_nao_existe"])


def test_total_vem_do_catalogo_e_nunca_do_cliente(cog):
    from conftest import comprar, guild_falso
    run(comprar(cog, membro(5), {"escolhidos": 3}, guild_falso()))
    assert cog.armazenamento.dados["pedidos"]["01"]["total_centavos"] == 3 * loja.ITENS["escolhidos"]["preco"]


def test_pagina_de_pedido_malicioso_e_preservada_como_texto(arm):
    from conftest import pedido_modelo
    nome = '<img src=x onerror=alert(1)>"; DROP TABLE'
    arm.registrar(1, pedido_modelo(1, nome=nome))
    assert arm.dados["pedidos"]["01"]["nome"] == nome            # guardado sem alterar; escapar é papel da página


# ---------------------------------------------------------------- vazamento de segredos
def _arquivos_versionados():
    saida = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.split()
    return [RAIZ / f for f in saida if (RAIZ / f).is_file() and (RAIZ / f).suffix not in (".jpg", ".png", ".pyc")]


def _valores_secretos_do_env():
    env = RAIZ / ".env"
    if not env.exists():
        return {}
    segredos = {}
    for linha in env.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.lstrip().startswith("#"):
            k, v = linha.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() in ("BOT_TOKEN", "GROQ_API_KEY", "PIX_COPIA_COLA", "FIREBASE_CREDENTIALS_BASE64") and len(v) > 12:
                segredos[k.strip()] = v
    return segredos


def test_nenhum_segredo_do_env_aparece_nos_arquivos_versionados():
    segredos = _valores_secretos_do_env()
    if not segredos:
        pytest.skip("sem .env local para comparar")
    for arq in _arquivos_versionados():
        texto = arq.read_text(encoding="utf-8", errors="ignore")
        for nome, valor in segredos.items():
            assert valor not in texto, f"{nome} apareceu em {arq.relative_to(RAIZ)}"


def test_chave_privada_da_service_account_nao_esta_em_nenhum_arquivo_versionado():
    cred = RAIZ / "firebase-credentials.json"
    marcadores = [r"BEGIN (RSA |EC )?PRIVATE KEY", r"\"private_key\"", r"gsk_[A-Za-z0-9]{20,}", r"\"client_email\"\s*:"]
    if cred.exists():
        import json
        info = json.loads(cred.read_text(encoding="utf-8"))
        marcadores += [re.escape(info["private_key_id"]), re.escape(info["client_email"])]
    for arq in _arquivos_versionados():
        texto = arq.read_text(encoding="utf-8", errors="ignore")
        for m in marcadores:
            if arq.name == "test_seguranca.py":
                continue
            assert not re.search(m, texto), f"padrão sensível {m!r} em {arq.relative_to(RAIZ)}"


def test_segredos_e_dados_de_runtime_estao_no_gitignore():
    for arq in (".env", "firebase-credentials.json", "meu-projeto-firebase-adminsdk-abc-123.json", "store_data.json", "bot-discord-x.json"):
        r = subprocess.run(["git", "check-ignore", "-q", arq], cwd=RAIZ)
        assert r.returncode == 0, f"{arq} NÃO está ignorado pelo git"


def test_env_nunca_vai_no_deploy_errado():
    """O .env precisa subir para a Discloud (o bot lê o token dele); só não pode ir ao GitHub."""
    ignore = (RAIZ / ".discloudignore").read_text(encoding="utf-8").splitlines()
    assert ".env" not in [l.strip() for l in ignore]
    assert "firebase-credentials.json" not in [l.strip() for l in ignore]


def test_nada_do_painel_web_vai_para_o_bot():
    ignore = [l.strip() for l in (RAIZ / ".discloudignore").read_text(encoding="utf-8").splitlines()]
    for item in ("dashboard/", "tools/", "firestore.rules"):
        assert item in ignore


# ---------------------------------------------------------------- regras do Firestore (análise estática)
def _regras():
    return (RAIZ / "firestore.rules").read_text(encoding="utf-8")


def test_regras_negam_escrita_e_leitura_por_padrao():
    r = _regras()
    assert "allow read, write: if false" in r
    assert not re.search(r"allow\s+[a-z, ]*:\s*if\s+true", r)
    assert not re.search(r"allow\s+(read|write|create|update|delete)[^;]*if\s+request\.auth\s*!=\s*null\s*;", r), "qualquer logado leria"


def test_regras_exigem_email_verificado_e_estar_na_lista():
    r = _regras()
    assert "email_verified == true" in r and "loja_staff" in r and "request.auth.token.email.lower()" in r
    for colecao in ("loja_pedidos", "loja_meta"):
        bloco = re.search(rf"match /{colecao}/\{{id\}} \{{(.*?)\}}", r, re.S).group(1)
        assert "allow read: if eStaff()" in bloco and "allow write: if false" in bloco


def test_regras_nao_expoem_a_lista_de_emails():
    r = _regras()
    assert "match /loja_staff" not in r            # cai na regra "tudo fechado"


# ---------------------------------------------------------------- painel web (análise estática)
def _html():
    return (RAIZ / "dashboard" / "index.html").read_text(encoding="utf-8")


def test_painel_nunca_injeta_dados_como_html():
    h = _html()
    for perigoso in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc"):
        assert perigoso not in h, perigoso


def test_painel_nao_carrega_scripts_de_terceiros_alem_do_firebase():
    h = _html()
    origens = set(re.findall(r"https?://[a-z0-9.\-]+", h))
    permitidas = {"https://www.gstatic.com", "http://www.w3.org"}
    assert origens <= permitidas, origens - permitidas
    assert 'name="robots" content="noindex' in h


def test_painel_nao_tem_credencial_privada():
    for arq in (RAIZ / "dashboard").rglob("*"):
        if arq.is_file():
            t = arq.read_text(encoding="utf-8", errors="ignore")
            assert "private_key" not in t and "client_secret" not in t and "BEGIN PRIVATE" not in t


def test_deploy_envia_cabecalhos_de_seguranca():
    d = (RAIZ / "tools" / "deploy_firebase.py").read_text(encoding="utf-8")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert h in d


# ---------------------------------------------------------------- site e banco no ar (só leitura, sem login)
def _config_web():
    t = (RAIZ / "dashboard" / "firebase-config.js").read_text(encoding="utf-8")
    return {k: re.search(rf'"{k}": "([^"]+)"', t).group(1) for k in ("apiKey", "projectId")}


@pytest.mark.integration
@pytest.mark.parametrize("colecao", ["loja_pedidos", "loja_meta", "loja_staff"])
def test_banco_real_recusa_leitura_anonima(colecao):
    import requests
    c = _config_web()
    r = requests.get(f"https://firestore.googleapis.com/v1/projects/{c['projectId']}/databases/(default)/documents/{colecao}?key={c['apiKey']}", timeout=30)
    assert r.status_code == 403


@pytest.mark.integration
@pytest.mark.parametrize("colecao", ["loja_pedidos", "loja_meta", "loja_staff"])
def test_banco_real_recusa_escrita_anonima(colecao):
    import requests
    c = _config_web()
    r = requests.post(f"https://firestore.googleapis.com/v1/projects/{c['projectId']}/databases/(default)/documents/{colecao}?key={c['apiKey']}",
                      json={"fields": {"x": {"stringValue": "y"}}}, timeout=30)
    assert r.status_code == 403


@pytest.mark.integration
def test_site_no_ar_tem_cabecalhos_de_seguranca_e_so_dois_arquivos():
    import requests
    c = _config_web()
    r = requests.get(f"https://{c['projectId']}.web.app/", timeout=30)
    assert r.status_code == 200
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    for caminho in ("/.env", "/firebase-credentials.json", "/store_data.json", "/bot.py", "/loja.py", "/tools/deploy_firebase.py"):
        assert requests.get(f"https://{c['projectId']}.web.app{caminho}", timeout=30).headers.get("content-type", "").startswith("text/html"), caminho
        assert "private_key" not in requests.get(f"https://{c['projectId']}.web.app{caminho}", timeout=30).text
