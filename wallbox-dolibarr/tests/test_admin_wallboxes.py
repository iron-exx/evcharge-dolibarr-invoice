"""Phase 2: Wallbox-Verwaltung gegen echten OCPP-Server und simulierte Wallbox."""
import asyncio
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import websockets  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from ocpp.v16 import call  # noqa: E402

from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from ocpp_server.central_system import CentralSystemDeps  # noqa: E402
from ocpp_server.server import OcppServer  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import connect_sim  # noqa: E402
from tests.test_admin_web import PW, _post, _token  # noqa: E402
from web_server import create_app  # noqa: E402

CP_PW = '0123456789abcdef'


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    for k in list(os.environ):
        if k.startswith('EC_'):
            monkeypatch.delenv(k)
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'},
              'ocpp_charge_points': [{'id': 'CP1', 'name': 'Garage', 'wallbox_id': 'garage', 'password': CP_PW}]}
    (tmp_path / 'options.json').write_text(json.dumps(config))
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    server = OcppServer(resolve_ocpp_settings(config),
                        CentralSystemDeps(session_manager=sm, whitelist=['EFCD083E'], live={}))
    port = await server.start('127.0.0.1', 0)

    def reload_ocpp():
        server.update_settings(resolve_ocpp_settings(config))
        return True

    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678',
                       reload_ocpp=reload_ocpp, ocpp=lambda: server)
    api_state = {'client': None, 'settings': resolve_app_settings(config), 'admin': ctx,
                 'charge_points': server.live}
    async with TestClient(TestServer(create_app(sm, config, api_state))) as client:
        await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
        yield {'client': client, 'port': port, 'server': server, 'dir': tmp_path, 'ctx': ctx}
    await server.close()


def _saved(env):
    return json.loads((env['dir'] / 'options.json').read_text())['ocpp_charge_points']


async def test_pending_adopt_generates_password_and_hot_reloads(env):
    c = env['client']
    with pytest.raises(websockets.InvalidStatus):
        async with connect_sim(env['port'], 'NEU42'):
            pass
    page = await (await c.get('/wallboxes')).text()
    assert 'Wartende Wallboxen' in page and 'NEU42' in page
    assert 'NEU42' in await (await c.get('/wallboxes/new?cp_id=NEU42')).text()
    r = await _post(c, '/wallboxes/new', '/wallboxes/new', {'cp_id': 'NEU42', 'name': 'Hof', 'wallbox_id': 'hof',
                                                             'password': ''})
    assert r.status == 302 and r.headers['Location'] == '/wallbox/NEU42'
    pw = [x for x in _saved(env) if x['id'] == 'NEU42'][0]['password']
    assert len(pw) == 24 and 'NEU42' not in env['server'].pending
    assert pw in await (await c.get('/wallbox/NEU42')).text(), "einmal anzeigen"
    assert pw not in await (await c.get('/wallbox/NEU42')).text()
    assert pw not in (env['dir'] / 'audit.log').read_text()
    async with connect_sim(env['port'], 'NEU42', pw) as cp:   # ohne Neustart angenommen
        assert (await cp.call(call.Heartbeat())) is not None


async def test_duplicate_and_bad_input_rejected(env):
    c = env['client']
    r = await _post(c, '/wallboxes/new', '/wallboxes/new', {'cp_id': 'CP1', 'wallbox_id': 'x', 'password': ''})
    assert 'schon eingetragen' in await r.text()
    r = await _post(c, '/wallboxes/new', '/wallboxes/new', {'cp_id': 'A B', 'wallbox_id': 'x', 'password': ''})
    assert 'Charge-Point-ID' in await r.text()


async def test_edit_keeps_password_and_delete_disconnects(env):
    c = env['client']
    r = await _post(c, '/wallbox/CP1/edit', '/wallbox/CP1/edit', {'name': 'Neu', 'wallbox_id': 'garage2', 'password': ''})
    assert r.status == 302
    assert _saved(env)[0] == {'id': 'CP1', 'name': 'Neu', 'wallbox_id': 'garage2', 'password': CP_PW}
    assert CP_PW not in await (await c.get('/wallbox/CP1/edit')).text()
    async with connect_sim(env['port'], 'CP1', CP_PW) as cp:
        await cp.call(call.Heartbeat())
        r = await _post(c, '/wallbox/CP1', '/wallbox/CP1/delete', {})
        assert r.headers['Location'] == '/wallboxes'
        await asyncio.sleep(0.05)
        assert 'CP1' not in env['server'].connected
    assert _saved(env) == []


async def test_commands_config_and_log(env):
    c = env['client']
    async with connect_sim(env['port'], 'CP1', CP_PW) as cp:
        await cp.call(call.Authorize(id_tag='EFCD083E'))
        page = await (await c.get('/wallbox/CP1')).text()
        assert 'Fernbefehle' in page and 'confirm(' in page
        await _post(c, '/wallbox/CP1', '/wallbox/CP1/command', {'cmd': 'reset', 'type': 'Soft'})
        await _post(c, '/wallbox/CP1', '/wallbox/CP1/command', {'cmd': 'remote_start', 'id_tag': 'efcd083e',
                                                                'connector_id': '1'})
        assert ('reset', 'Soft') in cp.config_changes and ('remote_start', 'EFCD083E') in cp.config_changes
        page = await (await c.get('/wallbox/CP1')).text()
        assert 'Accepted' in page

        await _post(c, '/wallbox/CP1', '/wallbox/CP1/config', {'action': 'get'})
        page = await (await c.get('/wallbox/CP1')).text()
        assert 'ChargePointModel' in page and 'geheim-geheim-123' not in page, "AuthorizationKey maskiert"
        r = await _post(c, '/wallbox/CP1', '/wallbox/CP1/config',
                        {'action': 'set', 'key': 'AuthorizationKey', 'value': 'x' * 20})
        await _post(c, '/wallbox/CP1', '/wallbox/CP1/config', {'action': 'recommended'})
        assert ('MeterValueSampleInterval', '60') not in cp.config_changes, "schon richtig → nicht gesendet"
        assert ('StopTransactionOnInvalidId', 'true') in cp.config_changes
        assert all(k != 'AuthorizationKey' for k, _ in cp.config_changes)

        log = await (await c.get('/wallbox/CP1')).text()
        assert 'Authorize' in log and 'EFCD083E' not in log and 'REDACTED' in log
        assert 'geheim-geheim-123' not in log
    audit = (env['dir'] / 'audit.log').read_text()
    assert 'wallbox_befehl' in audit and 'EFCD083E' not in audit


async def test_command_to_offline_wallbox(env):
    page = await (await env['client'].get('/wallbox/CP1')).text()
    assert 'nur, solange die Wallbox verbunden' in page
    r = await _post(env['client'], '/wallbox/CP1', '/wallbox/CP1/command', {'cmd': 'clear_cache'})
    assert r.status == 302
    assert 'nicht verbunden' in await (await env['client'].get('/wallbox/CP1')).text()


async def test_wallbox_pages_need_login(env):
    c = env['client']
    c.session.cookie_jar.clear()
    r = await c.get('/wallboxes', allow_redirects=False)
    assert r.status == 302 and r.headers['Location'].startswith('/login')
    token = _token(await (await c.get('/login')).text())
    r = await c.post('/wallbox/CP1/command', data={'cmd': 'reset', 'type': 'Hard', '_csrf': token})
    assert r.status == 401
