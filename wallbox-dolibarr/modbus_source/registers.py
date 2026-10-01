"""Dekodierung von Modbus-Registerwerten.

Modbus überträgt nackte 16-Bit-Wörter: ohne Typ, ohne Vorzeichen, ohne Einheit.
Was darin steht, legt allein die Registerkarte des Herstellers fest. Deshalb ist
alles konfigurierbar — Typ, Wort-Reihenfolge und Skalierungsfaktor.

Zur Wort-Reihenfolge: Innerhalb eines Registers ist Modbus immer Big-Endian.
Bei 32-Bit-Werten über zwei Register unterscheiden sich die Hersteller aber:
'big' = höheres Wort zuerst (Modbus-Konvention), 'little' = niedrigeres Wort
zuerst (verbreitet, oft "word swap" genannt).
"""
import struct
from typing import Sequence

# Typ → (Anzahl Register, struct-Format für den zusammengesetzten Big-Endian-Wert)
_TYPES = {
    'uint16': (1, '>H'),
    'int16': (1, '>h'),
    'uint32': (2, '>I'),
    'int32': (2, '>i'),
    'float32': (2, '>f'),
}

VALID_TYPES = tuple(_TYPES)
VALID_WORD_ORDERS = ('big', 'little')


def decode_registers(words: Sequence[int], value_type: str,
                     word_order: str = 'big', scale: float = 1.0):
    """Registerwörter → Zahl.

    scale wird am Ende multipliziert: 0.001 rechnet Wh in kWh um, 0.1 ein
    Register in Zehntelschritten.
    """
    try:
        count, fmt = _TYPES[value_type]
    except KeyError:
        raise ValueError(f"unbekannter Registertyp {value_type!r}, erlaubt: {VALID_TYPES}")
    if len(words) < count:
        raise ValueError(f"{value_type} braucht {count} Register, bekam {len(words)}")

    chunk = list(words[:count])
    if word_order == 'little':
        chunk.reverse()
    raw = b''.join(struct.pack('>H', w & 0xFFFF) for w in chunk)
    value = struct.unpack(fmt, raw)[0]
    return value * scale if scale != 1.0 else value


def decode_string(words: Sequence[int]) -> str:
    """Registerwörter → Text (für RFID-Tags, die manche Wallboxen so liefern).

    Zwei ASCII-Zeichen je Register, höheres Byte zuerst. Nullbytes und
    Leerzeichen am Rand werden entfernt; nicht darstellbare Bytes beenden den
    Text, statt Müll in die Karten-ID zu lassen.
    """
    out = []
    for word in words:
        for byte in (((word >> 8) & 0xFF), (word & 0xFF)):
            if byte == 0:
                return ''.join(out).strip()
            if not (0x20 <= byte < 0x7F):
                return ''.join(out).strip()
            out.append(chr(byte))
    return ''.join(out).strip()
