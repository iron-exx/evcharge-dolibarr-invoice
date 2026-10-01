"""Haftender RFID-Wert im HA-Pfad.

Die Alfen-Integration leitet den Tag aus dem Transaktions-Log der Wallbox ab —
also aus dem LETZTEN abgeschlossenen Ladevorgang. Dieser Wert bleibt stehen,
bis eine neue Transaktion auftaucht. Für ExpenseCharge heißt das: der Sensor
meldet dauerhaft dieselbe Karte, obwohl längst niemand mehr vorhält.

Ohne Gegenmaßnahme bringt das die Zustandslogik durcheinander. Deshalb kann
das Addon den Rückfall auf "kein Tag" nach `rfid_hold_seconds` selbst erzeugen.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tag_release import TagReleaser  # noqa: E402


def test_new_tag_is_reported_once():
    r = TagReleaser(hold_seconds=1.0)
    assert r.observe("EFCD083E", now=0.0) == "EFCD083E"
    assert r.observe("EFCD083E", now=0.5) is None, "vor Ablauf der Haltezeit nichts Neues"


def test_sticky_tag_is_released_once_after_the_hold():
    r = TagReleaser(hold_seconds=1.0)
    r.observe("EFCD083E", now=0.0)
    assert r.observe("EFCD083E", now=1.0) == "", "Rückfall auf 'kein Tag' muss kommen"
    assert r.observe("EFCD083E", now=1.5) is None, "und zwar genau einmal"
    assert r.observe("EFCD083E", now=99.0) is None


def test_a_different_card_is_reported_even_while_the_old_one_sticks():
    r = TagReleaser(hold_seconds=1.0)
    r.observe("AAAA", now=0.0)
    r.observe("AAAA", now=1.0)                      # Reset
    assert r.observe("BBBB", now=2.0) == "BBBB"


def test_the_same_sticky_card_never_retriggers():
    """Bewusste Einschränkung: Nach dem Reset löst derselbe Wert NICHT erneut aus.

    Bei einem haftenden Sensor ist "alte Karte klebt noch" nicht von "dieselbe
    Karte erneut vorgehalten" zu unterscheiden. Würde erneut ausgelöst, startete
    nach jeder Ladung eine Phantom-Session — schlimmer als eine verpasste
    Ladung. Eine zweite Ladung derselben Karte erkennt stattdessen der
    Zustandssensor; die Zuordnung übernimmt der erhaltene letzte Tag.
    """
    r = TagReleaser(hold_seconds=1.0)
    r.observe("AAAA", now=0.0)
    assert r.observe("AAAA", now=1.0) == ""         # Reset
    assert r.observe("AAAA", now=10.0) is None
    assert r.observe("AAAA", now=3600.0) is None


def test_real_none_values_pass_through_as_release():
    r = TagReleaser(hold_seconds=1.0)
    r.observe("AAAA", now=0.0)
    assert r.observe("No Tag", now=0.2) == "", "echtes 'No Tag' sofort durchlassen"
    assert r.observe("No Tag", now=0.3) is None, "aber nicht wiederholen"


def test_none_value_at_the_start_reports_nothing():
    r = TagReleaser(hold_seconds=1.0)
    assert r.observe("No Tag", now=0.0) is None
    assert r.observe("", now=0.1) is None


def test_disabled_when_hold_is_zero():
    """hold_seconds=0 schaltet die Selbsthilfe ab — für Wallboxen bzw.
    gepatchte Integrationen, die den Tag von sich aus zurücksetzen."""
    r = TagReleaser(hold_seconds=0.0)
    assert r.observe("AAAA", now=0.0) == "AAAA"
    assert r.observe("AAAA", now=5.0) is None, "kein selbst erzeugter Reset"
    assert r.observe("No Tag", now=6.0) == "", "echtes No Tag weiterhin durchlassen"


# ---- Verdrahtung im HA-Pfad ----------------------------------------------

async def test_ha_path_starts_only_one_session_for_a_sticky_tag(tmp_path, monkeypatch):
    """Mit aktivierter Haltezeit darf ein dauerhaft gemeldeter Tag genau EINE
    Session auslösen — nicht nach jedem Debounce-Fenster eine weitere."""
    import main
    from session_manager import SessionManager

    starts = []

    async def fake_start(rfid_hex, source):
        starts.append(rfid_hex)

    main.session_manager = SessionManager(db_path=str(tmp_path / "s.db"))
    main.current_config = {"rfid_whitelist": ["EFCD083E"], "rfid_hold_seconds": 1.0}
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None}
    main.profile = main.wallbox_profile.resolve_profile({})
    main._latest_rfid = None
    main._pending_auth = None
    main._tag_releaser = main.TagReleaser(hold_seconds=1.0)
    monkeypatch.setattr(main, "_start_session_for", fake_start)
    monkeypatch.setattr(main.session_manager, "debounce_rfid", lambda tag: True)

    sensor = main.profile.sensor_rfid
    for _ in range(5):
        await main.sensor_callback(sensor, {"state": "EFCD083E"})
    assert starts == ["EFCD083E"], f"haftender Tag löste mehrfach aus: {starts}"


async def test_ha_path_unchanged_when_hold_is_disabled(tmp_path, monkeypatch):
    """Ohne rfid_hold_seconds bleibt das Verhalten exakt wie bisher."""
    import main
    from session_manager import SessionManager

    starts = []

    async def fake_start(rfid_hex, source):
        starts.append(rfid_hex)

    main.session_manager = SessionManager(db_path=str(tmp_path / "s2.db"))
    main.current_config = {"rfid_whitelist": ["EFCD083E"]}
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None}
    main.profile = main.wallbox_profile.resolve_profile({})
    main._latest_rfid = None
    main._pending_auth = None
    main._tag_releaser = None
    monkeypatch.setattr(main, "_start_session_for", fake_start)
    monkeypatch.setattr(main.session_manager, "debounce_rfid", lambda tag: True)

    sensor = main.profile.sensor_rfid
    for _ in range(3):
        await main.sensor_callback(sensor, {"state": "EFCD083E"})
    assert starts == ["EFCD083E"] * 3, "ohne Haltezeit muss jeder Tag-Event durchgehen"
