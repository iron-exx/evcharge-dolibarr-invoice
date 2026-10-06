"""Phase 5: Einstellungen, Admin-Passwort, System-Log, Backup/Wiederherstellung, Systeminfo."""
import io
import json
import logging
import os
import sys
import zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402
import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from admin import system  # noqa: E402
from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.test_admin_web import PW, _post, _token  # noqa: E402
from web_server import create_app  # noqa: E402


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    for k in list(os.environ):
        if k.startswith('EC_'):
            monkeypatch.delenv(k)
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'}}
    (tmp_path / 'options.json').write_text(json.dumps(config))
    sm = SessionManager(db_path=str(tmp_path / 'sessions.db'))
    sm.upsert_tag('EFCD083E', 'Poolwagen', 'business')
    restarts = []
    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678',
                       restart=lambda: restarts.append(1))
    api_state = {'client': None, 'settings': resolve_app_settings(config), 'admin': ctx}
    async with TestClient(TestServer(create_app(sm, config, api_state))) as client:
        await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
        yield {'client': client, 'dir': tmp_path, 'config': config, 'ctx': ctx, 'restarts': restarts, 'sm': sm}
    logging.getLogger().setLevel(logging.WARNING)


def _saved(env):
    return json.loads((env['dir'] / 'options.json').read_text())


async def test_settings_validate_save_and_restart_hint(env):
    c = env['client']
    r = await _post(c, '/settings', '/settings', {'log_level': 'INFO', 'api.transmit_interval': '5'})
    assert 'Übertragung an Dolibarr alle' in await r.text() and 'transmit_interval' not in _saved(env)['api']
    r = await _post(c, '/settings', '/settings', {'log_level': 'DEBUG', 'api.transmit_interval': '600',
                                                  'min_session_kwh': '0,1'})
    text = await r.text()
    assert 'Gespeichert' in text and 'Neustart nötig' in text
    saved = _saved(env)
    assert saved['api']['transmit_interval'] == 600 and saved['min_session_kwh'] == 0.1
    assert saved['api']['api_token'] == 'tok-12345678', "Rest bleibt erhalten"
    assert logging.getLogger().level == logging.DEBUG, "Detailgrad wirkt sofort"
    # leeres Feld → zurück zur Vorgabe, nie null
    await _post(c, '/settings', '/settings', {'log_level': 'INFO', 'api.transmit_interval': ''})
    assert _saved(env)['api']['transmit_interval'] == 300


async def test_password_change_logs_out_others(env):
    c = env['client']
    other = env['ctx'].accounts.issue()
    r = await _post(c, '/settings', '/settings/password', {'password': 'falsch', 'new': 'x' * 12, 'new2': 'x' * 12})
    assert 'Admin-Passwort falsch' in await r.text()
    r = await _post(c, '/settings', '/settings/password', {'password': PW, 'new': 'neues-passwort-1',
                                                           'new2': 'neues-passwort-1'})
    assert 'Passwort geändert' in await r.text()
    assert env['ctx'].accounts.check('admin', 'neues-passwort-1')
    assert env['ctx'].accounts.session_user(other) is None, "andere Sitzungen abgemeldet"
    assert (await c.get('/settings')).status == 200, "diese Sitzung bleibt"


async def test_backup_needs_password_and_contains_data(env):
    c = env['client']
    r = await _post(c, '/settings', '/settings/backup', {'password': 'falsch'})
    assert r.content_type == 'text/html'
    r = await _post(c, '/settings', '/settings/backup', {'password': PW})
    assert r.content_type == 'application/zip'
    z = zipfile.ZipFile(io.BytesIO(await r.read()))
    assert {'options.json', 'sessions.db', 'admin.json', 'secret.key'} <= set(z.namelist())


async def test_restore_roundtrip_and_rejects_garbage(env):
    c = env['client']
    good = system.make_backup(str(env['dir']))
    page = await (await c.get('/settings')).text()
    token = _token(page)

    async def upload(data, password=PW):
        form = aiohttp.FormData()
        form.add_field('file', data, filename='b.zip', content_type='application/zip')
        form.add_field('password', password)
        return await c.post(f'/settings/restore?_csrf={token}', data=form)

    r = await upload(b'kein zip')
    assert 'keine ZIP-Datei' in await r.text()
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, 'w') as z:
        z.writestr('../../etc/passwd', 'x')
    assert 'unerwartete Datei' in await (await upload(evil.getvalue())).text()
    assert 'Admin-Passwort falsch' in await (await upload(good, 'falsch')).text()

    env['ctx'].store.update({'min_session_kwh': 3.0})   # nach dem Backup geändert
    r = await upload(good)
    assert 'Wiederhergestellt' in await r.text()
    assert 'min_session_kwh' not in _saved(env), "Stand aus dem Backup"
    assert list(env['dir'].glob('backup-vor-wiederherstellung-*.zip'))
    import asyncio
    await asyncio.sleep(1.2)
    assert env['restarts'] == [1]


async def test_multipart_without_csrf_rejected(env):
    form = aiohttp.FormData()
    form.add_field('file', b'x', filename='b.zip')
    r = await env['client'].post('/settings/restore', data=form)
    assert r.status == 403


async def test_logs_and_sysinfo(env):
    c = env['client']
    logging.getLogger('test').warning('Wallbox ACE0099 sagt hallo')
    page = await (await c.get('/logs?level=WARNING&q=hallo')).text()
    assert 'ACE0099 sagt hallo' in page
    txt = await (await c.get('/logs.txt?level=WARNING&q=hallo')).text()
    assert 'ACE0099 sagt hallo' in txt
    page = await (await c.get('/settings')).text()
    assert 'Systeminfo' in page and 'Laufzeit' in page and 'tok-12345678' not in page


def test_daily_backup_rotation(tmp_path):
    from datetime import date, timedelta
    (tmp_path / 'options.json').write_text('{"a": 1}')
    day = date(2026, 10, 1)
    for i in range(16):
        assert system.daily_backup(str(tmp_path), today=day + timedelta(days=i), keep=14)
    assert system.daily_backup(str(tmp_path), today=day + timedelta(days=15), keep=14) is None, "einmal pro Tag"
    files = sorted(p.name for p in (tmp_path / 'backups').iterdir())
    assert len(files) == 14 and files[0] == 'expensecharge-20261003.zip' and files[-1] == 'expensecharge-20261016.zip'
    assert oct((tmp_path / 'backups' / files[0]).stat().st_mode & 0o777) == '0o600', "enthält Geheimnisse"
    assert system.backup_status(str(tmp_path))['count'] == 14


async def test_notify_settings_save_mask_and_test(env, monkeypatch):
    from admin import notify
    c = env['client']
    form = {'email_to': 'fuhrpark@firma.de', 'smtp_host': 'mail.firma.de', 'smtp_port': '587', 'smtp_tls': 'starttls',
            'smtp_user': 'ec', 'smtp_password': 'smtp-geheim-1', 'smtp_from': '', 'webhook_url': '',
            'offline_hours': '3', 'pending_hours': '6'}
    r = await _post(c, '/settings', '/settings/notify', {**form, 'email_to': 'kaputt', 'action': 'save'})
    assert 'keine gültige Adresse' in await r.text()
    r = await _post(c, '/settings', '/settings/notify', {**form, 'action': 'save'})
    assert 'gespeichert' in await r.text()
    saved = _saved(env)['notify']
    assert saved['email_to'] == 'fuhrpark@firma.de' and saved['offline_hours'] == 3
    page = await (await c.get('/settings')).text()
    assert 'smtp-geheim-1' not in page and 'smtp-geheim-1' not in (env['dir'] / 'audit.log').read_text()
    await _post(c, '/settings', '/settings/notify', {**form, 'smtp_password': '', 'action': 'save'})
    assert _saved(env)['notify']['smtp_password'] == 'smtp-geheim-1', "leer = behalten"
    sent = []
    monkeypatch.setattr(notify, 'send', lambda cfg, subject, lines: sent.append(cfg) or [])
    r = await _post(c, '/settings', '/settings/notify', {**form, 'smtp_password': '', 'action': 'test'})
    assert 'Testnachricht verschickt' in await r.text() and sent[0]['smtp_password'] == 'smtp-geheim-1'


async def test_tax_flat_price_hot_no_restart(env):
    c = env['client']
    r = await _post(c, '/settings', '/settings', {'log_level': 'INFO', 'tax_flat_price': '0,34'})
    text = await r.text()
    assert 'Gespeichert' in text and 'Neustart nötig' not in text
    assert env['config']['tax_flat_price'] == 0.34, "sofort wirksam für den Ladenachweis"


def test_version_from_config_and_update_compare(monkeypatch):
    monkeypatch.delenv('EXPENSECHARGE_VERSION', raising=False)
    import re
    expected = re.search(r'^version: "([^"]+)"', open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'config.yaml')).read(), re.M).group(1)
    assert system.app_version() == expected
    assert system.newer('2.2.0', '2.1.0') and not system.newer('2.1.0', '2.1.0') and not system.newer('2.0.9', '2.1.0')
    assert system.newer('2.10.0', '2.9.1'), "numerisch, nicht als Text"
    assert not system.newer('kaputt', '2.1.0')
    monkeypatch.setattr(system.requests, 'get', lambda url, timeout: type('R', (), {
        'raise_for_status': lambda self: None, 'text': 'name: x\nversion: "9.9.9"\n'})())
    assert system.fetch_latest_version() == '9.9.9'


async def test_update_hint_on_settings(env):
    env['ctx'].latest_version = '99.0.0'
    page = await (await env['client'].get('/settings')).text()
    assert 'Update verfügbar: 99.0.0' in page and 'git pull' in page
