"""Datenverzeichnis über die Umgebung setzbar.

Im Addon- und Docker-Betrieb ist /data das richtige Verzeichnis. Ohne Docker —
etwa als systemd-Dienst auf einem Pi oder beim lokalen Ausprobieren — gibt es
kein /data und es lässt sich auch nicht anlegen. Dann muss das Verzeichnis
umstellbar sein, sonst ist das Addon dort nicht startbar.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_default_is_the_addon_data_directory(monkeypatch):
    monkeypatch.delenv('EXPENSECHARGE_DATA', raising=False)
    assert main.data_dir() == '/data'
    assert main.config_path() == '/data/options.json'
    assert main.db_path() == '/data/sessions.db'


def test_env_overrides_the_directory(monkeypatch, tmp_path):
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    assert main.data_dir() == str(tmp_path)
    assert main.config_path() == str(tmp_path / 'options.json')
    assert main.db_path() == str(tmp_path / 'sessions.db')


def test_load_config_reads_from_the_overridden_directory(monkeypatch, tmp_path):
    (tmp_path / 'options.json').write_text(
        json.dumps({"session_source": "ocpp", "rfid_whitelist": ["EFCD083E"]}), encoding='utf-8')
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    config = main.load_config()
    assert config['session_source'] == 'ocpp'
    assert config['rfid_whitelist'] == ["EFCD083E"]


def test_a_missing_config_still_returns_an_empty_dict(monkeypatch, tmp_path):
    """Kein Absturz ohne Konfiguration — das bisherige Verhalten."""
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path / 'gibtesnicht'))
    assert main.load_config() == {}


def test_trailing_slash_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path) + '/')
    assert main.config_path() == str(tmp_path / 'options.json')
