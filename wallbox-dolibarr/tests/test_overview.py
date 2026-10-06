"""Startseite: Ampel („Alles in Ordnung“ / was zu tun ist) und Einrichtungs-Checkliste."""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from admin import overview  # noqa: E402
from admin.system import daily_backup  # noqa: E402
from admin.web import AdminContext  # noqa: E402
from session_manager import SessionManager  # noqa: E402


class Server:
    def __init__(self, live):
        self.live, self.charge_points = live, {}


@pytest.fixture()
def ctx(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / 'sessions.db'))
    (tmp_path / 'options.json').write_text('{}')
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'},
              'ocpp_charge_points': [{'id': 'ACE1', 'name': 'Wallbox 1', 'password': 'x' * 16}]}
    live = {}
    c = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, ocpp=lambda: Server(live))
    c.api_state = {'client': object()}
    c._live = live
    return c


def test_checklist_until_first_transmission(ctx):
    st = overview.status(ctx)
    steps = {s['label']: s['done'] for s in st['checklist']}
    assert steps == {'Dolibarr verbunden': True, 'Wallbox eingetragen': True, 'Wallbox hat sich verbunden': False,
                     'Karte angelernt': False, 'Erste Ladung an Dolibarr übertragen': False}
    ctx._live['ACE1'] = {'connected': True, 'last_seen': '2026-10-06T10:00:00'}
    ctx.session_manager.upsert_tag('04A1B2C3', 'Max', 'business')
    conn = sqlite3.connect(ctx.session_manager.db_path)
    conn.execute("INSERT INTO sessions (rfid_hash, wallbox_id, start_time, end_time, total_kwh, status, created_at,"
                 " transmitted_at) VALUES ('a', 'w', '2026-10-01T08:00', '2026-10-01T09:00', 5, 'completed', 'x', 'y')")
    conn.commit()
    conn.close()
    assert overview.status(ctx)['checklist'] is None, "alles erledigt → Checkliste verschwindet"


def test_traffic_light(ctx):
    daily_backup(ctx.data_dir)
    ctx._live['ACE1'] = {'connected': True, 'last_seen': '2026-10-06T10:00:00'}
    assert overview.status(ctx)['problems'] == []
    ctx.api_state['client'] = None
    problems = overview.status(ctx)['problems']
    assert problems and 'Dolibarr' in problems[0]['text'] and problems[0]['link'] == '/setup/2'


def test_rendered_on_start_page_for_admin(ctx):
    html = overview.render(overview.status(ctx))
    assert 'Erste Schritte' in html and 'von 5 erledigt' in html


async def test_start_page_shows_overview_for_admin(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERVISOR_TOKEN', raising=False)
    from aiohttp.test_utils import TestClient, TestServer
    from app_settings import resolve_app_settings
    from tests.test_admin_web import PW, _post
    from web_server import create_app
    config = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'tok-12345678'}}
    (tmp_path / 'options.json').write_text('{}')
    sm = SessionManager(db_path=str(tmp_path / 's.db'))
    c = AdminContext(data_dir=str(tmp_path), config=config, session_manager=sm, setup_code='1234-5678')
    async with TestClient(TestServer(create_app(sm, config, {'client': None, 'settings': resolve_app_settings(config),
                                                             'admin': c}))) as client:
        await _post(client, '/setup/1', '/setup/1', {'code': '1234-5678', 'username': 'admin', 'password': PW,
                                                      'password2': PW})
        page = await (await client.get('/')).text()
        assert 'Erste Schritte' in page and 'Dolibarr nicht verbunden' in page
