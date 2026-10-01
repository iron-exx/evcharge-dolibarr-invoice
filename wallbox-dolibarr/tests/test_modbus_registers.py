"""Dekodierung von Modbus-Registerwerten.

Hier entscheidet sich, ob korrekt abgerechnet wird: Modbus liefert nackte
16-Bit-Wörter ohne Typ und ohne Einheit. Hersteller kombinieren sie frei zu
32-Bit-Werten, in beiden Wort-Reihenfolgen, mit beliebigem Skalierungsfaktor.
Ein Fehler hier bedeutet falsche kWh auf der Spesenabrechnung.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modbus_source.registers import decode_registers, decode_string  # noqa: E402


def test_uint16():
    assert decode_registers([1234], 'uint16') == 1234


def test_int16_negative():
    assert decode_registers([0xFFFF], 'int16') == -1
    assert decode_registers([0xFF9C], 'int16') == -100


def test_uint32_big_endian_word_order():
    # 0x0001_86A0 = 100000
    assert decode_registers([0x0001, 0x86A0], 'uint32') == 100000


def test_uint32_little_endian_word_order():
    """Viele Hersteller senden das niedrige Wort zuerst (word_order='little')."""
    assert decode_registers([0x86A0, 0x0001], 'uint32', word_order='little') == 100000


def test_int32_negative():
    assert decode_registers([0xFFFF, 0xFFFF], 'int32') == -1


def test_float32():
    # 1.0 = 0x3F800000
    assert decode_registers([0x3F80, 0x0000], 'float32') == pytest.approx(1.0)
    assert decode_registers([0x0000, 0x3F80], 'float32', word_order='little') == pytest.approx(1.0)


def test_scale_converts_wh_to_kwh():
    """Zählerstände kommen oft in Wh oder in 0,1-Wh-Schritten."""
    assert decode_registers([0x0001, 0x86A0], 'uint32', scale=0.001) == pytest.approx(100.0)
    assert decode_registers([1234], 'uint16', scale=0.1) == pytest.approx(123.4)


def test_unknown_type_and_short_input_are_rejected():
    with pytest.raises(ValueError):
        decode_registers([1], 'uint64')
    with pytest.raises(ValueError):
        decode_registers([1], 'uint32')          # zu wenig Wörter
    with pytest.raises(ValueError):
        decode_registers([], 'uint16')


def test_decode_string_strips_padding():
    # "AB12" in zwei Registern, big-endian
    assert decode_string([0x4142, 0x3132]) == "AB12"
    assert decode_string([0x4142, 0x0000]) == "AB"
    assert decode_string([0x0000, 0x0000]) == ""


def test_decode_string_tolerates_garbage():
    assert decode_string([0xFFFE]) == ""
