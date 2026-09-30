"""Verdrahtung der Betriebsart session_source=ocpp in main.py."""
import os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from ocpp.v16 import call  # noqa: E402

import main  # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.ocpp_sim import connect_sim  # noqa: E402

CONFIG = {"session_source": "ocpp", "rfid_whitelist": ["EFCD083E"],
          "ocpp_charge_points": [{"id": "CP1", "wallbox_id": "garage"}]}


@pytest.fixture()
def ocpp_env(tmp_path):
    main.session_manager = SessionManager(db_path=str(tmp_path / "sessions.db"))
    main.current_config = dict(CONFIG)
    main.api_client = None
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None, "last_update": None}
    main._transmit_requested.clear()
    yield main.session_manager


async def test_completed_session_requests_immediate_transmit(ocpp_env):
    server = main.build_ocpp_server(resolve_ocpp_settings(main.current_config))
    port = await server.start("127.0.0.1", 0)
    try:
        start_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with connect_sim(port, "CP1") as cp:
            start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                        meter_start=0, timestamp=start_ts))
            await cp.call(call.StopTransaction(transaction_id=start.transaction_id, meter_stop=7000,
                                               timestamp=datetime.now(timezone.utc).isoformat()))
    finally:
        await server.close()
    assert main._transmit_requested.is_set()
    assert "CP1" in main.api_state["charge_points"]
    assert ocpp_env.get_completed_sessions()[0]["wallbox_id"] == "garage"


def test_overdue_sessions(ocpp_env):
    ocpp_env.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 0.0,
                                    "2026-09-28T08:00:00", "2026-09-28T06:00:00Z")
    assert len(main.ocpp_overdue_sessions(24, now=datetime(2026, 9, 30, 8, 0))) == 1
    assert main.ocpp_overdue_sessions(72, now=datetime(2026, 9, 30, 8, 0)) == []


async def test_main_ocpp_mode_skips_home_assistant_and_recovery(ocpp_env, monkeypatch):
    # Laufende OCPP-Session von vor dem "Neustart" muss aktiv bleiben
    ocpp_env.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 0.0,
                                    "2026-09-30T08:00:00", "2026-09-30T06:00:00Z")
    calls = []

    async def fake_run(settings):
        calls.append(settings)

    def forbidden(*a, **kw):
        raise AssertionError("HA-Websocket darf im OCPP-Betrieb nicht genutzt werden")

    monkeypatch.setattr(main, "SessionManager", lambda db_path: ocpp_env)
    monkeypatch.setattr(main, "load_config", lambda: dict(CONFIG))
    monkeypatch.setattr(main, "run_ocpp_mode", fake_run)
    monkeypatch.setattr(main, "HomeAssistantWebsocket", forbidden)
    await main.main()
    assert len(calls) == 1 and calls[0].enabled
    assert len(ocpp_env.get_active_ocpp_sessions()) == 1
