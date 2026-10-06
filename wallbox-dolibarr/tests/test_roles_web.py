"""Rollen über HTTP: Buchhaltung liest alles, ändert nichts; Mitarbeiter sieht nur die eigenen Ladungen."""
import json
import os
import re
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.test_admin_web import PW, _post  # noqa: E402
from utils.hash import hash_rfid  # noqa: E402
from web_server import create_app  # noqa: E402

MAX, EVA = hash_rfid('04A1B2C3'), hash_rfid('EFCD083E')


def _ins(sm, h, start, kwh):
    conn = sqlite3.connect(sm.db_path)
    conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, start_energy_kwh, end_energy_kwh,"
                 " total_kwh, status, created_at) VALUES (?, 'garage', ?, ?, 100, ?, ?, 'completed', ?)",
                 (h, start, start, 100 + kwh, kwh, start))
    conn.commit()
    conn.close()


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'}}
    (tmp_path / 'options.json').write_text(json.dumps(config))
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    sm.upsert_tag('04A1B2C3', 'Max Müller', 'business')
    sm.upsert_tag('EFCD083E', 'Eva Schmidt', 'business')
    _ins(sm, MAX, '2026-10-01T08:00:00', 12.5)
    _ins(sm, EVA, '2026-10-02T08:00:00', 7.25)
    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678',
                       transmit_now=lambda: None)
    app = create_app(sm, config, {'client': None, 'settings': resolve_app_settings(config), 'admin': ctx})
    async with TestClient(TestServer(app)) as admin:
        await _post(admin, '/setup/1', '/setup/1', {'code': '1234-5678', 'username': 'admin', 'password': PW,
                                                     'password2': PW})
        yield {'admin': admin, 'ctx': ctx, 'app': app}


async def _create(env, data):
    r = await _post(env['admin'], '/settings', '/settings/users', {**data, 'action': 'add'})
    text = await r.text()
    m = re.search(r'id="newpw" readonly value="([^"]+)"', text)
    assert m, re.findall(r"msg (?:err|ok|warn)\">(.{0,200})", text) or text[-800:]
    return m.group(1)


async def _login(env, user, pw):
    c = TestClient(TestServer(env['app']))
    await c.start_server()
    r = await _post(c, '/login', '/login', {'username': user, 'password': pw})
    assert r.status == 302
    return c


async def test_employee_sees_only_own(env):
    pw = await _create(env, {'username': 'mmueller', 'role': 'mitarbeiter', 'cards': MAX[:16]})
    assert pw not in (env['ctx'].data_dir and open(os.path.join(env['ctx'].data_dir, 'audit.log')).read())
    c = await _login(env, 'mmueller', pw)
    try:
        r = await c.get('/', allow_redirects=False)
        assert r.headers['Location'] == '/me'
        me = await (await c.get('/me?month=2026-10')).text()
        assert '12,500' in me and '7,250' not in me, "nur die eigene Karte"
        assert 'Meine Ladungen' in me and 'href="/wallboxes"' not in me and 'href="history"' not in me
        report = await (await c.get('/report?month=2026-10')).text()
        assert 'Max Müller' in report and 'Eva Schmidt' not in report
        csv_text = (await (await c.get('/report.csv?month=2026-10')).read()).decode('utf-8-sig')
        assert 'Eva Schmidt' not in csv_text
        for path in ('/sessions', '/history', '/settings', '/tags', '/wallboxes', '/audit'):
            r = await c.get(path, allow_redirects=False)
            assert r.status == 302 and r.headers['Location'] == '/me', path
        token = c.session.cookie_jar.filter_cookies(c.make_url('/'))['ec_csrf'].value
        assert (await c.post('/tags', data={'tag': 'AAAA1111', '_csrf': token})).status == 403
        r = await _post(c, '/account', '/account', {'password': pw, 'new': 'mein-neues-pw-1', 'new2': 'mein-neues-pw-1'})
        assert 'Passwort geändert' in await r.text()
    finally:
        await c.close()


async def test_accounting_reads_all_changes_nothing(env):
    pw = await _create(env, {'username': 'buchhaltung', 'role': 'buchhaltung'})
    c = await _login(env, 'buchhaltung', pw)
    try:
        assert (await c.get('/', allow_redirects=False)).headers['Location'] == '/sessions'
        page = await (await c.get('/sessions?month=all')).text()
        assert '12,500' in page and '7,250' in page
        report = await (await c.get('/report?month=2026-10')).text()
        assert 'Max Müller' in report and 'Eva Schmidt' in report
        assert (await c.get('/settings', allow_redirects=False)).status == 302
        token = c.session.cookie_jar.filter_cookies(c.make_url('/'))['ec_csrf'].value
        for path in ('/sessions/transmit', '/sessions/1/discard', '/settings', '/tags'):
            assert (await c.post(path, data={'_csrf': token})).status == 403, path
        # Abmelden beendet nur die eigene Sitzung
        await _post(c, '/sessions', '/logout', {})
        assert (await env['admin'].get('/settings')).status == 200
    finally:
        await c.close()


async def test_admin_resets_and_deletes(env):
    pw = await _create(env, {'username': 'buha', 'role': 'buchhaltung'})
    r = await _post(env['admin'], '/settings', '/settings/users', {'username': 'buha', 'action': 'reset'})
    new = re.search(r'id="newpw" readonly value="([^"]+)"', await r.text()).group(1)
    assert new != pw and env['ctx'].accounts.check('buha', new)
    await _post(env['admin'], '/settings', '/settings/users', {'username': 'buha', 'action': 'delete'})
    assert env['ctx'].accounts.role('buha') is None
