"""OCPP-Transaktionen im SessionManager (session_source: ocpp)."""
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from utils.hash import hash_rfid  # noqa: E402


@pytest.fixture()
def sm(tmp_path):
    return SessionManager(db_path=str(tmp_path / "sessions.db"))


def _start(sm, cp="CP1", connector=1, meter=1000.0, ts="2026-09-30T08:00:00Z",
           start_time="2026-09-30T10:00:00"):
    return sm.start_ocpp_transaction(rfid_hex="EFCD083E", wallbox_id="garage", charge_point_id=cp,
                                     connector_id=connector, meter_start_kwh=meter,
                                     start_time=start_time, ocpp_start_timestamp=ts)


def _row(sm, session_id):
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
    conn.close()
    return row


def test_start_and_stop_completed(sm):
    tx = _start(sm)
    done = sm.stop_ocpp_transaction(tx, "CP1", 1012.5, "2026-09-30T12:00:00", "EVDisconnected")
    assert done["total_kwh"] == pytest.approx(12.5)
    assert done["wallbox_id"] == "garage"
    assert done["rfid_hash"] == hash_rfid("EFCD083E")
    row = _row(sm, tx)
    assert row["status"] == "completed"
    assert row["stop_reason"] == "EVDisconnected"
    assert row["transmitted_at"] is None


def test_start_is_idempotent_for_repeated_message(sm):
    assert _start(sm) == _start(sm)
    assert len(sm.get_active_ocpp_sessions()) == 1


def test_parallel_sessions_on_two_charge_points(sm):
    a = _start(sm, cp="CP1")
    b = _start(sm, cp="CP2")
    assert a != b
    assert {s["charge_point_id"] for s in sm.get_active_ocpp_sessions()} == {"CP1", "CP2"}


def test_new_start_on_same_connector_supersedes_lost_stop(sm):
    old = _start(sm, ts="2026-09-30T08:00:00Z")
    new = _start(sm, ts="2026-09-30T09:00:00Z")
    assert _row(sm, old)["status"] == "incomplete"
    assert _row(sm, old)["stop_reason"] == "ocpp_superseded"
    assert _row(sm, new)["status"] == "active"


def test_stop_is_idempotent(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", 1010.0, "2026-09-30T12:00:00", "Local") is not None
    assert sm.stop_ocpp_transaction(tx, "CP1", 1020.0, "2026-09-30T13:00:00", "Local") is None
    assert _row(sm, tx)["total_kwh"] == pytest.approx(10.0)


def test_stop_unknown_or_foreign_transaction_is_ignored(sm):
    tx = _start(sm, cp="CP1")
    assert sm.stop_ocpp_transaction(999, "CP1", 1010.0, "2026-09-30T12:00:00", "Local") is None
    assert sm.stop_ocpp_transaction(tx, "CP2", 1010.0, "2026-09-30T12:00:00", "Local") is None
    assert _row(sm, tx)["status"] == "active"


def test_stop_without_usable_meter_uses_last_meter_value(sm):
    tx = _start(sm)
    assert sm.update_ocpp_meter(tx, "CP1", 1007.25)
    done = sm.stop_ocpp_transaction(tx, "CP1", 0.0, "2026-09-30T12:00:00", "Local")
    assert done["total_kwh"] == pytest.approx(7.25)


def test_stop_without_any_meter_is_incomplete(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", None, "2026-09-30T12:00:00", "PowerLoss") is None
    assert _row(sm, tx)["status"] == "incomplete"


def test_meter_updates_only_monotonic_and_active(sm):
    tx = _start(sm)
    assert sm.update_ocpp_meter(tx, "CP1", 1005.0)
    assert not sm.update_ocpp_meter(tx, "CP1", 1004.0)
    assert not sm.update_ocpp_meter(tx, "CP1", 999.0)
    assert not sm.update_ocpp_meter(tx, "CP2", 1006.0)


def test_small_session_discarded(sm):
    tx = _start(sm)
    assert sm.stop_ocpp_transaction(tx, "CP1", 1000.01, "2026-09-30T10:05:00", "Local") is None
    row = _row(sm, tx)
    assert row["status"] == "discarded"
    assert row["transmitted_at"] is not None


def test_implausible_energy_is_incomplete(sm):
    # 500 kWh in 1 h = 500 kW → Einheiten-/Zählerfehler, nicht abrechnen
    tx = _start(sm, start_time="2026-09-30T10:00:00")
    assert sm.stop_ocpp_transaction(tx, "CP1", 1500.0, "2026-09-30T11:00:00", "Local") is None
    assert _row(sm, tx)["status"] == "incomplete"


def test_ocpp_session_is_transmitted(sm):
    class _Api:
        def __init__(self):
            self.sent = []

        def transmit_session(self, data):
            self.sent.append(data)
            return True, "ok"

    tx = _start(sm)
    sm.stop_ocpp_transaction(tx, "CP1", 1012.5, "2026-09-30T12:00:00", "Local")
    api = _Api()
    result = sm.transmit_completed_sessions(api)
    assert result["transmitted"] == 1
    assert api.sent[0]["wallbox_id"] == "garage"
    assert api.sent[0]["rfid_hash"] == hash_rfid("EFCD083E")


def test_legacy_ha_path_unaffected(sm):
    sid = sm.start_session("EFCD083E", 100.0, wallbox_id="alfen_eve")
    assert sm.get_active_ocpp_sessions() == []
    assert sm.end_session(110.0)["total_kwh"] == pytest.approx(10.0)
    assert _row(sm, sid)["charge_point_id"] is None


# ---- Feindliche Eingaben aus dem Code-Review ------------------------------

def test_stuck_clock_does_not_swallow_later_charges(sm):
    """Wallbox mit nie gestellter Uhr meldet für JEDE Ladung denselben
    Start-Zeitstempel. Ohne weiteres Unterscheidungsmerkmal bekäme die zweite
    Ladung die ID der ersten, würde nichts einfügen, und ihr Stop liefe ins
    Leere — die Ladung wäre nirgends erfasst, nicht einmal als incomplete.
    """
    first = _start(sm, meter=1000.0, ts="1970-01-01T00:00:00Z")
    assert sm.stop_ocpp_transaction(first, "CP1", 1010.0, "2026-09-30T12:00:00", "Local") is not None

    # Zweite, echte Ladung — gleicher (kaputter) Zeitstempel, aber der Zähler
    # ist weitergelaufen: das ist unterscheidbar.
    second = _start(sm, meter=1010.0, ts="1970-01-01T00:00:00Z",
                    start_time="2026-09-30T14:00:00")
    assert second != first, "zweite Ladung hat die ID der ersten geerbt"
    done = sm.stop_ocpp_transaction(second, "CP1", 1025.0, "2026-09-30T16:00:00", "Local")
    assert done is not None and done["total_kwh"] == pytest.approx(15.0)


def test_genuine_retry_still_idempotent_with_same_meter_start(sm):
    """Ein echter Wiederholungsversuch hat denselben Zeitstempel UND denselben
    Startzählerstand — der muss weiterhin dieselbe ID bekommen (D3)."""
    a = _start(sm, meter=1000.0, ts="2026-09-30T08:00:00Z")
    b = _start(sm, meter=1000.0, ts="2026-09-30T08:00:00Z")
    assert a == b
    assert len(sm.get_active_ocpp_sessions()) == 1


def test_long_session_below_min_kwh_is_incomplete_not_discarded(sm):
    """Meldet eine Wallbox ihren Zähler in kWh statt in Wh (OCPP verlangt Wh),
    ist das Ergebnis 1000x zu klein und fiele unter min_session_kwh — die
    Ladung würde als 'discarded' still verworfen UND als übertragen markiert.
    Eine stundenlange Session unter min_kwh ist aber ein Zählerfehler, kein
    'Karte gehalten und weggegangen' → sichtbar als incomplete.
    """
    tx = _start(sm, meter=1000.0, start_time="2026-09-30T10:00:00")
    assert sm.stop_ocpp_transaction(tx, "CP1", 1000.01, "2026-09-30T14:00:00", "Local") is None
    row = _row(sm, tx)
    assert row["status"] == "incomplete", f"war {row['status']}"
    assert row["transmitted_at"] is None, "darf nicht als erledigt abgehakt werden"


def test_short_session_below_min_kwh_stays_discarded(sm):
    """Der eigentliche Zweck von 'discarded': Karte kurz gehalten, nie geladen."""
    tx = _start(sm, meter=1000.0, start_time="2026-09-30T10:00:00")
    assert sm.stop_ocpp_transaction(tx, "CP1", 1000.01, "2026-09-30T10:03:00", "Local") is None
    row = _row(sm, tx)
    assert row["status"] == "discarded"
    assert row["transmitted_at"] is not None
