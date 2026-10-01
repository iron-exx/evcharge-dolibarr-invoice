"""Parser für das Transaktions-Log der Alfen-Wallbox.

Das Log enthält vollständige Ladevorgänge — Transaktions-ID, Zeitpunkt, Zähler
und Karte. Damit wird nichts aus Sensorflanken erraten, sondern wie bei OCPP
eine fertige Transaktion importiert.

ACHTUNG: Das Format ist aus dem Parser der HACS-Integration rekonstruiert, nicht
an echter Hardware verifiziert. Deshalb wird musterbasiert gelesen (Datum, kWh
und Karte per Muster) statt über feste Feldpositionen — ein Firmware-Update darf
die Reihenfolge verschieben, ohne falsche kWh zu erzeugen.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from alfen_source.transactions import parse_transaction_log, parse_transaction_line  # noqa: E402

START = "4711_1:txstart 2 Socket 1, 2026-10-01 08:12:34 1234.567kWh EFCD083E 1 y"
STOP = "4712_1:txstop 2 Socket 1, 2026-10-01 10:45:02 1247.067kWh EFCD083E y"


def test_parses_a_start_line():
    e = parse_transaction_line(START)
    assert e['kind'] == 'start'
    assert e['line_id'] == 4711
    assert e['socket'] == 'Socket 1'
    assert e['timestamp'] == '2026-10-01T08:12:34'
    assert e['kwh'] == pytest.approx(1234.567)
    assert e['tag'] == 'EFCD083E'


def test_parses_a_stop_line():
    e = parse_transaction_line(STOP)
    assert e['kind'] == 'stop'
    assert e['kwh'] == pytest.approx(1247.067)
    assert e['tag'] == 'EFCD083E'


def test_tolerates_extra_or_reordered_trailing_fields():
    """Musterbasiert heißt: zusätzliche Felder hinten dürfen nichts kaputt machen."""
    line = "4711_1:txstart 2 7 Socket 1, 2026-10-01 08:12:34 1234.567kWh EFCD083E 1 y extra"
    e = parse_transaction_line(line)
    assert e['kwh'] == pytest.approx(1234.567) and e['tag'] == 'EFCD083E'


def test_comma_decimal_separator_is_accepted():
    line = "4711_1:txstart 2 Socket 1, 2026-10-01 08:12:34 1234,567kWh EFCD083E 1 y"
    assert parse_transaction_line(line)['kwh'] == pytest.approx(1234.567)


def test_unrelated_and_broken_lines_are_ignored():
    for junk in ("", "   ", "version:2,irgendwas", "4711_1:info Netzteil ok",
                 "4711_1:txstart kaputt", "4711_1:txstop 2 Socket 1, kein-datum 5kWh TAG"):
        assert parse_transaction_line(junk) is None


def test_a_line_without_a_tag_is_rejected():
    """Ohne Karte ist der Vorgang nicht zuordenbar — lieber überspringen als
    einer falschen Person zuordnen."""
    line = "4711_1:txstart 2 Socket 1, 2026-10-01 08:12:34 1234.567kWh"
    assert parse_transaction_line(line) is None


def test_pairs_start_and_stop_into_one_session():
    sessions = parse_transaction_log([START, STOP])
    assert len(sessions) == 1
    s = sessions[0]
    assert s['tag'] == 'EFCD083E'
    assert s['socket'] == 'Socket 1'
    assert s['start_time'] == '2026-10-01T08:12:34'
    assert s['end_time'] == '2026-10-01T10:45:02'
    assert s['start_kwh'] == pytest.approx(1234.567)
    assert s['end_kwh'] == pytest.approx(1247.067)
    assert s['total_kwh'] == pytest.approx(12.5)
    assert s['transaction_id'], "braucht eine stabile ID für die Idempotenz"


def test_an_unfinished_start_is_not_returned():
    """Ein laufender Vorgang hat noch keinen Stop — er darf nicht abgerechnet
    werden, solange der Endzählerstand fehlt."""
    assert parse_transaction_log([START]) == []


def test_a_stop_without_its_start_is_skipped():
    assert parse_transaction_log([STOP]) == []


def test_two_sockets_do_not_mix():
    s2_start = START.replace("Socket 1,", "Socket 2,").replace("EFCD083E", "AABBCCDD")
    s2_stop = STOP.replace("Socket 1,", "Socket 2,").replace("EFCD083E", "AABBCCDD")
    sessions = parse_transaction_log([START, s2_start, STOP, s2_stop])
    assert {s['socket'] for s in sessions} == {'Socket 1', 'Socket 2'}
    by_socket = {s['socket']: s for s in sessions}
    assert by_socket['Socket 1']['tag'] == 'EFCD083E'
    assert by_socket['Socket 2']['tag'] == 'AABBCCDD'


def test_consecutive_sessions_on_one_socket():
    s2 = START.replace("4711", "4713").replace("08:12:34", "14:00:00").replace("1234.567", "1247.067")
    e2 = STOP.replace("4712", "4714").replace("10:45:02", "16:30:00").replace("1247.067", "1255.067")
    sessions = parse_transaction_log([START, STOP, s2, e2])
    assert len(sessions) == 2
    assert [round(s['total_kwh'], 3) for s in sessions] == [12.5, 8.0]
    assert len({s['transaction_id'] for s in sessions}) == 2, "IDs müssen verschieden sein"


def test_a_counter_that_went_backwards_is_skipped():
    """Zählerstand kleiner am Ende als am Anfang: nicht abrechnen."""
    bad_stop = STOP.replace("1247.067", "1000.000")
    assert parse_transaction_log([START, bad_stop]) == []
