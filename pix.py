"""Utilitários de PIX (BR Code / "copia e cola" e QR Code).

A partir do código PIX estático da conta (variável de ambiente PIX_COPIA_COLA)
gera um código com o VALOR da compra já embutido, no padrão EMV do Banco Central,
e o QR Code correspondente.
"""
import io

import qrcode


def _crc16(texto: str) -> int:
    """CRC16/CCITT-FALSE exigido pelo BR Code (polinômio 0x1021, inicial 0xFFFF)."""
    crc = 0xFFFF
    for byte in texto.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _ler_campos(payload: str) -> list[tuple[str, str]]:
    """Lê os campos TLV (id de 2 dígitos + tamanho de 2 dígitos + valor) do nível raiz."""
    campos, i = [], 0
    while i < len(payload):
        id_, tam = payload[i:i + 2], int(payload[i + 2:i + 4])
        campos.append((id_, payload[i + 4:i + 4 + tam]))
        i += 4 + tam
    return campos


def payload_valido(payload: str) -> bool:
    """True se o código tem estrutura TLV válida e o CRC16 final confere."""
    try:
        if len(payload) < 8 or payload[-8:-4] != "6304":
            return False
        _ler_campos(payload)
        return f"{_crc16(payload[:-4]):04X}" == payload[-4:].upper()
    except (ValueError, IndexError):
        return False


def beneficiario(payload: str) -> str:
    """Nome do recebedor (campo 59) para o comprador conferir antes de pagar."""
    return dict(_ler_campos(payload)).get("59", "")


def payload_com_valor(payload: str, centavos: int) -> str:
    """Devolve o código PIX com o valor (campo 54) e o CRC recalculado."""
    campos = [(i, v) for i, v in _ler_campos(payload) if i not in ("54", "63")]
    valor = f"{centavos // 100}.{centavos % 100:02d}"
    # Os campos seguem ordem crescente de id: o valor (54) vem logo após a moeda (53).
    posicao = next((n + 1 for n, (i, _) in enumerate(campos) if i == "53"), len(campos))
    campos.insert(posicao, ("54", valor))
    corpo = "".join(f"{i}{len(v):02d}{v}" for i, v in campos) + "6304"
    return f"{corpo}{_crc16(corpo):04X}"


def qr_png(payload: str) -> bytes:
    """QR Code (PNG) do código PIX."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()
