"""Eine von Dolibarr dauerhaft abgelehnte Ladung darf die übrigen nicht aufhalten."""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api_client import is_permanent_error  # noqa: E402
from session_manager import SessionManager  # noqa: E402

BAD = 'b' * 64


@pytest.fixture()
def sm(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    for i, h in enumerate((BAD, 'a' * 64, 'c' * 64)):
        sm.add_manual_session(kwh=3.0 + i, wallbox_id='garage', session_date=f'2026-07-1{i}', rfid_hash=h)
    return sm


class Dolibarr:
    """Kennt die Karte BAD nicht (HTTP 404), alles andere geht."""
    def __init__(self, error='HTTP 404: {"success":false,"error":"RFID not registered in Dolibarr"}'):
        self.sent, self.error = [], error

    def transmit_session(self, data):
        self.sent.append(data['rfid_hash'])
        return (False, self.error) if data['rfid_hash'] == BAD else (True, '')


def _by_hash(sm):
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    rows = {r['rfid_hash']: dict(r) for r in conn.execute('SELECT * FROM sessions')}
    conn.close()
    return rows


def test_classification():
    assert is_permanent_error('HTTP 404: RFID not registered')
    assert is_permanent_error('HTTP 400: kwh must be greater than 0')
    for temporary in ('HTTP 401: Unauthorized', 'HTTP 500: boom', 'Timeout nach 30s',
                      'Verbindungsfehler: x', 'Server returned HTML statt JSON'):
        assert not is_permanent_error(temporary), temporary


def test_rejected_session_is_parked_and_others_continue(sm):
    client = Dolibarr()
    result = sm.transmit_completed_sessions(client)
    assert result['transmitted'] == 2 and result['rejected'] == 1 and result['failed'] == 0
    rows = _by_hash(sm)
    assert rows[BAD]['transmitted_at'] is None and 'RFID not registered' in rows[BAD]['transmit_error']
    assert rows['a' * 64]['transmitted_at'] and rows['c' * 64]['transmitted_at']
    # nächster Lauf: die abgelehnte wird nicht sofort wieder versucht
    client.sent.clear()
    assert sm.transmit_completed_sessions(client)['rejected'] == 0 and client.sent == []
    assert sm.session_counts()['rejected'] == 1


def test_retry_after_fix(sm):
    sm.transmit_completed_sessions(Dolibarr())
    sid = _by_hash(sm)[BAD]['id']
    assert sm.retry_rejected_session(sid)
    ok = Dolibarr(error=None)
    ok.transmit_session = lambda data: (True, '')
    assert sm.transmit_completed_sessions(ok)['transmitted'] == 1
    row = _by_hash(sm)[BAD]
    assert row['transmitted_at'] and row['transmit_error'] is None


def test_global_error_still_stops_the_run(sm):
    client = Dolibarr()
    client.transmit_session = lambda data: (client.sent.append(1) or (False, 'HTTP 401: Unauthorized'))
    result = sm.transmit_completed_sessions(client)
    assert result['failed'] == 1 and len(client.sent) == 1, "Token falsch → alle scheitern, nicht weiter hämmern"
    assert all(r['transmit_error'] is None for r in _by_hash(sm).values()), "nicht als abgelehnt geparkt"
