"""System-Tab: wirksame Konfiguration und Diagnose.

Mit knapp 40 Optionen muss nachsehbar sein, was tatsächlich gilt — sonst rät
man. Die Seite ist bewusst NUR lesend: die Konfiguration gehört Home Assistant
bzw. der options.json, nicht der Oberfläche.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app_settings import resolve_app_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from web_server import create_app  # noqa: E402


@pytest.fixture()
async def client(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    config = {'session_source': 'ocpp', 'debounce_seconds': 12,
              'wallbox_id': 'garage', 'max_plausible_kw': 350}
    api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                 "last_update": None, "settings": resolve_app_settings(config)}
    async with TestClient(TestServer(create_app(sm, config, api_state))) as c:
        yield c


async def test_system_page_shows_the_effective_configuration(client):
    body = await (await client.get("/system")).text()
    assert "RFID-Entprellung" in body
    assert ">12<" in body or ">12.0<" in body, "geänderter Wert muss angezeigt werden"
    assert "Plausibilitätsgrenze" in body


async def test_changed_values_are_marked(client):
    body = await (await client.get("/system")).text()
    assert "abweichend" in body.lower()


async def test_system_json_lists_settings_and_diagnostics(client):
    data = await (await client.get("/system.json")).json()
    rows = {r['key']: r for r in data['settings']}
    assert rows['debounce_seconds']['value'] == 12
    assert rows['debounce_seconds']['changed'] is True
    assert rows['web_port']['changed'] is False
    assert data['diagnostics']['session_source'] == 'ocpp'
    assert 'database' in data['diagnostics']
    assert 'sessions' in data['diagnostics']


async def test_system_page_does_not_leak_secrets(tmp_path):
    """Token und Zugangsdaten dürfen nie in der Oberfläche landen."""
    sm = SessionManager(db_path=str(tmp_path / "s2.db"))
    config = {'api': {'dolibarr_url': 'https://erp.example.com',
                      'api_token': 'GEHEIMTOKEN1234567890'},
              'ha_token': 'HAGEHEIMTOKEN',
              'ocpp_charge_points': [{'id': 'CP1', 'password': 'WALLBOXGEHEIM'}]}
    api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                 "last_update": None, "settings": resolve_app_settings(config)}
    async with TestClient(TestServer(create_app(sm, config, api_state))) as c:
        body = await (await c.get("/system")).text()
        data = await (await c.get("/system.json")).json()
    blob = body + str(data)
    for secret in ('GEHEIMTOKEN1234567890', 'HAGEHEIMTOKEN', 'WALLBOXGEHEIM'):
        assert secret not in blob, f"Geheimnis in der Oberfläche: {secret}"
    assert 'erp.example.com' in body, \
        "die URL selbst ist kein Geheimnis und hilft bei der Diagnose"


async def test_system_tab_appears_in_the_navigation(client):
    body = await (await client.get("/")).text()
    assert 'href="system"' in body
