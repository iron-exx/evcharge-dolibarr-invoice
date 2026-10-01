"""Import der Alfen-Transaktionen in den SessionManager.

Abgebildet auf die bestehende OCPP-Maschinerie: die Transaktions-ID der
Wallbox wird zum ocpp_start_timestamp. Damit gilt die dort schon getestete
Idempotenz — das Log wird bei jedem Durchlauf komplett gelesen, also MUSS ein
mehrfach gesehener Vorgang genau einmal abgerechnet werden.
"""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from alfen_source.importer import import_transactions  # noqa: E402
from session_manager import SessionManager  # noqa: E402


@pytest.fixture()
def sm(tmp_path):
    return SessionManager(db_path=str(tmp_path / "s.db"))


def _tx(tid="Socket1#1-2", tag="EFCD083E", start=0.0, end=12.5,
        t0="2026-10-01T08:00:00", t1="2026-10-01T10:00:00", socket="Socket 1"):
    return {'transaction_id': tid, 'socket': socket, 'tag': tag,
            'start_time': t0, 'end_time': t1,
            'start_kwh': start, 'end_kwh': end, 'total_kwh': round(end - start, 3)}


def _rows(sm):
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY id")]
    conn.close()
    return rows


def test_authorized_transaction_is_imported(sm):
    result = import_transactions(sm, [_tx()], wallbox_id="garage", whitelist=["EFCD083E"])
    assert result['imported'] == 1
    rows = _rows(sm)
    assert len(rows) == 1
    assert rows[0]['status'] == 'completed'
    assert rows[0]['total_kwh'] == pytest.approx(12.5)
    assert rows[0]['wallbox_id'] == 'garage'
    assert rows[0]['transmitted_at'] is None, "muss noch übertragen werden"


def test_importing_twice_bills_once(sm):
    """Das Log wird bei jedem Durchlauf komplett gelesen — ohne Idempotenz
    würde jede Ladung endlos neu abgerechnet."""
    import_transactions(sm, [_tx()], wallbox_id="garage", whitelist=["EFCD083E"])
    second = import_transactions(sm, [_tx()], wallbox_id="garage", whitelist=["EFCD083E"])
    assert second['imported'] == 0
    assert second['already_known'] == 1
    assert len(_rows(sm)) == 1


def test_unknown_card_is_not_imported(sm):
    result = import_transactions(sm, [_tx(tag="DEADBEEF")], wallbox_id="garage",
                                 whitelist=["EFCD083E"])
    assert result['imported'] == 0
    assert result['unauthorized'] == 1
    assert _rows(sm) == []


def test_a_card_classified_in_the_registry_is_accepted(sm):
    """Der Lernmodus muss auch hier wirken — ohne Eintrag in der
    Konfigurations-Whitelist."""
    sm.upsert_tag("C0FFEE42", label="Firmenwagen", mode="business")
    result = import_transactions(sm, [_tx(tag="C0FFEE42")], wallbox_id="garage", whitelist=[])
    assert result['imported'] == 1


def test_private_card_is_imported_but_never_transmitted(sm):
    sm.upsert_tag("AABBCCDD", label="Privat", mode="private")
    import_transactions(sm, [_tx(tag="AABBCCDD")], wallbox_id="garage", whitelist=[])

    class _Api:
        def __init__(self): self.sent = []
        def transmit_session(self, data): self.sent.append(data); return True, "ok"

    api = _Api()
    sm.transmit_completed_sessions(api)
    assert api.sent == [], "private Ladung darf Dolibarr nicht erreichen"
    assert _rows(sm)[0]['status'] == 'private'


def test_two_sockets_are_separate_connectors(sm):
    import_transactions(sm, [_tx(tid="Socket1#1-2"),
                             _tx(tid="Socket2#3-4", socket="Socket 2", end=7.0)],
                        wallbox_id="garage", whitelist=["EFCD083E"])
    rows = _rows(sm)
    assert {r['connector_id'] for r in rows} == {1, 2}


def test_learn_hook_sees_every_card_even_an_unauthorized_one(sm):
    seen = []
    import_transactions(sm, [_tx(tag="DEADBEEF")], wallbox_id="garage",
                        whitelist=["EFCD083E"], on_tag_seen=seen.append)
    assert seen == ["DEADBEEF"], "sonst kann der Admin die Karte nie freischalten"


def test_implausible_transaction_is_marked_not_billed(sm):
    """Die Plausibilitätsprüfung des SessionManagers muss auch hier greifen."""
    tx = _tx(end=500.0, t0="2026-10-01T08:00:00", t1="2026-10-01T09:00:00")
    import_transactions(sm, [tx], wallbox_id="garage", whitelist=["EFCD083E"])
    assert _rows(sm)[0]['status'] == 'incomplete'


def test_a_broken_transaction_does_not_stop_the_rest(sm):
    txs = [{'transaction_id': 'kaputt'}, _tx()]
    result = import_transactions(sm, txs, wallbox_id="garage", whitelist=["EFCD083E"])
    assert result['imported'] == 1
    assert result['errors'] == 1
