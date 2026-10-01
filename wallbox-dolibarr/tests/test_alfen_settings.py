"""Konfiguration der Alfen-HTTP-Quelle."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from alfen_source.settings import AlfenConfigError, resolve_alfen_settings  # noqa: E402

MIN = {"session_source": "alfen_http",
       "alfen": {"host": "192.168.1.60", "username": "admin", "password": "geheim"}}


def test_disabled_by_default():
    assert resolve_alfen_settings({}).enabled is False


def test_minimal_config_with_defaults():
    s = resolve_alfen_settings(MIN)
    assert s.enabled is True
    assert s.base_url == "https://192.168.1.60"
    assert s.verify_ssl is False, "Alfen liefert ein selbst ausgestelltes Zertifikat"
    assert s.poll_interval == 30.0
    assert s.transaction_interval == 300.0
    assert s.param_energy == "2221_22"
    assert s.param_state == "2501_1"
    assert s.fixed_login == ""


def test_host_may_include_a_scheme_or_port():
    assert resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"], "host": "http://10.0.0.7:8080"}}).base_url \
        == "http://10.0.0.7:8080"
    assert resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"], "host": "wallbox.local"}}).base_url \
        == "https://wallbox.local"


def test_host_and_credentials_are_mandatory():
    for missing in ('host', 'username', 'password'):
        cfg = {"session_source": "alfen_http", "alfen": dict(MIN["alfen"])}
        del cfg["alfen"][missing]
        with pytest.raises(AlfenConfigError, match=missing):
            resolve_alfen_settings(cfg)


def test_intervals_are_clamped():
    s = resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"],
                                                 "poll_interval": 0.1,
                                                 "transaction_interval": 1}})
    assert s.poll_interval >= 5.0
    assert s.transaction_interval >= 30.0, \
        "das Transaktions-Log ist teuer — nicht im Sekundentakt lesen"


def test_transaction_interval_is_never_shorter_than_the_poll_interval():
    s = resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"],
                                                 "poll_interval": 120,
                                                 "transaction_interval": 60}})
    assert s.transaction_interval >= s.poll_interval


def test_param_ids_are_validated():
    with pytest.raises(AlfenConfigError, match="param_energy"):
        resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"], "param_energy": "rm -rf /"}})
    s = resolve_alfen_settings({**MIN, "alfen": {**MIN["alfen"], "param_energy": "2221_22"}})
    assert s.param_energy == "2221_22"


def test_credentials_are_not_in_the_repr():
    """Die Einstellungen landen im Log und im System-Tab."""
    s = resolve_alfen_settings(MIN)
    assert "geheim" not in repr(s)
