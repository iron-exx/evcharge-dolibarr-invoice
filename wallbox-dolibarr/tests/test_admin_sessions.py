"""Phase 4: Ladevorgänge — Filter, CSV, Übertragung anstoßen, unvollständige nachbearbeiten."""
import json
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.test_admin_web import PW, _post  # noqa: E402
from web_server import create_app  # noqa: E402


def _insert(sm, start, kwh, status, transmitted=None, rfid='h' * 64):
    conn = sqlite3.connect(sm.db_path)
    cur = conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, total_kwh, status, "
                       "created_at, transmitted_at) VALUES (?, 'garage', ?, ?, ?, ?, ?, ?)",
                       (rfid, start, start, kwh, status, start, transmitted))
    conn.commit()
    conn.close()
    return cur.lastrowid


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'}}
    (tmp_path / 'options.json').write_text(json.dumps(config))
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    calls = []
    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678',
                       transmit_now=lambda: calls.append(1))
    api_state = {'client': None, 'settings': resolve_app_settings(config), 'admin': ctx}
    ids = {'done': _insert(sm, '2026-09-03T08:00:00', 12.5, 'completed', '2026-09-03T09:00:00'),
           'pending': _insert(sm, '2026-09-10T08:00:00', 7.25, 'completed'),
           'broken': _insert(sm, '2026-09-11T08:00:00', None, 'incomplete'),
           'old': _insert(sm, '2026-08-01T08:00:00', 3.0, 'completed', '2026-08-01T09:00:00')}
    sm.upsert_tag('EFCD083E', 'Poolwagen', 'business')
    async with TestClient(TestServer(create_app(sm, config, api_state))) as client:
        await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
        yield {'client': client, 'sm': sm, 'ids': ids, 'calls': calls, 'dir': tmp_path, 'api_state': api_state}


async def test_list_filters_and_counts(env):
    c = env['client']
    page = await (await c.get('/sessions?month=2026-09')).text()
    assert '3 Ladung(en)' in page and 'abrechenbar 19,750 kWh' in page
    assert 'unvollständige Ladung' in page
    page = await (await c.get('/sessions?month=all&status=pending')).text()
    assert '1 Ladung(en)' in page and '7,250' in page
    page = await (await c.get('/sessions?month=../../x&status=evil')).text()
    assert page, "ungültige Filter fallen auf Standard zurück"


async def test_csv_export(env):
    r = await env['client'].get('/sessions.csv?month=2026-09&status=')
    assert r.headers['Content-Disposition'].endswith('ladevorgaenge_2026-09.csv"')
    text = (await r.read()).decode('utf-8-sig')
    lines = text.strip().splitlines()
    assert lines[0].startswith('Nr;Beginn;Ende;kWh;Karte') and len(lines) == 4
    assert '12,500' in text and 'übertragen' in text and 'unvollständig' in text


async def test_resolve_incomplete_then_transmit(env):
    c, sid = env['client'], env['ids']['broken']
    r = await _post(c, '/sessions', f'/sessions/{sid}/resolve', {'kwh': 'abc'})
    assert 'Zahl angeben' in await (await c.get('/sessions')).text()
    r = await _post(c, '/sessions', f'/sessions/{sid}/resolve', {'kwh': '9,5'})
    assert r.status == 302
    s = [x for x in env['sm'].list_sessions() if x['id'] == sid][0]
    assert s['status'] == 'completed' and s['total_kwh'] == 9.5 and 'manuell' in s['stop_reason']
    assert env['calls'] == [1], "gleich zur Übertragung angestoßen"
    assert 'ladevorgang' in (env['dir'] / 'audit.log').read_text()


async def test_discard_only_untransmitted(env):
    c = env['client']
    await _post(c, '/sessions', f'/sessions/{env["ids"]["pending"]}/discard', {})
    await _post(c, '/sessions', f'/sessions/{env["ids"]["done"]}/discard', {})
    by_id = {x['id']: x for x in env['sm'].list_sessions()}
    assert by_id[env['ids']['pending']]['status'] == 'discarded'
    assert by_id[env['ids']['done']]['status'] == 'completed', "übertragene bleiben unangetastet"


async def test_transmit_button_and_last_run(env):
    c = env['client']
    r = await _post(c, '/sessions', '/sessions/transmit', {})
    assert r.status == 302 and env['calls'] == [1]
    env['api_state']['last_transmit'] = {'time': '2026-10-03T10:00:00', 'transmitted': 2, 'failed': 1,
                                         'error': 'Session 5: HTTP 500'}
    page = await (await c.get('/sessions')).text()
    assert '2 übertragen, 1 fehlgeschlagen' in page and 'HTTP 500' in page


async def test_redirect_never_leaves_host(env):
    c = env['client']
    token = c.session.cookie_jar.filter_cookies(c.make_url('/'))['ec_csrf'].value
    r = await c.post('/sessions/transmit', data={'_csrf': token}, allow_redirects=False,
                     headers={'Referer': 'https://evil.com/sessions?x=1'})
    assert r.headers['Location'] == '/sessions?x=1', "nur Pfad/Filter, nie der fremde Host"
