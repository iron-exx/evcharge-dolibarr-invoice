"""Integrationstests: simulierte Wallbox ↔ echter OCPP-Server (WebSocket auf Port 0)."""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import websockets  # noqa: E402
from ocpp.v16 import call  # noqa: E402

from ocpp_server.central_system import CentralSystemDeps  # noqa: E402
from ocpp_server.server import OcppServer, charge_point_id_from_path, parse_basic_auth  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import connect_sim  # noqa: E402

PASSWORD = "0123456789abcdef"


def _ts(hours_ago: float = 0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture()
async def env(tmp_path):
    settings = resolve_ocpp_settings({
        "session_source": "ocpp",
        "ocpp_charge_points": [{"id": "CP1", "password": PASSWORD, "wallbox_id": "garage"},
                               {"id": "OPEN"}],
    })
    completed = []
    deps = CentralSystemDeps(session_manager=SessionManager(db_path=str(tmp_path / "s.db")),
                             whitelist=["EFCD083E"], live={}, on_session_completed=completed.append)
    server = OcppServer(settings, deps)
    port = await server.start("127.0.0.1", 0)
    yield {"port": port, "deps": deps, "completed": completed, "server": server}
    await server.close()


def _status(result):
    return result.id_tag_info["status"]


async def test_full_charging_session(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        boot = await cp.call(call.BootNotification(charge_point_vendor="Alfen BV", charge_point_model="NG910"))
        assert boot.status == "Accepted"
        assert _status(await cp.call(call.Authorize(id_tag="efcd083e"))) == "Accepted"
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e",
                                                    meter_start=1_000_000, timestamp=_ts(hours_ago=2)))
        assert _status(start) == "Accepted" and start.transaction_id > 0
        await cp.call(call.MeterValues(connector_id=1, transaction_id=start.transaction_id, meter_value=[
            {"timestamp": _ts(), "sampledValue": [{"value": "1005000"}]}]))
        await cp.call(call.StopTransaction(transaction_id=start.transaction_id, meter_stop=1_012_500,
                                           timestamp=_ts(), reason="EVDisconnected"))
    assert len(env["completed"]) == 1
    assert env["completed"][0]["total_kwh"] == pytest.approx(12.5)
    assert env["completed"][0]["wallbox_id"] == "garage"
    live = env["deps"].live["CP1"]
    assert live["vendor"] == "Alfen BV"
    assert live["connectors"]["1"]["energy_kwh"] == pytest.approx(1005.0)
    await asyncio.sleep(0.05)
    assert live["connected"] is False


async def test_unknown_tag_rejected_and_never_billed(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        assert _status(await cp.call(call.Authorize(id_tag="DEADBEEF"))) == "Invalid"
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="DEADBEEF",
                                                    meter_start=0, timestamp=_ts()))
        assert start.transaction_id == 0 and _status(start) == "Invalid"
        stop = await cp.call(call.StopTransaction(transaction_id=0, meter_stop=5000, timestamp=_ts(),
                                                  reason="DeAuthorized"))
        assert stop is not None
    assert env["completed"] == []
    assert env["deps"].live["CP1"]["last_rejected_id_tag"] == "DEADBEEF"
    assert env["deps"].session_manager.get_active_ocpp_sessions() == []


async def test_start_without_prior_authorize_still_checked(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                    meter_start=0, timestamp=_ts()))
        assert start.transaction_id > 0


async def test_repeated_start_after_reconnect_gets_same_id(env):
    ts = _ts()
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        first = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E", meter_start=0, timestamp=ts))
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        again = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E", meter_start=0, timestamp=ts))
    assert first.transaction_id == again.transaction_id


async def test_stop_for_unknown_transaction_is_acknowledged(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        stop = await cp.call(call.StopTransaction(transaction_id=4711, meter_stop=1, timestamp=_ts()))
        assert stop is not None
    assert env["completed"] == []


async def test_stop_uses_transaction_data_when_meter_stop_zero(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                    meter_start=2_000_000, timestamp=_ts(hours_ago=2)))
        await cp.call(call.StopTransaction(
            transaction_id=start.transaction_id, meter_stop=0, timestamp=_ts(),
            transaction_data=[{"timestamp": _ts(), "sampledValue": [
                {"value": "2008.4", "unit": "kWh", "context": "Transaction.End"}]}]))
    assert env["completed"][0]["total_kwh"] == pytest.approx(8.4)


async def test_status_heartbeat_datatransfer_answered(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        assert (await cp.call(call.Heartbeat())).current_time
        await cp.call(call.StatusNotification(connector_id=1, error_code="NoError", status="Charging"))
        dt = await cp.call(call.DataTransfer(vendor_id="com.alfen", message_id="x", data="y"))
        assert dt.status == "UnknownVendorId"
    assert env["deps"].live["CP1"]["connectors"]["1"]["status"] == "Charging"


async def test_recommended_config_pushed_after_boot_when_enabled(env):
    env["deps"].apply_recommended_config = True
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.BootNotification(charge_point_vendor="v", charge_point_model="m"))
        await asyncio.sleep(0.2)
        assert ("StopTransactionOnInvalidId", "true") in cp.config_changes


async def test_recommended_config_not_pushed_by_default(env):
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.BootNotification(charge_point_vendor="v", charge_point_model="m"))
        await asyncio.sleep(0.2)
        assert cp.config_changes == []


async def test_id_tag_plaintext_never_logged(env, caplog):
    caplog.set_level("DEBUG")
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        await cp.call(call.Authorize(id_tag="c0ffee42"))
        await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e", meter_start=0, timestamp=_ts(1)))
    # Alles außer der simulierten Wallbox selbst (ihr ocpp- und websockets-Client-Log)
    server_side = [r for r in caplog.records if r.name not in ("ocpp_sim", "websockets.client")]
    text = "\n".join(r.getMessage() for r in server_side).upper()
    assert "EFCD083E" not in text and "C0FFEE42" not in text


# ---- Verbindungsannahme --------------------------------------------------

async def test_unknown_charge_point_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "FREMD"):
            pass
    assert exc.value.response.status_code == 404


async def test_wrong_password_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "CP1", "falsch-falsch-falsch"):
            pass
    assert exc.value.response.status_code == 401


async def test_missing_password_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "CP1"):
            pass
    assert exc.value.response.status_code == 401


async def test_open_charge_point_without_password(env):
    async with connect_sim(env["port"], "OPEN") as cp:
        assert (await cp.call(call.Heartbeat())).current_time


async def test_missing_subprotocol_tolerated(env):
    # Manche Wallboxen senden keinen Sec-WebSocket-Protocol-Header → als 1.6 behandeln
    async with connect_sim(env["port"], "OPEN", subprotocols=()) as cp:
        assert (await cp.call(call.Heartbeat())).current_time


async def test_foreign_subprotocol_rejected(env):
    with pytest.raises(websockets.InvalidStatus) as exc:
        async with connect_sim(env["port"], "OPEN", subprotocols=("ocpp2.0.1",)):
            pass
    assert exc.value.response.status_code == 400


async def test_reconnect_replaces_old_connection(env):
    async with connect_sim(env["port"], "OPEN"):
        async with connect_sim(env["port"], "OPEN") as second:
            assert (await second.call(call.Heartbeat())).current_time
            assert len(env["server"].connected) == 1


def test_path_and_auth_parsing():
    assert charge_point_id_from_path("/ocpp/CP001") == "CP001"
    assert charge_point_id_from_path("/CP001/?a=1") == "CP001"
    assert charge_point_id_from_path("/steve/websocket/CentralSystemService/ACE%200001") == "ACE 0001"
    assert charge_point_id_from_path("/") == ""
    assert parse_basic_auth("Basic Q1AxOnNlY3JldA==") == ("CP1", "secret")
    assert parse_basic_auth("Basic !!!") is None
    assert parse_basic_auth("Bearer x") is None
    assert parse_basic_auth(None) is None


# ---- D15: Karten-Klartext darf NIE ins Log (auch nicht auf Fehlerpfaden) ----

async def test_id_tag_plaintext_never_logged_even_at_debug(env, caplog):
    """D15 darf nicht nur am Log-Level hängen.

    Dreht jemand die Protokoll-Logger ausdrücklich auf DEBUG — z.B. beim
    späteren Verdrahten der Option log_level —, loggt die ocpp-Bibliothek jede
    Nachricht samt idTag. Der Schutz muss am Inhalt hängen, nicht am Level.
    """
    import logging
    for name in ("ocpp.expensecharge", "websockets.expensecharge"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    caplog.set_level("DEBUG")
    try:
        async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
            await cp.call(call.Authorize(id_tag="c0ffee42"))
            await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e",
                                                meter_start=0, timestamp=_ts(1)))
        server_side = [r for r in caplog.records if r.name not in ("ocpp_sim", "websockets.client")]
        text = "\n".join(r.getMessage() for r in server_side).upper()
        assert "EFCD083E" not in text and "C0FFEE42" not in text
    finally:
        logging.getLogger("ocpp.expensecharge").setLevel(logging.WARNING)
        logging.getLogger("websockets.expensecharge").setLevel(logging.INFO)


async def test_id_tag_plaintext_never_logged_when_handler_raises(env, caplog):
    """Wirft ein Handler, loggt die ocpp-Bibliothek die GANZE Nachricht auf
    ERROR — inklusive idTag im Klartext. Genau dieser Pfad ist im Betrieb
    normal (Schema-Verstoß der Wallbox, SQLite-Fehler) und muss sauber sein."""
    def boom(*args, **kwargs):
        raise RuntimeError("Simulierter Fehler im Handler")

    env["deps"].session_manager.start_ocpp_transaction = boom
    caplog.set_level("DEBUG")
    async with connect_sim(env["port"], "CP1", PASSWORD) as cp:
        try:
            await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                meter_start=0, timestamp=_ts(1)))
        except Exception:
            pass  # CallError ist hier erwartet — geprüft wird das Log
    server_side = [r for r in caplog.records if r.name not in ("ocpp_sim", "websockets.client")]
    text = "\n".join(r.getMessage() for r in server_side).upper()
    assert "EFCD083E" not in text, "idTag im Klartext auf dem Fehlerpfad"


def test_charge_point_id_from_path_is_bounded_and_safe_to_log():
    """Die Charge-Point-ID kommt von einem noch NICHT authentifizierten Peer und
    landet direkt in einer Logzeile. Ohne Begrenzung kann er damit das Log
    fluten; mit %0A kann er gefälschte Logzeilen einschleusen."""
    from ocpp_server.server import _MAX_LOGGED_CP_ID, safe_cp_id_for_log
    # +2 für die Anführungszeichen, die repr() setzt
    assert len(safe_cp_id_for_log("A" * 500)) <= _MAX_LOGGED_CP_ID + 2
    injected = safe_cp_id_for_log("CP1\nWARNUNG gefaelschte Zeile")
    assert "\n" not in injected and "\\n" in injected
    assert safe_cp_id_for_log("CP-001.a_2") == "'CP-001.a_2'"
