"""Envia o catálogo PADRÃO (catalogo_padrao.py) para o Firebase, para você poder editá-lo pelo painel.

    python tools/semear_catalogo.py            # só semeia se o catálogo nunca foi semeado e está vazio
    python tools/semear_catalogo.py --forcar   # SOBRESCREVE os itens e páginas padrão (cuidado: desfaz edições deles)

Normalmente você não precisa rodar isto: o bot semeia sozinho na primeira vez que inicia com o Firebase
configurado. Serve para deixar o painel pronto antes do próximo deploy do bot.
"""
import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / ".env")

import catalogo  # noqa: E402
import loja_dados  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forcar", action="store_true", help="sobrescreve os itens/páginas padrão mesmo se já houver dados")
    args = ap.parse_args()
    remoto = loja_dados.FirestoreRemoto.conectar()
    if not remoto:
        sys.exit("Não consegui conectar ao Firebase (veja as credenciais).")
    if catalogo.semear(remoto.cliente, forcar=args.forcar):
        print(f"Catálogo semeado: {len(catalogo.PAGINAS_PADRAO)} páginas e {len(catalogo.ITENS_PADRAO)} itens.")
    else:
        print("Nada a fazer: o catálogo já foi semeado (ou já tem dados). Use --forcar para sobrescrever o padrão.")


if __name__ == "__main__":
    main()
