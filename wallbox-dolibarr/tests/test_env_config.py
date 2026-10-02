"""Konfiguration über Umgebungsvariablen.

Praxisbefund vom ersten Aufsetzen: eine handgeschriebene options.json im
Container ist mühsam und fehleranfällig — Platzhalter, Heredocs, nano,
Kopierfehler. Mit Umgebungsvariablen reicht eine einfache .env-Datei neben
docker-compose.yml, oder die Werte werden direkt in Portainer/Proxmox gesetzt.

Generisch statt fest verdrahtet: jede Option ist setzbar, ohne Zuordnungsliste.
  EC_<OPTION>              → oberste Ebene      (EC_SESSION_SOURCE)
  EC_<BEREICH>__<OPTION>   → verschachtelt      (EC_API__API_TOKEN, EC_ALFEN__HOST)
Umgebung hat Vorrang vor options.json; options.json ist dann optional.
"""
import json
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from env_config import apply_env_overrides  # noqa: E402


def test_top_level_value():
    cfg = apply_env_overrides({}, {'EC_SESSION_SOURCE': 'alfen_http', 'EC_WALLBOX_ID': 'garage'})
    assert cfg['session_source'] == 'alfen_http'
    assert cfg['wallbox_id'] == 'garage'


def test_nested_value_with_double_underscore():
    cfg = apply_env_overrides({}, {'EC_API__DOLIBARR_URL': 'https://erp.example.org',
                                   'EC_API__API_TOKEN': 'abc',
                                   'EC_ALFEN__HOST': '192.168.1.60'})
    assert cfg['api'] == {'dolibarr_url': 'https://erp.example.org', 'api_token': 'abc'}
    assert cfg['alfen']['host'] == '192.168.1.60'


def test_env_overrides_the_file_but_keeps_the_rest():
    base = {'session_source': 'ocpp', 'api': {'dolibarr_url': 'alt', 'transmit_interval': 300}}
    cfg = apply_env_overrides(base, {'EC_API__DOLIBARR_URL': 'neu'})
    assert cfg['api'] == {'dolibarr_url': 'neu', 'transmit_interval': 300}
    assert cfg['session_source'] == 'ocpp'
    assert base['api']['dolibarr_url'] == 'alt', "Eingabe darf nicht verändert werden"


def test_lists_and_objects_as_json():
    cfg = apply_env_overrides({}, {
        'EC_RFID_WHITELIST': '["EFCD083E", "AABBCCDD"]',
        'EC_OCPP_CHARGE_POINTS': '[{"id": "ACE01", "password": "x"}]'})
    assert cfg['rfid_whitelist'] == ['EFCD083E', 'AABBCCDD']
    assert cfg['ocpp_charge_points'][0]['id'] == 'ACE01'


def test_booleans_are_parsed():
    """bool("false") ist in Python True — ein String würde stumm das Gegenteil
    bewirken."""
    cfg = apply_env_overrides({}, {'EC_OCPP_APPLY_RECOMMENDED_CONFIG': 'false',
                                   'EC_ALFEN__VERIFY_SSL': 'TRUE'})
    assert cfg['ocpp_apply_recommended_config'] is False
    assert cfg['alfen']['verify_ssl'] is True


def test_numeric_looking_secrets_stay_strings():
    """Ein Passwort '123456' darf nicht zur Zahl werden."""
    cfg = apply_env_overrides({}, {'EC_ALFEN__PASSWORD': '123456', 'EC_API__API_TOKEN': '0042'})
    assert cfg['alfen']['password'] == '123456'
    assert cfg['api']['api_token'] == '0042'


def test_broken_json_is_rejected_loudly():
    """Ein Tippfehler in einer Liste darf nicht still als Text durchrutschen —
    sonst gälte z.B. die ganze Zeile als EINE Karten-ID."""
    with pytest.raises(ValueError, match='EC_RFID_WHITELIST'):
        apply_env_overrides({}, {'EC_RFID_WHITELIST': '["EFCD083E", '})


def test_unrelated_and_empty_variables_are_ignored():
    cfg = apply_env_overrides({'a': 1}, {'PATH': '/bin', 'EC_': 'x', 'EC_WALLBOX_ID': '',
                                         'HOME': '/root'})
    assert cfg == {'a': 1}, "leere Werte überschreiben nichts"


def test_override_names_are_logged_but_never_values(caplog):
    caplog.set_level(logging.INFO)
    apply_env_overrides({}, {'EC_API__API_TOKEN': 'GEHEIM123', 'EC_ALFEN__PASSWORD': 'PW999'})
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'api.api_token' in text and 'alfen.password' in text
    assert 'GEHEIM123' not in text and 'PW999' not in text


def test_load_config_works_without_options_json(monkeypatch, tmp_path):
    """Nur Umgebung, keine Datei — der Normalfall bei reiner .env-Konfiguration."""
    import main
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    monkeypatch.setenv('EC_SESSION_SOURCE', 'alfen_http')
    monkeypatch.setenv('EC_ALFEN__HOST', '10.0.0.5')
    cfg = main.load_config()
    assert cfg['session_source'] == 'alfen_http'
    assert cfg['alfen']['host'] == '10.0.0.5'


def test_load_config_merges_file_and_env(monkeypatch, tmp_path):
    import main
    (tmp_path / 'options.json').write_text(json.dumps(
        {'session_source': 'ocpp', 'api': {'dolibarr_url': 'https://alt', 'transmit_interval': 60}}))
    monkeypatch.setenv('EXPENSECHARGE_DATA', str(tmp_path))
    monkeypatch.setenv('EC_API__API_TOKEN', 'tok')
    cfg = main.load_config()
    assert cfg['api'] == {'dolibarr_url': 'https://alt', 'transmit_interval': 60, 'api_token': 'tok'}


def test_env_file_is_gitignored_but_example_is_not():
    """.env enthält Dolibarr-Token und Passwörter; die Repos sind öffentlich."""
    import subprocess
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ignored = lambda p: subprocess.run(['git', 'check-ignore', '-q', p], cwd=here).returncode == 0
    assert ignored('.env')
    assert ignored('docker-compose.override.yml'), "lokale Overrides dürfen git pull nie blockieren"
    assert not ignored('.env.example')


def test_compose_reads_optional_env_file():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(here, 'docker-compose.yml')).read()
    assert 'env_file' in text and 'required: false' in text
