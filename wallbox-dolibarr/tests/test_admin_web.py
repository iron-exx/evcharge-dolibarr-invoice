"""Phase 1 über HTTP: Ersteinrichtung, Anmeldung, CSRF, Sperre, Assistent."""
import json
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import admin.web as admin_web  # noqa: E402
from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from web_server import create_app  # noqa: E402

PW = 'richtig-langes-pw'


def _token(html):
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert m, "jedes POST-Formular muss das CSRF-Feld tragen"
    return m.group(1)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    for k in list(os.environ):
        if k.startswith('EC_'):
            monkeypatch.delenv(k)
    template = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                           'options.standalone.example.json')))
    template.pop('web_auth', None)
    (tmp_path / 'options.json').write_text(json.dumps(template))
    config = json.loads(json.dumps(template))
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    calls = {'dolibarr': [], 'ocpp': 0, 'restart': 0}

    def reload_ocpp():
        calls['ocpp'] += 1
        return True

    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm,
                       setup_code='1234-5678', ocpp_port=9000,
                       reload_dolibarr=lambda u, t: calls['dolibarr'].append((u, t)),
                       reload_ocpp=reload_ocpp,
                       restart=lambda: calls.__setitem__('restart', calls['restart'] + 1))
    api_state = {'client': None, 'current_energy': None, 'wallbox_state': None, 'last_update': None,
                 'settings': resolve_app_settings(config), 'admin': ctx}
    return {'app': create_app(sm, config, api_state), 'ctx': ctx, 'calls': calls, 'dir': tmp_path, 'sm': sm}


@pytest.fixture()
async def client(env):
    async with TestClient(TestServer(env['app'])) as c:
        yield c


async def _post(client, page, action, data):
    token = _token(await (await client.get(page)).text())
    return await client.post(action, data={**data, '_csrf': token}, allow_redirects=False)


async def _setup_admin(client):
    r = await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
    assert r.status == 302 and r.headers['Location'] == '/setup/2'


# -- ohne Konto ------------------------------------------------------------------

async def test_first_visit_starts_wizard(client):
    r = await client.get('/', allow_redirects=False)
    assert r.status == 302 and r.headers['Location'] == '/setup'
    r = await client.get('/setup', allow_redirects=False)
    assert r.headers['Location'] == '/setup/1'


async def test_without_account_read_only(client):
    assert (await client.get('/history')).status == 200, "Lesen bleibt möglich"
    token = _token(await (await client.get('/setup/1')).text())
    r = await client.post('/tags', data={'tag': 'EFCD083E', '_csrf': token})
    assert r.status == 403
    assert 'Ersteinrichtung' in await r.text()


async def test_wrong_setup_code_rejected_and_locked(client, env):
    for _ in range(5):
        r = await _post(client, '/setup/1', '/setup/1',
                        {'code': '0000-0000', 'username': 'admin', 'password': PW, 'password2': PW})
        assert 'Einrichtungscode falsch' in await r.text()
    r = await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
    assert 'Fehlversuche' in await r.text()
    assert not env['ctx'].accounts.exists()


async def test_health_open(client):
    assert (await (await client.get('/health')).json()) == {'status': 'ok'}


# -- Konto anlegen, CSRF, Anmeldung ------------------------------------------------

async def test_csrf_required_for_every_post(client):
    await client.get('/setup/1')   # Cookie holen
    r = await client.post('/setup/1', data={'code': '1234-5678'})
    assert r.status == 403 and 'CSRF' in await r.text()
    r = await client.post('/setup/1', data={'code': '1234-5678', '_csrf': 'gefälscht'})
    assert r.status == 403


async def test_session_cookie_flags(client):
    await client.get('/setup/1')
    token = client.session.cookie_jar.filter_cookies(client.make_url('/'))['ec_csrf'].value
    r = await client.post('/setup/1', allow_redirects=False, data={
        'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW, '_csrf': token})
    cookie = r.headers.getall('Set-Cookie')
    session = [c for c in cookie if c.startswith('ec_session=')][0]
    assert 'HttpOnly' in session and 'SameSite=Strict' in session


async def test_setup_creates_account_logs_in_and_audits(client, env):
    await _setup_admin(client)
    assert env['ctx'].accounts.exists()
    assert env['ctx'].setup_code is None, "Code ist nach Verwendung verbraucht"
    assert (await client.get('/setup/2')).status == 200, "direkt angemeldet"
    assert env['ctx'].audit.entries()[0]['field'] == 'admin_konto'
    assert PW not in (env['dir'] / 'admin.json').read_text()


async def test_after_setup_everything_needs_login(client, env):
    await _setup_admin(client)
    client.session.cookie_jar.clear()
    r = await client.get('/history', allow_redirects=False)
    assert r.status == 302 and r.headers['Location'].startswith('/login')
    assert (await client.get('/live.json')).status == 401
    assert (await client.get('/health')).status == 200


async def test_login_logout_and_lockout(client, env):
    await _setup_admin(client)
    client.session.cookie_jar.clear()
    for _ in range(5):
        r = await _post(client, '/login', '/login', {'username': 'admin', 'password': 'falsch'})
        assert 'falsch' in await r.text()
    r = await _post(client, '/login', '/login', {'username': 'admin', 'password': PW})
    assert 'Fehlversuche' in await r.text(), "auch das richtige Passwort ist 5 min gesperrt"
    env['ctx'].limiter.success('127.0.0.1')
    r = await _post(client, '/login', '/login', {'username': 'admin', 'password': PW, 'next': '//evil.com'})
    assert r.status == 302 and r.headers['Location'] == '/', "keine Weiterleitung auf fremde Seiten"
    assert (await client.get('/history')).status == 200
    r = await _post(client, '/history', '/logout', {})
    assert r.headers['Location'] == '/login'
    assert (await client.get('/live.json')).status == 401


# -- Assistent -------------------------------------------------------------------

async def test_placeholders_send_logged_in_user_to_wizard(client):
    await _setup_admin(client)
    r = await client.get('/', allow_redirects=False)
    assert r.headers['Location'] == '/setup/2'


async def test_step2_validates_saves_masks_and_hot_reloads(client, env):
    await _setup_admin(client)
    r = await _post(client, '/setup/2', '/setup/2',
                    {'dolibarr_url': 'https://erp.example.com', 'api_token': 'tok-12345678', 'action': 'save'})
    assert 'Platzhalter' in await r.text()
    r = await _post(client, '/setup/2', '/setup/2',
                    {'dolibarr_url': 'https://erp.firma.de/', 'api_token': 'tok-12345678', 'action': 'save'})
    assert r.headers['Location'] == '/setup/3'
    saved = json.loads((env['dir'] / 'options.json').read_text())
    assert saved['api']['dolibarr_url'] == 'https://erp.firma.de'
    assert env['calls']['dolibarr'] == [('https://erp.firma.de', 'tok-12345678')]
    page = await (await client.get('/setup/2')).text()
    assert 'tok-12345678' not in page and '••••' in page, "Token nie im Klartext ausliefern"
    audit = (env['dir'] / 'audit.log').read_text()
    assert 'tok-12345678' not in audit and 'api.api_token' in audit
    # leer gelassenes Token behält das gespeicherte
    r = await _post(client, '/setup/2', '/setup/2',
                    {'dolibarr_url': 'https://erp2.firma.de', 'api_token': '', 'action': 'save'})
    assert json.loads((env['dir'] / 'options.json').read_text())['api']['api_token'] == 'tok-12345678'


async def test_step2_connection_test_shows_steps(client, monkeypatch):
    await _setup_admin(client)
    monkeypatch.setattr(admin_web, 'check_dolibarr', lambda url, token: [
        {'step': 'DNS', 'ok': False, 'detail': 'Name erp.firma.de nicht auflösbar'}])
    r = await _post(client, '/setup/2', '/setup/2',
                    {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678', 'action': 'test'})
    text = await r.text()
    assert 'nicht auflösbar' in text and 'tok-12345678' not in text


async def test_step3_generates_password_shown_once(client, env):
    await _setup_admin(client)
    r = await _post(client, '/setup/3', '/setup/3',
                    {'cp_id': 'ACE0099', 'name': 'Garage', 'wallbox_id': 'garage', 'password': ''})
    assert r.headers['Location'] == '/setup/4'
    cps = json.loads((env['dir'] / 'options.json').read_text())['ocpp_charge_points']
    assert [c['id'] for c in cps] == ['ACE0099'], "Vorlagen-Eintrag ersetzt"
    pw = cps[0]['password']
    assert len(pw) == 24
    assert env['calls']['ocpp'] == 1, "Hot-Reload versucht"
    summary = await (await client.get('/setup/5')).text()
    assert pw in summary and 'ws://127.0.0.1:9000/' in summary
    assert pw not in await (await client.get('/setup/5')).text(), "nur einmal im Klartext"
    assert pw not in (env['dir'] / 'audit.log').read_text()


async def test_step3_mode_change_requires_restart(client, env):
    await _setup_admin(client)
    env['ctx'].config['session_source'] = 'alfen_http'
    await _post(client, '/setup/3', '/setup/3',
                {'cp_id': 'ACE0099', 'name': '', 'wallbox_id': 'garage', 'password': 'x' * 20})
    summary = await (await client.get('/setup/5')).text()
    assert 'neu starten' in summary
    r = await _post(client, '/setup/5', '/restart', {})
    assert r.status == 200
    import asyncio
    await asyncio.sleep(1.2)
    assert env['calls']['restart'] == 1


async def test_step3_rejects_bad_input(client):
    await _setup_admin(client)
    r = await _post(client, '/setup/3', '/setup/3',
                    {'cp_id': 'a/b', 'name': '', 'wallbox_id': 'garage', 'password': ''})
    assert 'Charge-Point-ID' in await r.text()
    r = await _post(client, '/setup/3', '/setup/3',
                    {'cp_id': 'CP1', 'name': '', 'wallbox_id': 'garage', 'password': 'kurz'})
    assert 'OCPP-Passwort' in await r.text()


async def test_step4_cards_become_business_tags(client, env):
    await _setup_admin(client)
    r = await _post(client, '/setup/4', '/setup/4', {'cards': 'EFCD083E; Firmenwagen\n04a1b2c3'})
    assert r.headers['Location'] == '/setup/5'
    assert env['sm'].is_tag_billable('EFCD083E') is True
    assert env['sm'].get_tag('04A1B2C3')['mode'] == 'business'
    assert 'EFCD083E' not in (env['dir'] / 'audit.log').read_text(), "keine Karten-ID im Klartext"


async def test_audit_page(client):
    await _setup_admin(client)
    text = await (await client.get('/audit')).text()
    assert 'admin_konto' in text


async def test_env_override_warning(client, monkeypatch):
    await _setup_admin(client)
    monkeypatch.setenv('EC_API__API_TOKEN', 'aus-env-123')
    text = await (await client.get('/setup/2')).text()
    assert 'EC_API__API_TOKEN' in text and 'Vorrang' in text


async def test_ha_addon_has_no_login(tmp_path, monkeypatch):
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    api_state = {'client': None, 'settings': resolve_app_settings({})}
    async with TestClient(TestServer(create_app(sm, {}, api_state))) as c:
        assert (await c.get('/system.json')).status == 200
        assert (await c.get('/setup')).status == 404
