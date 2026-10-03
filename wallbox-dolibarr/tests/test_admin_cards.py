"""Phase 3: Kartenverwaltung — von Hand anlegen, umbenennen/umordnen, Mitarbeiter, alte Whitelist."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from admin.web import AdminContext  # noqa: E402
from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.test_admin_web import PW, _post  # noqa: E402
from web_server import create_app  # noqa: E402


class FakeDolibarr:
    def list_employees(self):
        return [{'login': 'mmueller', 'name': 'Max Müller'}]


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    config = {'session_source': 'ocpp', 'rfid_whitelist': ['efcd083e', 'AABBCCDD'],
              'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'}}
    (tmp_path / 'options.json').write_text(json.dumps(config))
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    ctx = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678')
    api_state = {'client': FakeDolibarr(), 'settings': resolve_app_settings(config), 'admin': ctx}
    async with TestClient(TestServer(create_app(sm, config, api_state))) as client:
        await _post(client, '/setup/1', '/setup/1',
                    {'code': '1234-5678', 'username': 'admin', 'password': PW, 'password2': PW})
        yield {'client': client, 'sm': sm, 'dir': tmp_path, 'config': config}


async def test_manual_add_validates_normalizes_and_audits(env):
    c = env['client']
    r = await _post(c, '/tags', '/tags', {'manual': '1', 'tag': 'ab-12', 'label': '', 'mode': 'business'})
    assert 'Karten-ID' in await r.text()
    r = await _post(c, '/tags', '/tags', {'manual': '1', 'tag': ' 04a1b2c3 ', 'label': 'Max Müller',
                                          'mode': 'private'})
    assert r.status == 302
    tag = env['sm'].get_tag('04A1B2C3')
    assert tag['mode'] == 'private' and tag['label'] == 'Max Müller'
    audit = (env['dir'] / 'audit.log').read_text()
    assert 'karte' in audit and '04A1B2C3' not in audit


async def test_update_by_hash_prefix(env):
    c = env['client']
    saved = env['sm'].upsert_tag('EFCD083E', label=None, mode='unknown')
    r = await _post(c, '/tags', '/tags/update', {'hash_prefix': saved['rfid_hash'][:16], 'label': 'Poolwagen',
                                                 'mode': 'business'})
    assert r.status == 302
    assert env['sm'].get_tag('EFCD083E')['label'] == 'Poolwagen'
    assert env['sm'].get_tag('EFCD083E')['mode'] == 'business'
    r = await _post(c, '/tags', '/tags/update', {'hash_prefix': 'kurz', 'label': 'x', 'mode': 'business'})
    assert r.status == 404


async def test_employees_offered_as_names(env):
    page = await (await env['client'].get('/tags')).text()
    assert 'Mitarbeiter in Dolibarr' in page and 'Max Müller' in page and 'ec-employees' in page


async def test_whitelist_import(env):
    c = env['client']
    page = await (await c.get('/tags')).text()
    assert 'Alte Whitelist' in page
    r = await _post(c, '/tags', '/tags/import-whitelist', {})
    assert '2 Karte(n) übernommen' in await r.text()
    assert env['sm'].get_tag('EFCD083E')['mode'] == 'business'
    assert env['sm'].get_tag('AABBCCDD')['mode'] == 'business'
    assert json.loads((env['dir'] / 'options.json').read_text())['rfid_whitelist'] == []
    assert env['config']['rfid_whitelist'] == []
    assert 'EFCD083E' not in (env['dir'] / 'audit.log').read_text()
    assert 'Alte Whitelist' not in await (await c.get('/tags')).text()
