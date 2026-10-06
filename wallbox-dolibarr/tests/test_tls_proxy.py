"""TLS-Proxy (Caddy) vor ExpenseCharge: echte Absenderadresse nur vom eigenen Proxy, über das Internet nur mit Passwort."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import websockets  # noqa: E402
from ocpp.v16 import call  # noqa: E402

from ocpp_server.central_system import CentralSystemDeps  # noqa: E402
from ocpp_server.server import OcppServer  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import basic_auth_header  # noqa: E402
from utils import proxy  # noqa: E402

PW = '0123456789abcdef'


def test_client_ip_only_trusts_own_proxy(monkeypatch):
    monkeypatch.setattr(proxy, 'trusted_proxies', lambda: {'172.20.0.5'})
    assert proxy.client_ip('172.20.0.5', '203.0.113.7') == '203.0.113.7'
    assert proxy.client_ip('172.20.0.5', '10.0.0.1, 203.0.113.7') == '203.0.113.7', "der vom Proxy angehängte Wert"
    assert proxy.client_ip('192.168.1.50', '1.2.3.4') == '192.168.1.50', "fremder Absender darf nichts fälschen"
    assert proxy.client_ip('172.20.0.5', None) == '172.20.0.5'
    assert proxy.via_proxy('172.20.0.5') and not proxy.via_proxy('192.168.1.50')


def test_no_proxy_configured(monkeypatch):
    monkeypatch.delenv('TLS_DOMAIN', raising=False)
    assert proxy.trusted_proxies() == set()
    assert proxy.public_ws_url() is None
    monkeypatch.setenv('TLS_DOMAIN', 'ladung.firma.de')
    assert proxy.public_ws_url() == 'wss://ladung.firma.de/ocpp/'


@pytest.fixture()
async def server(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, 'trusted_proxies', lambda: {'127.0.0.1'})   # Test: localhost spielt den Proxy
    settings = resolve_ocpp_settings({'session_source': 'ocpp', 'ocpp_charge_points': [
        {'id': 'CP1', 'password': PW}, {'id': 'OPEN'}]})
    srv = OcppServer(settings, CentralSystemDeps(session_manager=SessionManager(db_path=str(tmp_path / 's.db')),
                                                 whitelist=[], live={}))
    port = await srv.start('127.0.0.1', 0)
    yield srv, port
    await srv.close()


async def _connect(port, cp_id, headers):
    async with websockets.connect(f'ws://127.0.0.1:{port}/ocpp/{cp_id}', subprotocols=['ocpp1.6'],
                                  additional_headers=headers):
        pass


async def test_lockout_keyed_by_real_client(server):
    srv, port = server
    for _ in range(5):
        with pytest.raises(websockets.InvalidStatus):
            await _connect(port, 'CP1', {**basic_auth_header('CP1', 'falsch-falsch-123'), 'X-Forwarded-For': '203.0.113.7'})
    assert srv._limiter.locked_for('203.0.113.7') and not srv._limiter.locked_for('127.0.0.1')
    await _connect(port, 'CP1', {**basic_auth_header('CP1', PW), 'X-Forwarded-For': '198.51.100.9'})   # andere Wallbox geht


async def test_passwordless_rejected_via_internet(server):
    _, port = server
    with pytest.raises(websockets.InvalidStatus) as exc:
        await _connect(port, 'OPEN', {'X-Forwarded-For': '203.0.113.7'})
    assert exc.value.response.status_code == 401


def test_ui_shows_wss_and_profile_2(monkeypatch):
    from admin import web as admin_web
    monkeypatch.setenv('TLS_DOMAIN', 'ladung.firma.de')
    assert admin_web._ws_url(None, None) == 'wss://ladung.firma.de/ocpp/'
    assert 'Security Profile 2' in admin_web._security_profile()
    monkeypatch.delenv('TLS_DOMAIN')
    assert 'Security Profile 1' in admin_web._security_profile()
