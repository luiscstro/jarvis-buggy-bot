"""Zera os dados de compras do Firebase (para tirar compras de teste antes de abrir a loja de verdade).

    python tools/limpar_banco.py                # só MOSTRA o que seria apagado (não apaga nada)
    python tools/limpar_banco.py --confirmar    # faz backup em backups/ e apaga

O que apaga:
  - todos os pedidos (loja_pedidos) e zera o contador: a próxima compra será a 01;
  - coleções de teste (nome começando com "teste_"), se houver.

O que NÃO mexe:
  - loja_staff (e-mails autorizados no painel web) e os usuários do login;
  - a referência ao painel antigo do Discord (para o bot apagar essa mensagem ao reiniciar).

Como o arquivo local do bot (store_data.json, inclusive o que está no servidor) ainda tem os pedidos
antigos, o banco ganha uma nova "época": ao reiniciar, o bot vê que o banco foi zerado, descarta o
arquivo local e passa a valer só o Firebase. Sem isso os pedidos apagados voltariam.

Lembre-se de apagar também, no Discord, os canais das compras de teste (nick-01, nick-02...).
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / ".env")

import loja_dados  # noqa: E402


def conectar():
    remoto = loja_dados.FirestoreRemoto.conectar()
    if not remoto:
        sys.exit("Não consegui conectar ao Firebase (veja as credenciais).")
    return remoto.cliente


def reais(cliente, colecao):
    return [(d.id, d.to_dict()) for d in cliente.collection(colecao).stream()]


def resumo_pedido(chave, p):
    itens = ", ".join(f"{i['qtd']}x {i['nome']}" for i in p.get("itens_detalhe", [])) or str(p.get("itens", {}))
    total = p.get("total_centavos", 0)
    return (f"  #{chave}  {p.get('nome') or p.get('usuario', '?'):<18} R$ {total // 100},{total % 100:02d}  "
            f"[{p.get('status', '?')}]  {p.get('criado_em', 'sem data')[:19]}  {itens[:60]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirmar", action="store_true", help="de fato apaga (faz backup antes)")
    args = ap.parse_args()

    cliente = conectar()
    pedidos = reais(cliente, loja_dados.COLECAO_PEDIDOS)
    meta = cliente.collection(loja_dados.COLECAO_META).document(loja_dados.DOC_META).get()
    meta = meta.to_dict() if meta.exists else {}
    staff = [i for i, _ in reais(cliente, loja_dados.COLECAO_STAFF)]
    teste = {c.id: [i for i, _ in reais(cliente, c.id)] for c in cliente.collections() if c.id.startswith("teste_")}

    print(f"\nPedidos que serão apagados ({len(pedidos)}):")
    for chave, p in sorted(pedidos, key=lambda x: int(x[0])):
        print(resumo_pedido(chave, p))
    print(f"\nContador: {meta.get('ultimo_id', 0)} -> 0 (a próxima compra será a 01)")
    print(f"Coleções de teste a apagar: {teste or 'nenhuma'}")
    print(f"Fica intacto: loja_staff ({len(staff)} e-mail(s)) e os usuários do login.")

    if not args.confirmar:
        print("\nNada foi apagado. Para apagar de verdade: python tools/limpar_banco.py --confirmar")
        return

    # backup antes de qualquer exclusão
    pasta = RAIZ / "backups"
    pasta.mkdir(exist_ok=True)
    arq = pasta / f"banco_{datetime.now():%Y%m%d_%H%M%S}.json"
    arq.write_text(json.dumps({"meta": meta, "pedidos": dict(pedidos), "teste": teste}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nBackup salvo em {arq.relative_to(RAIZ)}")

    for chave, _ in pedidos:
        cliente.collection(loja_dados.COLECAO_PEDIDOS).document(chave).delete()
    for colecao in teste:
        for d in cliente.collection(colecao).stream():
            d.reference.delete()
    epoca = uuid.uuid4().hex
    cliente.collection(loja_dados.COLECAO_META).document(loja_dados.DOC_META).set({
        "ultimo_id": 0,
        "dashboard": meta.get("dashboard"),          # o bot apaga o painel antigo do Discord ao reiniciar
        "epoca": epoca,
        "atualizado_em": loja_dados.agora_iso(),
    })

    restantes = len(reais(cliente, loja_dados.COLECAO_PEDIDOS))
    print(f"Apagados {len(pedidos)} pedido(s). Pedidos restantes: {restantes}. Nova época: {epoca[:8]}…")
    print("\nPróximos passos:")
    print("  1. No Discord, apague os canais das compras de teste.")
    print("  2. Reinicie o bot: ele detecta a nova época e descarta o arquivo local antigo do servidor.")


if __name__ == "__main__":
    main()
