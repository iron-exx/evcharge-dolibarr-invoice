"""Jeder Wallbox-Name muss bei Dolibarr ankommen (receive.php: ^[\\w\\-\\.]{1,50}$, nur ASCII)."""
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import WallboxApiClient  # noqa: E402
from utils.wallbox_id import to_dolibarr_id  # noqa: E402

RECEIVE_PHP = re.compile(r'^[\w\-.]{1,50}$', re.ASCII)


def test_any_name_becomes_valid_id():
    cases = {'Wallbox 1': 'Wallbox_1', 'Müller Straße': 'Mueller_Strasse', 'Garage/links': 'Garage_links',
             'Alfen Eve #1': 'Alfen_Eve__1', 'garage': 'garage', 'Élan': 'Elan', '': 'wallbox',
             '   ': 'wallbox', '🚗': 'wallbox', 'x' * 80: 'x' * 50}
    for raw, expected in cases.items():
        assert to_dolibarr_id(raw) == expected, raw
        assert RECEIVE_PHP.match(to_dolibarr_id(raw))


def test_every_transmission_is_converted(monkeypatch):
    client = WallboxApiClient(base_url='https://erp.firma.de', api_token='tok-12345678', retries=0)
    sent = {}

    class Resp:
        status_code = 200
        headers = {'Content-Type': 'application/json'}
        def raise_for_status(self): pass
        def json(self): return {'success': True}

    def post(url, json=None, **kw):
        sent.update(json)
        return Resp()

    monkeypatch.setattr(client.session, 'post', post)
    client.transmit_session({'wallbox_id': 'Wallbox Müller 1', 'start_time': '2026-10-05T08:00:00',
                             'end_time': '2026-10-05T09:00:00', 'kwh': 5, 'rfid_hash': 'a' * 64})
    assert sent['wallbox_id'] == 'Wallbox_Mueller_1'
