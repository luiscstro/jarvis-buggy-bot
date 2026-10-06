"""PIX: CRC, valor embutido, estrutura do código e QR Code."""
import random

import pytest

import pix
from conftest import BASE_PIX_TESTE, PIX_TESTE


def test_crc16_vetor_de_referencia():
    # vetor padrão do CRC16/CCITT-FALSE
    assert pix._crc16("123456789") == 0x29B1


def test_codigo_de_teste_e_valido():
    assert pix.payload_valido(PIX_TESTE)


@pytest.mark.parametrize("lixo", ["", "abc", "6304", "0002016304ZZZZ", "00020126" + "9" * 40, "00020101021226"])
def test_codigos_invalidos_sao_recusados_sem_excecao(lixo):
    assert pix.payload_valido(lixo) is False


def test_crc_adulterado_e_recusado():
    ruim = PIX_TESTE[:-1] + ("0" if PIX_TESTE[-1] != "0" else "1")
    assert pix.payload_valido(ruim) is False


def test_alterar_qualquer_caractere_invalida_o_codigo():
    for i in range(0, len(PIX_TESTE) - 4, 7):
        trocado = PIX_TESTE[:i] + ("X" if PIX_TESTE[i] != "X" else "Y") + PIX_TESTE[i + 1:]
        assert pix.payload_valido(trocado) is False, f"posição {i}"


@pytest.mark.parametrize("centavos,texto", [
    (1, "0.01"), (5, "0.05"), (99, "0.99"), (100, "1.00"), (500, "5.00"), (1500, "15.00"),
    (12345, "123.45"), (186425, "1864.25"), (99999999, "999999.99"),
])
def test_valor_embutido_formato_correto(centavos, texto):
    codigo = pix.payload_com_valor(PIX_TESTE, centavos)
    assert dict(pix._ler_campos(codigo))["54"] == texto
    assert pix.payload_valido(codigo)


def test_campos_em_ordem_crescente_e_valor_logo_apos_a_moeda():
    ids = [i for i, _ in pix._ler_campos(pix.payload_com_valor(PIX_TESTE, 2500))]
    assert ids == sorted(ids)
    assert ids[ids.index("53") + 1] == "54"


def test_preserva_os_outros_campos_e_nao_altera_o_original():
    original = PIX_TESTE
    novo = pix.payload_com_valor(original, 2500)
    assert original == PIX_TESTE
    a = {k: v for k, v in pix._ler_campos(original) if k not in ("63",)}
    b = {k: v for k, v in pix._ler_campos(novo) if k not in ("63", "54")}
    assert a == b


def test_e_idempotente_e_substitui_valor_anterior():
    uma = pix.payload_com_valor(PIX_TESTE, 700)
    duas = pix.payload_com_valor(uma, 700)
    assert uma == duas
    outra = pix.payload_com_valor(uma, 900)
    campos = [i for i, _ in pix._ler_campos(outra)]
    assert campos.count("54") == 1 and dict(pix._ler_campos(outra))["54"] == "9.00"


def test_muitos_valores_aleatorios_sempre_validos():
    rng = random.Random(42)
    for _ in range(500):
        c = rng.randint(1, 10_000_000)
        codigo = pix.payload_com_valor(PIX_TESTE, c)
        assert pix.payload_valido(codigo) and dict(pix._ler_campos(codigo))["54"] == f"{c // 100}.{c % 100:02d}"


def test_beneficiario():
    assert pix.beneficiario(PIX_TESTE) == "Loja de Teste"
    assert pix.beneficiario("00020101") == ""


def test_qr_e_um_png_valido():
    png = pix.qr_png(pix.payload_com_valor(PIX_TESTE, 2500))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 300


def test_qr_decodifica_para_o_mesmo_codigo():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    codigo = pix.payload_com_valor(PIX_TESTE, 2500)
    img = cv2.imdecode(np.frombuffer(pix.qr_png(codigo), np.uint8), cv2.IMREAD_COLOR)
    lido, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert lido == codigo
