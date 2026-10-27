"""Proteção contra o 429 / ban 1015 da Cloudflare: log curto e rotinas automáticas em pausa."""
from types import SimpleNamespace

import discord

import limites

PAGINA_CLOUDFLARE = "<!doctype html>\n" + "<p>Error 1015 You are being rate limited</p>\n" * 300


def _http(status, texto):
    return discord.HTTPException(SimpleNamespace(status=status, reason="x"), texto)


def test_429_vira_uma_linha_curta():
    e = _http(429, PAGINA_CLOUDFLARE)
    assert limites.eh_limite(e)
    resumo = limites.resumir_erro(e)
    assert "429" in resumo and "\n" not in resumo and len(resumo) < 200


def test_outros_erros_sao_truncados():
    assert len(limites.resumir_erro(ValueError("x" * 5000))) <= 201


def test_429_pausa_as_rotinas(monkeypatch):
    monkeypatch.setattr(limites, "_pausa_ate", 0.0)
    assert not limites.em_pausa()
    assert not limites.registrar_se_limite(_http(403, "Missing Permissions"))
    assert not limites.em_pausa()
    assert limites.registrar_se_limite(_http(429, PAGINA_CLOUDFLARE))
    assert limites.em_pausa() and limites.segundos_de_pausa() > 0


def test_espera_do_login_dobra_ate_o_teto():
    assert [limites.espera_do_login(n) // 60 for n in range(5)] == [15, 30, 60, 120, 120]


def test_freio_de_loop_entre_bots():
    import bot
    bot._respostas_a_bots.clear()
    assert [bot.bot_em_loop(1) for _ in range(bot.BOT_MAX_RESPOSTAS)] == [False] * bot.BOT_MAX_RESPOSTAS
    assert bot.bot_em_loop(1) is True                                  # a próxima passa do limite
    assert bot.bot_em_loop(2) is False                                 # outro canal não é afetado
