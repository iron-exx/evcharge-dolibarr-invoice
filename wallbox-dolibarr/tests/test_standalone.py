"""Standalone-Betrieb in Docker, ohne Home Assistant.

Ohne HA gibt es keinen Supervisor, der /data/options.json schreibt, und keinen
HA-Websocket. Nur die Betriebsart `ocpp` funktioniert dort — und genau das muss
die mitgelieferte Beispielkonfiguration hergeben, sonst scheitert jeder, der
der Doku folgt.
"""
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
    unknown = set(_example()) - known
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
    assert all(p.startswith('127.0.0.1:') for p in ui), \
        f"Web-UI muss an 127.0.0.1 gebunden sein (ist: {ui})"


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
