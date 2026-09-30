import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocpp_server.settings import resolve_ocpp_settings, sanitize_wallbox_id  # noqa: E402


def test_defaults_disabled():
    s = resolve_ocpp_settings({})
    assert s.enabled is False
    assert s.charge_points == ()
    assert s.heartbeat_interval == 300
    assert s.apply_recommended_config is False


def test_enabled_with_charge_points():
    s = resolve_ocpp_settings({
        "session_source": "ocpp",
        "ocpp_charge_points": [
            {"id": "ACE0123456", "password": "0123456789abcdef", "wallbox_id": "garage"},
            {"id": "CP 2/links"},
        ],
    })
    assert s.enabled is True
    assert s.find("ACE0123456").wallbox_id == "garage"
    assert s.find("ACE0123456").password == "0123456789abcdef"
    assert s.find("CP 2/links").wallbox_id == "CP_2_links"
    assert s.find("CP 2/links").password == ""
    assert s.find("unbekannt") is None


def test_invalid_entries_skipped_and_duplicates_first_wins():
    s = resolve_ocpp_settings({"ocpp_charge_points": [
        {"id": ""}, "kaputt", {"id": "A", "wallbox_id": "erste"}, {"id": "A", "wallbox_id": "zweite"}]})
    assert [c.id for c in s.charge_points] == ["A"]
    assert s.find("A").wallbox_id == "erste"


def test_heartbeat_clamped():
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": 5}).heartbeat_interval == 30
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": 99999}).heartbeat_interval == 3600
    assert resolve_ocpp_settings({"ocpp_heartbeat_interval": "x"}).heartbeat_interval == 300


def test_sanitize_wallbox_id():
    assert sanitize_wallbox_id("Alfen Eve #1") == "Alfen_Eve__1"
    assert sanitize_wallbox_id("x" * 80) == "x" * 50
    assert sanitize_wallbox_id("") == "wallbox"
