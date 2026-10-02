"""Optionale Anmeldung (HTTP Basic Auth) für die Web-UI im Standalone-Betrieb.

Standalone fehlt der HA-Ingress mit Login. Wer die UI per WEB_BIND=0.0.0.0
ins LAN/VPN stellt, braucht einen eigenen Schutz.
"""
import base64
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from web_server import create_app  # noqa: E402

AUTH = {'username': 'admin', 'password': 's3cret'}


def _hdr(user, pw):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{pw}'.encode()).decode()}


async def _client(tmp_path, config):
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    api_state = {'client': None, 'current_energy': None, 'wallbox_state': None,
                 'last_update': None, 'settings': resolve_app_settings(config)}
    return TestClient(TestServer(create_app(sm, config, api_state)))


@pytest.fixture()
async def protected(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    async with await _client(tmp_path, {'web_auth': AUTH}) as c:
        yield c


@pytest.mark.parametrize('method,path', [('get', '/'), ('get', '/live.json'), ('get', '/tags.json'),
                                         ('get', '/system.json'), ('get', '/export'),
                                         ('post', '/transmit'), ('post', '/learn')])
async def test_every_route_needs_login(protected, method, path):
    r = await getattr(protected, method)(path)
    assert r.status == 401
    assert 'Basic' in r.headers['WWW-Authenticate']


async def test_wrong_password_rejected(protected):
    assert (await protected.get('/', headers=_hdr('admin', 'falsch'))).status == 401


async def test_correct_login_passes(protected):
    assert (await protected.get('/system.json', headers=_hdr('admin', 's3cret'))).status == 200


async def test_health_is_open(protected):
    r = await protected.get('/health')
    assert r.status == 200
    assert (await r.json())['status'] == 'ok'


async def test_without_web_auth_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    async with await _client(tmp_path, {}) as c:
        assert (await c.get('/system.json')).status == 200


async def test_ha_addon_ignores_web_auth(tmp_path, monkeypatch):
    """Im Addon schützt der Ingress; Basic Auth würde ihn nur stören."""
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'x')
    async with await _client(tmp_path, {'web_auth': AUTH}) as c:
        assert (await c.get('/system.json')).status == 200


def test_exposed_without_auth_warns(monkeypatch, caplog):
    import main
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    monkeypatch.setenv('WEB_BIND', '0.0.0.0')
    main.warn_if_web_exposed({})
    assert any(r.levelname == 'WARNING' and 'web_auth' in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize('bind,cfg,sup', [('127.0.0.1', {}, None), ('0.0.0.0', {'web_auth': AUTH}, None),
                                          ('0.0.0.0', {}, 'x'), (None, {}, None)])
def test_no_warning_when_safe(monkeypatch, caplog, bind, cfg, sup):
    import main
    for k, v in (('WEB_BIND', bind), ('SUPERVISOR_TOKEN', sup)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    main.warn_if_web_exposed(cfg)
    assert not [r for r in caplog.records if r.levelname == 'WARNING']
