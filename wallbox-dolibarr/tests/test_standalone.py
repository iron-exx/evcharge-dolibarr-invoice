"""Standalone-Betrieb in Docker, ohne Home Assistant.

Ohne HA gibt es keinen Supervisor, der /data/options.json schreibt, und keinen
HA-Websocket. Nur die Betriebsart `ocpp` funktioniert dort — und genau das muss
die mitgelieferte Beispielkonfiguration hergeben, sonst scheitert jeder, der
der Doku folgt.
"""
import pytest
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(HERE, 'options.standalone.example.json')
COMPOSE = os.path.join(HERE, 'docker-compose.yml')


def _example():
    with open(EXAMPLE, encoding='utf-8') as f:
        return json.load(f)


def test_example_config_enables_ocpp_mode():
    settings = resolve_ocpp_settings(_example())
    assert settings.enabled is True, "Beispiel muss session_source: ocpp setzen"
    assert settings.charge_points, "Beispiel braucht mindestens eine Wallbox"


def test_example_config_uses_no_home_assistant_keys():
    """Sensoren und ha_token sind im Standalone-Betrieb wirkungslos — sie im
    Beispiel zu zeigen würde in die Irre führen."""
    cfg = _example()
    for key in ('ha_token', 'sensor_rfid', 'sensor_energy', 'sensor_state'):
        assert key not in cfg, f"{key} gehört nicht in die Standalone-Beispielkonfiguration"


def test_example_config_only_uses_known_options():
    """Jeder Schlüssel muss es auch in config.yaml geben, sonst driftet das
    Beispiel von der echten Konfiguration weg."""
    known = set(yaml.safe_load(open(os.path.join(HERE, 'config.yaml'), encoding='utf-8'))['options'])
    # web_auth gibt es nur standalone; im Addon schützt der Ingress.
    unknown = set(_example()) - known - {'web_auth'}
    assert not unknown, f"unbekannte Schlüssel im Beispiel: {sorted(unknown)}"


def test_compose_maps_ocpp_port_and_persists_data():
    compose = yaml.safe_load(open(COMPOSE, encoding='utf-8'))
    svc = compose['services']['expensecharge']
    assert any(str(p).startswith('9000:') or ':9000' in str(p) for p in svc['ports']), \
        "OCPP-Port 9000 muss veröffentlicht werden, sonst erreicht keine Wallbox den Server"
    assert any('/data' in str(v) for v in svc['volumes']), \
        "/data muss ein Volume sein, sonst ist die SQLite-DB nach jedem Neustart leer"
    assert 'TZ' in svc.get('environment', {}), \
        "ohne TZ laufen die Zeitstempel in UTC und die Ladung landet im falschen Abrechnungsmonat"


def test_compose_does_not_publish_the_web_ui_publicly():
    """Im HA-Betrieb schützt der Ingress die Web-UI mit dem HA-Login. Standalone
    gibt es diesen Schutz NICHT — die UI darf daher nicht offen im Netz hängen."""
    compose = yaml.safe_load(open(COMPOSE, encoding='utf-8'))
    svc = compose['services']['expensecharge']
    ui = [str(p) for p in svc['ports'] if '8099' in str(p)]
    assert ui, "Web-UI-Port sollte vorhanden, aber gebunden sein"
    # Per WEB_BIND aus der .env überschreibbar, Vorgabe bleibt lokal.
    assert all(p.startswith(('127.0.0.1:', '${WEB_BIND:-127.0.0.1}:')) for p in ui), \
        f"Web-UI muss standardmäßig an 127.0.0.1 gebunden sein (ist: {ui})"


async def test_missing_token_names_the_standalone_option(tmp_path, monkeypatch, caplog):
    """Wer den Container ohne HA startet und session_source auf dem Default
    'ha_sensors' lässt, bekommt nur 'Kein HA-Token verfügbar' — und kommt nicht
    auf die Lösung. Die Meldung muss den Standalone-Weg benennen.
    """
    import main
    from session_manager import SessionManager

    class _NoWs:
        def __init__(self, *a, **kw):
            raise AssertionError("ohne Token darf kein HA-Websocket aufgebaut werden")

    monkeypatch.setattr(main, "SessionManager", lambda db_path, **kw: SessionManager(db_path=str(tmp_path / "s.db")))
    monkeypatch.setattr(main, "load_config", lambda: {"rfid_whitelist": []})
    monkeypatch.setattr(main, "HomeAssistantWebsocket", _NoWs)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    caplog.set_level("ERROR")

    try:
        await main.main()
    except AssertionError:
        pass  # erwartet: ohne Token wird gar nicht erst verbunden

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "session_source" in text and "ocpp" in text, \
        f"Meldung nennt den Standalone-Weg nicht:\n{text}"


def test_web_bind_is_configurable_without_editing_compose():
    """Lokale Änderungen an docker-compose.yml blockieren jedes git pull."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    compose = open(os.path.join(here, 'docker-compose.yml')).read()
    assert '"${WEB_BIND:-127.0.0.1}:8099:8099"' in compose
    example = open(os.path.join(here, '.env.example')).read()
    assert 'WEB_BIND=127.0.0.1' in example and 'TZ=Europe/Berlin' in example
    for line in example.splitlines():
        assert not line.startswith('EC_'), "aktive EC_-Werte würden options.json überschreiben"


def test_setup_script_writes_valid_options(tmp_path):
    import json
    import shutil
    import subprocess
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not shutil.which('jq'):
        pytest.skip('jq nicht installiert')
    for f in ('setup-standalone.sh', 'options.standalone.example.json', '.env.example'):
        shutil.copy(os.path.join(here, f), tmp_path)
    answers = 'ACE1\ngarage\nhttps://erp.firma.de\ntok"1\nEFCD083E, AABB\n0.0.0.0\n\nadmin\n\n'
    out = subprocess.run(['bash', str(tmp_path / 'setup-standalone.sh')], input=answers,
                         capture_output=True, text=True, check=True).stdout
    cfg = json.load(open(tmp_path / 'data' / 'options.json'))
    pw = cfg['ocpp_charge_points'][0]['password']
    assert len(pw) == 24 and pw in out and 'ws://' in out
    assert cfg['web_auth']['username'] == 'admin' and len(cfg['web_auth']['password']) == 16
    assert 'WEB_BIND=0.0.0.0' in open(tmp_path / '.env').read()
    assert ':8099/' in out
    assert cfg['api']['api_token'] == 'tok"1'
    assert cfg['rfid_whitelist'] == ['EFCD083E', 'AABB']
    from placeholders import find_placeholders
    assert find_placeholders(cfg) == []
