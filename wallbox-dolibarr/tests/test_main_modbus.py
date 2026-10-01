"""Verdrahtung der Betriebsart session_source=modbus in main.py."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.modbus_sim import FakeModbusServer  # noqa: E402

CONFIG = {
    "session_source": "modbus",
    "rfid_whitelist": ["EFCD083E"],
    "wallbox_id": "garage",
    "modbus": {
        "host": "127.0.0.1",
        "registers": {
            "energy": {"address": 100, "type": "uint32", "scale": 0.001},
            "state": {"address": 200, "state_map": {"3": "Charging", "2": "Available"}},
        },
    },
}


@pytest.fixture()
def wired(tmp_path):
    main.session_manager = SessionManager(db_path=str(tmp_path / "s.db"))
    main.current_config = dict(CONFIG)
    main.api_client = None
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None, "learn": main.learn_buffer}
    main.profile = main.wallbox_profile.resolve_profile(CONFIG)
    main._latest_rfid = None
    main._pending_auth = None
    main._tag_releaser = None
    yield main.session_manager


async def test_poller_feeds_the_existing_session_logic(wired, monkeypatch):
    """Der Modbus-Poller muss dieselbe sensor_callback bedienen wie der
    HA-Pfad — sonst läge die ganze getestete Logik daneben."""
    s = FakeModbusServer(registers={100: 0x0001, 101: 0x86A0, 200: 3})
    await s.start()
    main.current_config["modbus"]["port"] = s.port

    seen = []
    original = main.sensor_callback

    async def spy(entity_id, state):
        seen.append((entity_id, state["state"]))
        await original(entity_id, state)

    monkeypatch.setattr(main, "sensor_callback", spy)
    try:
        poller = main.build_modbus_poller(main.resolve_modbus_settings(main.current_config))
        await poller.poll_once()
        await poller.close()
    finally:
        await s.close()

    values = dict(seen)
    assert values[main.profile.sensor_energy] == pytest.approx(100.0)
    assert values[main.profile.sensor_state] == "Charging"
    assert main.api_state["current_energy"] == pytest.approx(100.0), \
        "Live-Zustand für die Web-UI muss mitlaufen"


async def test_main_modbus_mode_skips_home_assistant(wired, monkeypatch):
    """Im Modbus-Betrieb darf kein HA-Websocket aufgebaut werden."""
    calls = []

    async def fake_run(settings):
        calls.append(settings)

    def forbidden(*a, **kw):
        raise AssertionError("HA-Websocket darf im Modbus-Betrieb nicht genutzt werden")

    monkeypatch.setattr(main, "SessionManager", lambda db_path, **kw: wired)
    monkeypatch.setattr(main, "load_config", lambda: dict(CONFIG))
    monkeypatch.setattr(main, "run_modbus_mode", fake_run)
    monkeypatch.setattr(main, "HomeAssistantWebsocket", forbidden)
    await main.main()
    assert len(calls) == 1 and calls[0].enabled
    assert calls[0].host == "127.0.0.1"


async def test_broken_modbus_config_stops_the_addon_with_a_clear_error(wired, monkeypatch):
    """Eine unbrauchbare Registerkarte darf nicht mit Standardwerten
    weiterlaufen — sonst würden 0 kWh abgerechnet."""
    bad = {"session_source": "modbus", "modbus": {"host": "x", "registers": {}}}
    monkeypatch.setattr(main, "SessionManager", lambda db_path, **kw: wired)
    monkeypatch.setattr(main, "load_config", lambda: dict(bad))
    monkeypatch.setattr(main, "HomeAssistantWebsocket",
                        lambda *a, **kw: pytest.fail("kein HA im Modbus-Betrieb"))
    with pytest.raises(main.ModbusConfigError, match="energy"):
        await main.main()
