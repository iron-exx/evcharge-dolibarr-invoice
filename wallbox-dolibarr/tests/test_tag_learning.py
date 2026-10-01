"""Lernmodus: erkannte Karten flüchtig sichtbar machen.

Der Admin schaltet den Modus ein, hält eine Karte an die Wallbox, sieht sie in
der Oberfläche, benennt sie und ordnet sie ein.

Der RFID-Klartext ist dabei das Heikle: der Admin MUSS ihn sehen, um die Karte
wiederzuerkennen und sie ggf. in Dolibarr einzutragen. Er darf aber nicht
persistiert werden und nicht beliebig lange im Speicher liegen.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tag_learning import LearnBuffer  # noqa: E402


def test_disabled_by_default_and_records_nothing():
    b = LearnBuffer()
    assert b.enabled is False
    assert b.observe("EFCD083E", now=0.0) is False
    assert b.detected() == []


def test_records_a_tag_while_enabled():
    b = LearnBuffer()
    b.enabled = True
    assert b.observe("efcd083e", now=10.0) is True
    (entry,) = b.detected(now=10.0)
    assert entry["tag"] == "EFCD083E", "Anzeige in Großbuchstaben, wie in Dolibarr einzutragen"
    assert entry["hash_prefix"] and len(entry["hash_prefix"]) == 16
    assert entry["seconds_ago"] == pytest.approx(0.0)


def test_same_card_twice_stays_one_entry_and_refreshes():
    b = LearnBuffer()
    b.enabled = True
    b.observe("EFCD083E", now=10.0)
    b.observe("EFCD083E", now=20.0)
    entries = b.detected(now=20.0)
    assert len(entries) == 1
    assert entries[0]["count"] == 2
    assert entries[0]["seconds_ago"] == pytest.approx(0.0)


def test_newest_card_first():
    b = LearnBuffer()
    b.enabled = True
    b.observe("AAAA", now=1.0)
    b.observe("BBBB", now=2.0)
    assert [e["tag"] for e in b.detected(now=2.0)] == ["BBBB", "AAAA"]


def test_entries_expire_so_plaintext_does_not_linger():
    """Der Klartext darf nicht unbegrenzt im Speicher stehen."""
    b = LearnBuffer(ttl_seconds=60)
    b.enabled = True
    b.observe("EFCD083E", now=0.0)
    assert len(b.detected(now=59.0)) == 1
    assert b.detected(now=61.0) == [], "abgelaufener Eintrag muss verschwinden"


def test_buffer_is_capped():
    b = LearnBuffer(max_entries=3)
    b.enabled = True
    for i in range(6):
        b.observe(f"CARD{i}", now=float(i))
    entries = b.detected(now=6.0)
    assert len(entries) == 3
    assert [e["tag"] for e in entries] == ["CARD5", "CARD4", "CARD3"]


def test_disabling_clears_the_plaintext_immediately():
    """Lernmodus aus heißt: Klartext weg, nicht nur unsichtbar."""
    b = LearnBuffer()
    b.enabled = True
    b.observe("EFCD083E", now=0.0)
    b.enabled = False
    assert b.detected(now=1.0) == []
    b.enabled = True
    assert b.detected(now=2.0) == [], "nach dem Ausschalten darf nichts zurückkommen"


def test_none_values_are_ignored():
    b = LearnBuffer()
    b.enabled = True
    for junk in ("", "   ", "No Tag", "unknown", "unavailable", None):
        assert b.observe(junk, now=1.0) is False
    assert b.detected(now=1.0) == []


# ---- Einhängen in die Datenquellen ---------------------------------------

async def test_ha_path_learns_an_unknown_card_and_still_refuses_it(tmp_path, monkeypatch):
    """Eine unbekannte Karte muss im Lernmodus auftauchen — aber trotzdem
    NICHT laden dürfen, solange sie nicht eingeordnet ist."""
    import main
    from session_manager import SessionManager

    starts = []

    async def fake_start(rfid_hex, source):
        starts.append(rfid_hex)

    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    main.session_manager = sm
    main.current_config = {"rfid_whitelist": []}
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None}
    main.profile = main.wallbox_profile.resolve_profile({})
    main._latest_rfid = None
    main._pending_auth = None
    main._tag_releaser = None
    main.learn_buffer = main.LearnBuffer()
    main.learn_buffer.enabled = True
    monkeypatch.setattr(main, "_start_session_for", fake_start)

    await main.sensor_callback(main.profile.sensor_rfid, {"state": "C0FFEE42"})

    assert [e["tag"] for e in main.learn_buffer.detected()] == ["C0FFEE42"]
    assert starts == [], "nicht eingeordnete Karte darf nicht laden"
    assert sm.get_tag("C0FFEE42")["mode"] == "unknown", "muss als 'unknown' vermerkt sein"


async def test_ha_path_charges_once_the_card_is_classified(tmp_path, monkeypatch):
    """Nach dem Einordnen als geschäftlich muss dieselbe Karte laden dürfen —
    ohne Eintrag in der Konfigurations-Whitelist."""
    import main
    from session_manager import SessionManager

    starts = []

    async def fake_start(rfid_hex, source):
        starts.append(rfid_hex)

    sm = SessionManager(db_path=str(tmp_path / "s2.db"))
    sm.upsert_tag("C0FFEE42", label="Firmenwagen 1", mode="business")
    main.session_manager = sm
    main.current_config = {"rfid_whitelist": []}
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None}
    main.profile = main.wallbox_profile.resolve_profile({})
    main._latest_rfid = None
    main._pending_auth = None
    main._tag_releaser = None
    main.learn_buffer = main.LearnBuffer()
    monkeypatch.setattr(main, "_start_session_for", fake_start)

    await main.sensor_callback(main.profile.sensor_rfid, {"state": "C0FFEE42"})
    assert starts == ["C0FFEE42"]


async def test_ocpp_path_learns_the_card_it_rejects(tmp_path):
    """Im OCPP-Betrieb muss eine abgelehnte Karte ebenfalls im Lernmodus
    erscheinen — sonst kann der Admin sie nicht freischalten."""
    import websockets  # noqa: F401
    from ocpp.v16 import call

    from ocpp_server.central_system import CentralSystemDeps
    from ocpp_server.server import OcppServer
    from ocpp_server.settings import resolve_ocpp_settings
    from session_manager import SessionManager
    from tests.ocpp_sim import connect_sim

    buf = LearnBuffer()
    buf.enabled = True
    sm = SessionManager(db_path=str(tmp_path / "s3.db"))
    deps = CentralSystemDeps(session_manager=sm, whitelist=[], live={},
                             on_tag_seen=buf.observe)
    settings = resolve_ocpp_settings({"session_source": "ocpp",
                                      "ocpp_charge_points": [{"id": "CP1"}]})
    server = OcppServer(settings, deps)
    port = await server.start("127.0.0.1", 0)
    try:
        async with connect_sim(port, "CP1") as cp:
            res = await cp.call(call.Authorize(id_tag="c0ffee42"))
            assert res.id_tag_info["status"] == "Invalid"
    finally:
        await server.close()
    assert [e["tag"] for e in buf.detected()] == ["C0FFEE42"]


async def test_ocpp_path_accepts_a_classified_card_without_whitelist(tmp_path):
    """Eine in der Oberfläche eingeordnete Karte muss auch im OCPP-Betrieb
    laden dürfen — sonst nützt der Lernmodus dort nichts."""
    from ocpp.v16 import call

    from ocpp_server.central_system import CentralSystemDeps
    from ocpp_server.server import OcppServer
    from ocpp_server.settings import resolve_ocpp_settings
    from session_manager import SessionManager
    from tests.ocpp_sim import connect_sim

    sm = SessionManager(db_path=str(tmp_path / "s4.db"))
    sm.upsert_tag("C0FFEE42", label="Firmenwagen 1", mode="business")
    sm.upsert_tag("AABBCCDD", label="Privat Meier", mode="private")
    sm.upsert_tag("DEADBEEF", label=None, mode="unknown")

    deps = CentralSystemDeps(session_manager=sm, whitelist=[], live={})
    settings = resolve_ocpp_settings({"session_source": "ocpp",
                                      "ocpp_charge_points": [{"id": "CP1"}]})
    server = OcppServer(settings, deps)
    port = await server.start("127.0.0.1", 0)
    try:
        async with connect_sim(port, "CP1") as cp:
            business = await cp.call(call.Authorize(id_tag="c0ffee42"))
            private = await cp.call(call.Authorize(id_tag="aabbccdd"))
            unclassified = await cp.call(call.Authorize(id_tag="deadbeef"))
    finally:
        await server.close()

    assert business.id_tag_info["status"] == "Accepted"
    assert private.id_tag_info["status"] == "Accepted", "privat heißt nicht abrechnen, nicht nicht laden"
    assert unclassified.id_tag_info["status"] == "Invalid", "nicht eingeordnet darf nicht laden"
