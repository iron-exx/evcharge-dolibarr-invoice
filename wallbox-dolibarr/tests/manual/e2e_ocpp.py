#!/usr/bin/env python3
"""Ende-zu-Ende-Abnahme des OCPP-Betriebs ohne Docker und ohne echte Wallbox.

Fährt die Szenarien S1–S6 aus Task 12 gegen den ECHTEN Addon-Code
(main.build_ocpp_server + web_server.start_web_server), angetrieben von der
simulierten Wallbox aus tests/ocpp_sim.py.

    python tests/manual/e2e_ocpp.py

Nicht Teil der pytest-Suite: startet echte Server auf freien Ports und ist als
Abnahme-Werkzeug gedacht, nicht als Regressionstest.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import aiohttp                                        # noqa: E402
from ocpp.v16 import call                             # noqa: E402

import main                                           # noqa: E402
from ocpp_server.settings import resolve_ocpp_settings  # noqa: E402
from session_manager import SessionManager            # noqa: E402
from tests.ocpp_sim import connect_sim                # noqa: E402
from datetime import datetime, timedelta, timezone    # noqa: E402

CONFIG = {
    "session_source": "ocpp",
    "rfid_whitelist": ["EFCD083E"],
    "ocpp_charge_points": [{"id": "CS-SIM-00001", "wallbox_id": "garage"}],
    "min_session_kwh": 0.05,
}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def by_id(sessions, session_id):
    """Session mit dieser ID. get_completed_sessions() sortiert nach end_time DESC —
    in diesem Skript enden mehrere Sessions in derselben Sekunde, die Reihenfolge
    ist dann nicht definiert. Deshalb nie über den Index zugreifen."""
    return next((s for s in sessions if s["id"] == session_id), None)


def ts(hours_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def wire(db_path):
    """Addon-Globals setzen, wie main() es täte — ohne HA und ohne Dolibarr."""
    main.session_manager = SessionManager(db_path=db_path)
    main.current_config = dict(CONFIG)
    main.api_client = None
    main.api_state = {"client": None, "current_energy": None,
                      "wallbox_state": None, "last_update": None}
    main._transmit_requested.clear()
    return main.session_manager


async def live_json(web_port):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{web_port}/live.json") as r:
            return await r.json()


async def run():
    tmp = tempfile.mkdtemp(prefix="expensecharge-e2e-")
    db = os.path.join(tmp, "sessions.db")
    sm = wire(db)

    server = main.build_ocpp_server(resolve_ocpp_settings(main.current_config))
    port = await server.start("127.0.0.1", 0)
    web_port = 18099
    await main.start_web_server(sm, main.current_config, main.api_state, port=web_port)
    print(f"\nOCPP-Server auf Port {port}, Ingress-UI auf {web_port}, DB {db}\n")

    # ---- S1: Boot, Heartbeat, StatusNotification -------------------------
    print("S1  Boot, Heartbeat, StatusNotification")
    async with connect_sim(port, "CS-SIM-00001") as cp:
        boot = await cp.call(call.BootNotification(charge_point_vendor="SimVendor",
                                                   charge_point_model="SimModel"))
        await cp.call(call.Heartbeat())
        await cp.call(call.StatusNotification(connector_id=1, error_code="NoError",
                                              status="Available"))
        check("S1 BootNotification Accepted", boot.status == "Accepted", boot.status)
        data = await live_json(web_port)
        cps = data["charge_points"]
        check("S1 Wallbox in live.json verbunden",
              len(cps) == 1 and cps[0]["connected"] is True and cps[0]["wallbox_id"] == "garage",
              json.dumps(cps))

    # ---- S2: Ladung mit bekannter Karte ---------------------------------
    print("\nS2  Ladung mit EFCD083E")
    async with connect_sim(port, "CS-SIM-00001") as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="efcd083e",
                                                    meter_start=1_000_000, timestamp=ts(2)))
        check("S2 Start akzeptiert (Kleinschreibung toleriert)",
              start.id_tag_info["status"] == "Accepted" and start.transaction_id > 0,
              f"tx={start.transaction_id}")
        await cp.call(call.StatusNotification(connector_id=1, error_code="NoError",
                                              status="Charging"))
        await cp.call(call.MeterValues(connector_id=1, transaction_id=start.transaction_id,
                                       meter_value=[{"timestamp": ts(),
                                                     "sampledValue": [{"value": "1003250"}]}]))
        data = await live_json(web_port)
        sess = data["sessions"]
        check("S2 kWh steigt in der UI",
              len(sess) == 1 and abs(sess[0]["current_kwh"] - 3.25) < 1e-6,
              f"current_kwh={sess[0]['current_kwh'] if sess else None}")
        check("S2 Status-Chip Charging",
              data["charge_points"][0]["status"] == "Charging")
        await cp.call(call.StopTransaction(transaction_id=start.transaction_id,
                                           meter_stop=1_007_250, timestamp=ts(),
                                           reason="EVDisconnected"))
    done = sm.get_completed_sessions()
    check("S2 genau eine completed Session mit 7,25 kWh",
          len(done) == 1 and abs(done[0]["total_kwh"] - 7.25) < 1e-6,
          f"{[(d['id'], d['total_kwh'], d['wallbox_id']) for d in done]}")
    check("S2 Sofort-Übertragung angefordert", main._transmit_requested.is_set())

    # ---- S3: unbekannte Karte -------------------------------------------
    print("\nS3  Ladung mit unbekanntem Tag")
    async with connect_sim(port, "CS-SIM-00001") as cp:
        auth = await cp.call(call.Authorize(id_tag="DEADBEEF"))
        st = await cp.call(call.StartTransaction(connector_id=1, id_tag="DEADBEEF",
                                                 meter_start=1_007_250, timestamp=ts()))
        check("S3 Authorize Invalid", auth.id_tag_info["status"] == "Invalid")
        check("S3 Start abgelehnt, transactionId 0",
              st.transaction_id == 0 and st.id_tag_info["status"] == "Invalid")
        await cp.call(call.StopTransaction(transaction_id=0, meter_stop=1_009_000,
                                           timestamp=ts(), reason="DeAuthorized"))
        data = await live_json(web_port)
        check("S3 UI zeigt abgelehnte Karte",
              data["charge_points"][0]["last_rejected_id_tag"] == "DEADBEEF")
    check("S3 keine zusätzliche Abrechnung", len(sm.get_completed_sessions()) == 1)

    # ---- S4: Addon-Neustart mitten in der Ladung ------------------------
    print("\nS4  Addon-Neustart während der Ladung")
    open_ts = ts(1)
    async with connect_sim(port, "CS-SIM-00001") as cp:
        start = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                    meter_start=1_009_000, timestamp=open_ts))
        tx = start.transaction_id
    await server.close()                       # "Addon gestoppt"
    sm2 = wire(db)                             # "Addon neu gestartet", gleiche DB
    server = main.build_ocpp_server(resolve_ocpp_settings(main.current_config))
    port = await server.start("127.0.0.1", 0)
    offen = sm2.get_active_ocpp_sessions()
    check("S4 Session überlebt den Neustart als active",
          len(offen) == 1 and offen[0]["id"] == tx, f"offen={[(o['id'], o['status']) for o in offen]}")
    async with connect_sim(port, "CS-SIM-00001") as cp:
        await cp.call(call.StopTransaction(transaction_id=tx, meter_stop=1_014_000,
                                           timestamp=ts(), reason="Local"))
    done = sm2.get_completed_sessions()
    s4 = by_id(done, tx)
    check("S4 nach dem Stop genau eine weitere completed Session (5,0 kWh)",
          len(done) == 2 and s4 is not None and abs(s4["total_kwh"] - 5.0) < 1e-6,
          f"{[(d['id'], d['total_kwh']) for d in done]}")

    # ---- S5: Verbindungsabriss, Nachlieferung + Wiederholung ------------
    print("\nS5  Abriss: Start und Stop werden verspätet/doppelt nachgeliefert")
    lost_ts = ts(3)
    async with connect_sim(port, "CS-SIM-00001") as cp:
        a = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                meter_start=1_014_000, timestamp=lost_ts))
    # Verbindung weg — die Wallbox wiederholt denselben Start nach dem Reconnect
    async with connect_sim(port, "CS-SIM-00001") as cp:
        b = await cp.call(call.StartTransaction(connector_id=1, id_tag="EFCD083E",
                                                meter_start=1_014_000, timestamp=lost_ts))
        check("S5 wiederholter Start bekommt dieselbe transactionId",
              a.transaction_id == b.transaction_id, f"{a.transaction_id} vs {b.transaction_id}")
        await cp.call(call.StopTransaction(transaction_id=b.transaction_id,
                                           meter_stop=1_020_000, timestamp=ts(), reason="Local"))
        await cp.call(call.StopTransaction(transaction_id=b.transaction_id,
                                           meter_stop=1_099_000, timestamp=ts(), reason="Local"))
    done = sm2.get_completed_sessions()
    ids = [d["id"] for d in done]
    s5 = by_id(done, b.transaction_id)
    check("S5 doppelter Stop erzeugt keine zweite Abrechnung",
          len(done) == 3 and len(ids) == len(set(ids)), f"ids={ids}")
    check("S5 abgerechnet wird der ERSTE Stop (6,0 kWh), nicht der wiederholte",
          s5 is not None and abs(s5["total_kwh"] - 6.0) < 1e-6,
          f"{s5['total_kwh'] if s5 else None} kWh")
    check("S5 Zeitstempel der Wallbox übernommen (Start vor 3 h)",
          s5 is not None and s5["start_time"].startswith(
              (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H")),
          s5["start_time"] if s5 else None)

    # ---- S6: unbekannte Station -----------------------------------------
    print("\nS6  Station ohne Eintrag in ocpp_charge_points")
    import websockets
    try:
        async with connect_sim(port, "FREMD-00002"):
            check("S6 unbekannte Station abgewiesen", False, "Verbindung kam zustande!")
    except websockets.InvalidStatus as exc:
        check("S6 unbekannte Station abgewiesen (HTTP 404)",
              exc.response.status_code == 404, f"HTTP {exc.response.status_code}")

    await server.close()

    print("\n" + "=" * 70)
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} Prüfungen OK")
    if bad:
        print("FEHLGESCHLAGEN: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
    sys.exit(asyncio.run(run()))
