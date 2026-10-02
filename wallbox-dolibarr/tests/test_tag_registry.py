"""Tag-Verwaltung im Addon: benennen, einordnen, Abrechnung steuern.

Kernzusage: ein als PRIVAT eingeordneter Tag erreicht Dolibarr nie. Das ist
die einzige Stelle, an der das durchgesetzt wird — sie braucht Tests, die
fehlschlagen, wenn jemand die Schranke später aufweicht.

RFID-Klartext wird auch hier nie gespeichert: die Tabelle kennt nur den
SHA-256-Hash plus den vom Admin vergebenen Namen.
"""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from utils.hash import hash_rfid  # noqa: E402

BUSINESS = "EFCD083E"
PRIVATE = "AABBCCDD"


@pytest.fixture()
def sm(tmp_path):
    return SessionManager(db_path=str(tmp_path / "sessions.db"))


class _Api:
    def __init__(self):
        self.sent = []

    def transmit_session(self, data):
        self.sent.append(data)
        return True, "ok"


def _charge(sm, tag, kwh=10.0):
    """Eine abgeschlossene Ladung mit diesem Tag anlegen."""
    tx = sm.start_ocpp_transaction(rfid_hex=tag, wallbox_id="garage", charge_point_id="CP1",
                                   connector_id=1, meter_start_kwh=0.0,
                                   start_time="2026-10-01T10:00:00",
                                   ocpp_start_timestamp=f"2026-10-01T08:00:00Z#{tag}")
    sm.stop_ocpp_transaction(tx, "CP1", kwh, "2026-10-01T12:00:00", "Local")
    return tx


# ---- Registry -------------------------------------------------------------

def test_unknown_tag_is_not_registered(sm):
    assert sm.get_tag(BUSINESS) is None
    assert sm.list_tags() == []


def test_register_and_rename_a_tag(sm):
    sm.upsert_tag(BUSINESS, label="Firmenwagen 1", mode="business")
    tag = sm.get_tag(BUSINESS)
    assert tag["label"] == "Firmenwagen 1"
    assert tag["mode"] == "business"
    assert tag["rfid_hash"] == hash_rfid(BUSINESS)

    sm.upsert_tag(BUSINESS, label="Firmenwagen Nord", mode="business")
    assert sm.get_tag(BUSINESS)["label"] == "Firmenwagen Nord"
    assert len(sm.list_tags()) == 1, "Umbenennen darf keinen zweiten Eintrag anlegen"


def test_plaintext_is_never_stored(sm):
    sm.upsert_tag(BUSINESS, label="Firmenwagen 1", mode="business")
    conn = sqlite3.connect(sm.db_path)
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert BUSINESS not in dump.upper(), "RFID-Klartext in der Datenbank"


def test_invalid_mode_is_rejected(sm):
    with pytest.raises(ValueError):
        sm.upsert_tag(BUSINESS, label="x", mode="vielleicht")


def test_delete_tag(sm):
    sm.upsert_tag(BUSINESS, label="x", mode="business")
    assert sm.delete_tag(BUSINESS) is True
    assert sm.get_tag(BUSINESS) is None
    assert sm.delete_tag(BUSINESS) is False


# ---- Autorisierung --------------------------------------------------------

def test_registered_tag_is_authorized_without_being_in_the_whitelist(sm):
    """Der Lernmodus soll Karten freischalten können, ohne dass der Admin die
    Konfigurationsdatei anfassen muss."""
    assert sm.is_rfid_authorized(BUSINESS, []) is False
    sm.upsert_tag(BUSINESS, label="Firmenwagen 1", mode="business")
    assert sm.is_rfid_authorized(BUSINESS, []) is True


def test_private_tag_is_authorized_to_charge(sm):
    """Privat heißt 'nicht abrechnen', nicht 'nicht laden'."""
    sm.upsert_tag(PRIVATE, label="Privat Meier", mode="private")
    assert sm.is_rfid_authorized(PRIVATE, []) is True


def test_unknown_mode_does_not_authorize(sm):
    """Ein nur erkannter, noch nicht eingeordneter Tag darf nicht laden."""
    sm.upsert_tag(BUSINESS, label=None, mode="unknown")
    assert sm.is_rfid_authorized(BUSINESS, []) is False


def test_config_whitelist_still_works(sm):
    """Bestehende Installationen pflegen die Whitelist in der Konfiguration."""
    assert sm.is_rfid_authorized(BUSINESS, [BUSINESS]) is True


# ---- Abrechnungsschranke --------------------------------------------------

def test_business_charge_is_transmitted(sm):
    sm.upsert_tag(BUSINESS, label="Firmenwagen 1", mode="business")
    _charge(sm, BUSINESS)
    api = _Api()
    result = sm.transmit_completed_sessions(api)
    assert result["transmitted"] == 1
    assert len(api.sent) == 1


def test_private_charge_never_reaches_dolibarr(sm):
    sm.upsert_tag(PRIVATE, label="Privat Meier", mode="private")
    tx = _charge(sm, PRIVATE)
    api = _Api()
    result = sm.transmit_completed_sessions(api)
    assert api.sent == [], "private Ladung wurde an Dolibarr geschickt"
    assert result["transmitted"] == 0
    assert result.get("private") == 1


def test_private_charge_is_not_retried_forever(sm):
    """Sie darf nicht in der Warteschlange hängen und bei jedem Durchlauf
    erneut geprüft werden — aber auch nicht als 'übertragen' gelten."""
    sm.upsert_tag(PRIVATE, label="Privat Meier", mode="private")
    tx = _charge(sm, PRIVATE)
    api = _Api()
    sm.transmit_completed_sessions(api)
    sm.transmit_completed_sessions(api)
    assert api.sent == []
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, transmitted_at FROM sessions WHERE id=?", (tx,)).fetchone()
    conn.close()
    assert row["status"] == "private", "Status muss sichtbar machen, warum nichts übertragen wurde"


def test_private_charge_stays_visible_locally(sm):
    """Der ganze Zweck: lokal sichtbar, nur nicht abgerechnet."""
    sm.upsert_tag(PRIVATE, label="Privat Meier", mode="private")
    tx = _charge(sm, PRIVATE, kwh=7.5)
    sm.transmit_completed_sessions(_Api())
    local = sm.get_sessions_by_wallbox("garage")
    mine = [s for s in local if s["id"] == tx]
    assert mine and mine[0]["total_kwh"] == pytest.approx(7.5)


def test_reclassifying_to_private_stops_a_pending_transmission(sm):
    """Markiert der Admin eine Karte nachträglich als privat, darf eine noch
    nicht übertragene Ladung nicht mehr rausgehen."""
    sm.upsert_tag(PRIVATE, label="Noch geschäftlich", mode="business")
    _charge(sm, PRIVATE)
    sm.upsert_tag(PRIVATE, label="Doch privat", mode="private")
    api = _Api()
    sm.transmit_completed_sessions(api)
    assert api.sent == []


def test_charge_without_a_registry_entry_is_still_transmitted(sm):
    """Abwärtskompatibilität: wer nur die Konfigurations-Whitelist nutzt, hat
    keine Registry-Einträge — diese Ladungen müssen weiter abgerechnet werden."""
    _charge(sm, BUSINESS)
    api = _Api()
    assert sm.transmit_completed_sessions(api)["transmitted"] == 1


def test_rejection_message_does_not_claim_the_whitelist_is_missing(sm, caplog):
    """Die OCPP-Autorisierung fragt die Tag-Verwaltung mit leerer Whitelist ab.
    Die Meldung darf dann nicht behaupten, es sei keine Whitelist konfiguriert —
    das schickt bei der Fehlersuche in die falsche Richtung."""
    import logging
    caplog.set_level(logging.WARNING)
    assert sm.is_rfid_authorized("DEADBEEF", []) is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Keine RFID-Whitelist konfiguriert" not in text, text
    assert "nicht autorisiert" in text.lower() or "nicht freigeschaltet" in text.lower()
