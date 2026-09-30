"""live.json im OCPP-Betrieb: Wallbox-Liste und kWh je Session."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from web_server import create_app  # noqa: E402


async def test_live_json_lists_charge_points_and_session_kwh(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    tx = sm.start_ocpp_transaction("EFCD083E", "garage", "CP1", 1, 1000.0,
                                   "2026-09-30T10:00:00", "2026-09-30T08:00:00Z")
    sm.update_ocpp_meter(tx, "CP1", 1003.5)
    api_state = {"client": None, "current_energy": None, "wallbox_state": None, "last_update": None,
                 "charge_points": {"CP1": {"connected": True, "wallbox_id": "garage", "vendor": "Alfen BV",
                                           "connectors": {"1": {"status": "Charging", "energy_kwh": 1003.5}},
                                           "last_rejected_id_tag": "DEADBEEF"}}}
    async with TestClient(TestServer(create_app(sm, {"wallbox_id": "garage"}, api_state))) as client:
        data = await (await client.get("/live.json")).json()
    assert data["sessions"][0]["current_kwh"] == 3.5
    cp = data["charge_points"][0]
    assert cp["id"] == "CP1" and cp["status"] == "Charging" and cp["connected"] is True
    assert cp["last_rejected_id_tag"] == "DEADBEEF"


async def test_live_json_ha_mode_has_empty_charge_points(tmp_path):
    sm = SessionManager(db_path=str(tmp_path / "s.db"))
    api_state = {"client": None, "current_energy": 5.0, "wallbox_state": "Charging", "last_update": None}
    async with TestClient(TestServer(create_app(sm, {}, api_state))) as client:
        data = await (await client.get("/live.json")).json()
    assert data["charge_points"] == []
    assert data["sensor"]["current_energy"] == 5.0
