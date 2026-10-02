"""Platzhalter aus options.standalone.example.json erkennen.

Praxisbefund: mit erp.example.com im Dolibarr-Feld gab es beim Start fünf
Retry-Warnungen mit NameResolutionError — die Ursache stand nirgends.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import websockets  # noqa: E402

from placeholders import find_placeholders, is_placeholder_password  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_unchanged_template_reports_every_placeholder():
    with open(os.path.join(HERE, 'options.standalone.example.json')) as f:
        template = json.load(f)
    found = find_placeholders(template)
    assert 'api.dolibarr_url' in found
    assert 'api.api_token' in found
    assert 'ocpp_charge_points[ACE0123456].password' in found


def test_real_values_report_nothing():
    cfg = {'api': {'dolibarr_url': 'https://erp.firma.de', 'api_token': 'x7Gk2'},
           'ocpp_charge_points': [{'id': 'ACE1', 'password': 'a1b2c3d4e5f6a1b2c3d4e5f6'}]}
    assert find_placeholders(cfg) == []


def test_addon_default_values_are_placeholders_too():
    cfg = {'api': {'dolibarr_url': 'https://dolibarr.example.com', 'api_token': 'your_dolapikey_here'}}
    assert find_placeholders(cfg) == ['api.dolibarr_url', 'api.api_token']


def test_placeholder_password():
    assert is_placeholder_password('bitte-mindestens-16-zeichen')
    assert not is_placeholder_password('a1b2c3d4e5f6a1b2c3d4e5f6')
    assert not is_placeholder_password('')


async def test_ocpp_rejects_template_password(tmp_path):
    from ocpp_server.central_system import CentralSystemDeps
    from ocpp_server.server import OcppServer
    from ocpp_server.settings import resolve_ocpp_settings
    from session_manager import SessionManager
    from tests.ocpp_sim import connect_sim
    pw = 'bitte-mindestens-16-zeichen'
    settings = resolve_ocpp_settings({'session_source': 'ocpp',
                                      'ocpp_charge_points': [{'id': 'CP1', 'password': pw}]})
    deps = CentralSystemDeps(session_manager=SessionManager(db_path=str(tmp_path / 's.db')),
                             whitelist=[], live={}, on_session_completed=lambda s: None)
    server = OcppServer(settings, deps)
    port = await server.start('127.0.0.1', 0)
    try:
        with pytest.raises(websockets.InvalidStatus) as exc:
            async with connect_sim(port, 'CP1', pw):
                pass
        assert exc.value.response.status_code == 401
    finally:
        await server.close()
