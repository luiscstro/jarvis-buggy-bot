"""Proteção contra o limite de requisições do Discord (HTTP 429 / ban 1015 da Cloudflare).

O erro 1015 é um bloqueio temporário do IP inteiro pela Cloudflare, e não se resolve insistindo: cada nova
requisição negada conta contra o IP e prolonga o bloqueio. Por isso, ao receber um 429, as rotinas automáticas do
bot ficam em pausa por um tempo em vez de tentar de novo a cada rodada.
"""
import time

import discord

PAUSA_429_SEGUNDOS = 15 * 60        # tempo sem rotinas automáticas depois de um 429
_pausa_ate = 0.0


def eh_limite(e: BaseException) -> bool:
    """True se o erro é um 429 do Discord (inclui a página HTML da Cloudflare, "error code: 0")."""
    return isinstance(e, discord.HTTPException) and e.status == 429


def resumir_erro(e: BaseException, limite: int = 200) -> str:
    """Texto curto para o log. A resposta da Cloudflare é uma página HTML de ~300 linhas: nunca imprimir inteira."""
    if eh_limite(e):
        return "429 Too Many Requests (Discord/Cloudflare limitou as requisições deste IP)"
    texto = " ".join(str(e).split())
    return texto if len(texto) <= limite else texto[:limite] + "…"


def registrar_se_limite(e: BaseException) -> bool:
    """Se for um 429, pausa as rotinas automáticas. Devolve se era um 429."""
    global _pausa_ate
    if not eh_limite(e):
        return False
    _pausa_ate = max(_pausa_ate, time.monotonic() + PAUSA_429_SEGUNDOS)
    return True


def em_pausa() -> bool:
    return time.monotonic() < _pausa_ate


def segundos_de_pausa() -> int:
    return max(0, int(_pausa_ate - time.monotonic()))
