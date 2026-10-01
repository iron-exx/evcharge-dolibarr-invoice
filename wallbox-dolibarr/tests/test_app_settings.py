"""Zentrale Auflösung aller Betriebsparameter.

Eine Stelle, an der jeder einstellbare Wert steht — mit Validierung und
Begrenzung. Ein unsinniger Wert in options.json darf nie zu einer falschen
Abrechnung führen, sondern wird auf einen vertretbaren Bereich gezogen und
protokolliert.
"""
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app_settings import resolve_app_settings  # noqa: E402


def test_defaults_match_todays_hardcoded_behaviour():
    """Ohne Konfiguration muss alles genau so laufen wie bisher — sonst
    ändert ein Update stillschweigend das Verhalten bestehender Anlagen."""
    s = resolve_app_settings({})
    assert s.log_level == 'INFO'
    assert s.web_port == 8099
    assert s.web_bind == '0.0.0.0'
    assert s.ocpp_bind == '0.0.0.0'
    assert s.ocpp_port == 9000
    assert s.debounce_seconds == 7
    assert s.max_plausible_kw == pytest.approx(50.0)
    assert s.max_discard_hours == pytest.approx(0.25)
    assert s.pending_auth_window == 600
    assert s.api_timeout == 30
    assert s.api_retries == 5
    assert s.api_backoff == pytest.approx(0.5)
    assert s.learn_ttl_seconds == pytest.approx(600.0)
    assert s.learn_max_entries == 10
    assert s.trend_days == 14


def test_log_level_comes_from_the_option():
    """Die Option existierte, wurde aber nie ausgewertet — das war ein Fehler."""
    assert resolve_app_settings({'log_level': 'debug'}).log_level == 'DEBUG'
    assert resolve_app_settings({'log_level': 'WARNING'}).log_level == 'WARNING'


def test_unknown_log_level_falls_back_without_crashing():
    assert resolve_app_settings({'log_level': 'LAUT'}).log_level == 'INFO'


def test_env_overrides_the_option_for_log_level(monkeypatch):
    """LOG_LEVEL in der Umgebung hat Vorrang — so lässt sich im Container
    debuggen, ohne die Konfiguration anzufassen."""
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    assert resolve_app_settings({'log_level': 'WARNING'}).log_level == 'DEBUG'


def test_values_are_clamped_not_rejected():
    """Ein unsinniger Wert darf das Addon nicht lahmlegen, aber auch nicht
    wirken: er wird in den vertretbaren Bereich gezogen."""
    s = resolve_app_settings({
        'debounce_seconds': 0,
        'max_plausible_kw': 0,
        'max_discard_hours': -5,
        'api_timeout': 1,
        'api_retries': 99,
        'learn_ttl_seconds': 1,
        'trend_days': 400,
        'web_port': 70000,
    })
    assert s.debounce_seconds >= 1
    assert s.max_plausible_kw >= 1.0
    assert s.max_discard_hours >= 0.0
    assert s.api_timeout >= 5
    assert s.api_retries <= 10
    assert s.learn_ttl_seconds >= 30
    assert 1 <= s.trend_days <= 90
    assert 1 <= s.web_port <= 65535


def test_garbage_types_fall_back_to_the_default():
    s = resolve_app_settings({'debounce_seconds': 'sieben', 'max_plausible_kw': None,
                              'web_port': [], 'trend_days': {}})
    assert s.debounce_seconds == 7
    assert s.max_plausible_kw == pytest.approx(50.0)
    assert s.web_port == 8099
    assert s.trend_days == 14


def test_clamping_is_logged_so_it_is_not_silent(caplog):
    caplog.set_level(logging.WARNING)
    resolve_app_settings({'debounce_seconds': 0})
    assert any('debounce_seconds' in r.getMessage() for r in caplog.records)


def test_bind_address_is_validated():
    assert resolve_app_settings({'web_bind': '127.0.0.1'}).web_bind == '127.0.0.1'
    assert resolve_app_settings({'web_bind': ' 10.0.0.5 '}).web_bind == '10.0.0.5'
    assert resolve_app_settings({'web_bind': 'rm -rf /'}).web_bind == '0.0.0.0', \
        "unplausible Adresse darf nicht durchgereicht werden"


def test_as_rows_lists_every_value_for_the_ui():
    """Die Oberfläche zeigt die wirksame Konfiguration — dafür braucht sie
    Label, Wert und ob er vom Standard abweicht."""
    rows = resolve_app_settings({'debounce_seconds': 12}).as_rows()
    assert rows, "darf nicht leer sein"
    by_key = {r['key']: r for r in rows}
    assert by_key['debounce_seconds']['value'] == 12
    assert by_key['debounce_seconds']['changed'] is True
    assert by_key['web_port']['changed'] is False
    assert all({'key', 'label', 'value', 'default', 'changed'} <= set(r) for r in rows)


# ---- Wirken die Werte tatsächlich? ---------------------------------------

def test_debounce_setting_takes_effect(tmp_path):
    from session_manager import SessionManager
    sm = SessionManager(db_path=str(tmp_path / "a.db"), debounce_seconds=0.0)
    assert sm.debounce_rfid("EFCD083E") is True
    assert sm.debounce_rfid("EFCD083E") is True, "ohne Entprellung muss jeder Tap durchgehen"

    sm2 = SessionManager(db_path=str(tmp_path / "b.db"), debounce_seconds=60)
    assert sm2.debounce_rfid("EFCD083E") is True
    assert sm2.debounce_rfid("EFCD083E") is False, "innerhalb des Fensters gesperrt"


def test_plausibility_limit_takes_effect(tmp_path):
    """Eine DC-Ladesäule liefert mehr als 50 kW — die Grenze muss anhebbar
    sein, sonst landet jede schnelle Ladung in 'incomplete'."""
    from session_manager import SessionManager

    def charge(sm, kwh):
        tx = sm.start_ocpp_transaction(rfid_hex="EFCD083E", wallbox_id="dc", charge_point_id="CP1",
                                       connector_id=1, meter_start_kwh=0.0,
                                       start_time="2026-10-01T10:00:00",
                                       ocpp_start_timestamp=f"ts{kwh}")
        sm.stop_ocpp_transaction(tx, "CP1", kwh, "2026-10-01T11:00:00", "Local")
        import sqlite3
        c = sqlite3.connect(sm.db_path)
        st = c.execute("SELECT status FROM sessions WHERE id=?", (tx,)).fetchone()[0]
        c.close()
        return st

    # 120 kWh in 1 h = 120 kW
    assert charge(SessionManager(db_path=str(tmp_path / "c.db")), 120.0) == 'incomplete'
    assert charge(SessionManager(db_path=str(tmp_path / "d.db"),
                                 max_plausible_kw=350.0), 120.0) == 'completed'


def test_discard_window_takes_effect(tmp_path):
    from session_manager import SessionManager
    import sqlite3

    def charge(sm):
        tx = sm.start_ocpp_transaction(rfid_hex="EFCD083E", wallbox_id="g", charge_point_id="CP1",
                                       connector_id=1, meter_start_kwh=0.0,
                                       start_time="2026-10-01T10:00:00", ocpp_start_timestamp="x")
        sm.stop_ocpp_transaction(tx, "CP1", 0.01, "2026-10-01T10:30:00", "Local")
        c = sqlite3.connect(sm.db_path)
        st = c.execute("SELECT status FROM sessions WHERE id=?", (tx,)).fetchone()[0]
        c.close()
        return st

    # 30 min unter min_kwh: mit Standardfenster (0.25 h) ein Zählerfehler
    assert charge(SessionManager(db_path=str(tmp_path / "e.db"))) == 'incomplete'
    # Fenster auf 1 h: gilt wieder als "Karte gehalten, nie geladen"
    assert charge(SessionManager(db_path=str(tmp_path / "f.db"),
                                 max_discard_hours=1.0)) == 'discarded'


def test_api_client_honours_timeout_and_retries():
    from api_client import WallboxApiClient
    c = WallboxApiClient(base_url="https://example.invalid", api_token="t",
                         timeout=90, retries=2, backoff=1.5)
    assert c.timeout == 90
    adapter = c.session.get_adapter("https://example.invalid")
    assert adapter.max_retries.total == 2
    assert adapter.max_retries.backoff_factor == pytest.approx(1.5)
