"""Modbus-Poller: liest Register und speist die bestehende Session-Logik."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402
import pytest  # noqa: E402

from modbus_source.poller import ModbusPoller  # noqa: E402
from modbus_source.settings import resolve_modbus_settings  # noqa: E402
from tests.modbus_sim import FakeModbusServer  # noqa: E402

ENTITIES = {"energy": "modbus.energy", "state": "modbus.state", "rfid": "modbus.rfid",
            "power": "modbus.power"}


def _cfg(port, **extra):
    regs = {"energy": {"address": 100, "type": "uint32", "scale": 0.001},
            "state": {"address": 200, "state_map": {"3": "Charging", "2": "Available"}},
            "rfid": {"address": 300, "count": 4, "type": "string"}}
    regs.update(extra.pop("registers", {}))
    return {"session_source": "modbus",
            "modbus": {"host": "127.0.0.1", "port": port, "registers": regs, **extra}}


async def _poll_once(server, cfg_extra=None):
    seen = []

    async def cb(entity_id, state):
        seen.append((entity_id, state["state"]))

    settings = resolve_modbus_settings(_cfg(server.port, **(cfg_extra or {})))
    poller = ModbusPoller(settings, entity_ids=ENTITIES, callback=cb)
    try:
        await poller.poll_once()
    finally:
        await poller.close()
    return dict(seen), seen


async def test_reads_and_decodes_every_configured_register():
    s = FakeModbusServer(registers={
        100: 0x0001, 101: 0x86A0,          # 100000 Wh → 100.0 kWh
        200: 3,                            # Zustandscode 3
        300: 0x4546, 301: 0x4344, 302: 0x3038, 303: 0x3345,   # "EFCD083E"
    })
    await s.start()
    try:
        values, _ = await _poll_once(s)
    finally:
        await s.close()
    assert values["modbus.energy"] == pytest.approx(100.0)
    assert values["modbus.state"] == "Charging", "Zahlencode muss übersetzt werden"
    assert values["modbus.rfid"] == "EFCD083E"


async def test_unknown_state_code_is_passed_through_visibly():
    s = FakeModbusServer(registers={100: 0, 101: 0, 200: 7})
    await s.start()
    try:
        values, _ = await _poll_once(s)
    finally:
        await s.close()
    assert values["modbus.state"] == "7"


async def test_empty_rfid_register_reports_nothing():
    """Ein von Anfang an leeres Tag-Register ist keine Flanke — es gibt nichts
    zu melden. Gemeldet wird nur ein Wechsel oder der selbst erzeugte Reset,
    sonst würde bei jedem Durchlauf 'kein Tag' erneut gefeuert."""
    s = FakeModbusServer(registers={100: 0, 101: 0, 200: 2, 300: 0, 301: 0, 302: 0, 303: 0})
    await s.start()
    try:
        values, _ = await _poll_once(s)
    finally:
        await s.close()
    assert "modbus.rfid" not in values
    assert values["modbus.energy"] == 0.0, "Zähler wird trotzdem gemeldet"


async def test_failed_read_delivers_nothing_instead_of_zero():
    """Antwortet die Wallbox mit einer Modbus-Exception, darf daraus NIEMALS
    ein Wert 0 werden: das würde 0 kWh abrechnen bzw. eine laufende Session
    mit falschem Zählerstand beenden."""
    s = FakeModbusServer(fail_with=2)
    await s.start()
    try:
        values, seen = await _poll_once(s)
    finally:
        await s.close()
    assert seen == [], f"es wurde trotz Lesefehler etwas gemeldet: {seen}"


async def test_recovers_after_the_wallbox_stops_failing():
    """Die Wallbox antwortet erst mit einer Modbus-Exception, dann wieder
    normal. Der Poller muss sich erholen, ohne neu gebaut zu werden."""
    s = FakeModbusServer(registers={100: 0x0000, 101: 0x2710, 200: 2}, fail_with=2)
    await s.start()

    seen = []

    async def cb(entity_id, state):
        seen.append(entity_id)

    settings = resolve_modbus_settings(_cfg(s.port))
    poller = ModbusPoller(settings, entity_ids=ENTITIES, callback=cb)
    try:
        assert await poller.poll_once() is False
        assert seen == [], "bei Lesefehler darf nichts gemeldet werden"

        s.fail_with = None                     # Wallbox wieder gesund
        assert await poller.poll_once() is True
        assert "modbus.energy" in seen
    finally:
        await poller.close()
        await s.close()


async def test_run_loop_survives_an_unreachable_wallbox():
    """Eine nicht erreichbare Wallbox darf den Loop nicht beenden — sonst
    bliebe das Addon bis zum Neustart stumm."""
    import socket
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]; sock.close()

    seen = []

    async def cb(entity_id, state):
        seen.append(entity_id)

    settings = resolve_modbus_settings(_cfg(port, poll_interval=1, timeout=0.2))
    poller = ModbusPoller(settings, entity_ids=ENTITIES, callback=cb)
    task = asyncio.create_task(poller.run())
    try:
        await asyncio.sleep(0.8)
        assert seen == [], "ohne erreichbare Wallbox darf nichts gemeldet werden"
        assert not task.done(), "Loop ist am Verbindungsfehler gestorben"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await poller.close()


# ---- Haftendes Tag-Register (Hinweis aus dem Praxisbetrieb) ---------------

async def test_sticky_tag_register_is_reset_after_the_hold_time():
    """Viele Wallboxen halten im Tag-Register die LETZTE Karte dauerhaft.
    Ein Poller sähe sie dann endlos und würde den Zustand durcheinander
    bringen. Der Poller muss den Rückfall auf "kein Tag" selbst erzeugen:
    Karte einmal melden, danach leer.
    """
    s = FakeModbusServer(registers={
        100: 0x0000, 101: 0x2710, 200: 2,
        300: 0x4546, 301: 0x4344, 302: 0x3038, 303: 0x3345,   # "EFCD083E", haftend
    })
    await s.start()

    seen = []

    async def cb(entity_id, state):
        if entity_id == ENTITIES["rfid"]:
            seen.append(state["state"])

    settings = resolve_modbus_settings(_cfg(s.port, rfid_hold_seconds=0.05))
    poller = ModbusPoller(settings, entity_ids=ENTITIES, callback=cb)
    try:
        await poller.poll_once()                       # Karte neu → melden
        assert seen == ["EFCD083E"]

        await asyncio.sleep(0.06)
        await poller.poll_once()                       # Haltezeit um → Reset
        assert seen == ["EFCD083E", ""]

        await poller.poll_once()                       # danach Ruhe
        await poller.poll_once()
        assert seen == ["EFCD083E", ""], f"Reset wurde wiederholt: {seen}"
    finally:
        await poller.close()
        await s.close()


async def test_a_different_card_is_reported_again():
    """Nach dem Reset muss eine ANDERE Karte wieder erkannt werden."""
    s = FakeModbusServer(registers={
        100: 0x0000, 101: 0x2710, 200: 2,
        300: 0x4142, 301: 0x0000, 302: 0x0000, 303: 0x0000,   # "AB"
    })
    await s.start()

    seen = []

    async def cb(entity_id, state):
        if entity_id == ENTITIES["rfid"]:
            seen.append(state["state"])

    settings = resolve_modbus_settings(_cfg(s.port, rfid_hold_seconds=0.05))
    poller = ModbusPoller(settings, entity_ids=ENTITIES, callback=cb)
    try:
        await poller.poll_once()
        await asyncio.sleep(0.06)
        await poller.poll_once()                       # Reset
        s.registers.update({300: 0x4344})              # neue Karte "CD"
        await poller.poll_once()
        assert seen == ["AB", "", "CD"]
    finally:
        await poller.close()
        await s.close()
