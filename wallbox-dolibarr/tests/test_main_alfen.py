"""Betriebsart session_source=alfen_http in main.py."""
import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402
from session_manager import SessionManager  # noqa: E402
from tests.alfen_sim import FakeAlfen  # noqa: E402

LINES = [
    "4711_1:txstart 2 Socket 1, 2026-10-01 08:12:34 1234.567kWh EFCD083E 1 y",
    "4712_1:txstop 2 Socket 1, 2026-10-01 10:45:02 1247.067kWh EFCD083E y",
]


@pytest.fixture()
async def box():
    b = FakeAlfen(props={'2221_22': 1247.067, '2501_1': 'Charging Power On'}, lines=LINES)
    await b.start()
    yield b
    await b.close()


@pytest.fixture()
def wired(tmp_path, box):
    main.session_manager = SessionManager(db_path=str(tmp_path / "s.db"))
    main.current_config = {
        "session_source": "alfen_http",
        "rfid_whitelist": ["EFCD083E"],
        "wallbox_id": "garage",
        "alfen": {"host": box.base_url, "username": "admin", "password": "geheim"},
    }
    main.api_client = None
    main.api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                      "last_update": None, "learn": main.learn_buffer,
                      "settings": main.app_settings}
    main.profile = main.wallbox_profile.resolve_profile(main.current_config)
    yield main.session_manager


def _rows(sm):
    conn = sqlite3.connect(sm.db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM sessions")]
    conn.close()
    return rows


async def test_one_cycle_imports_the_log_and_updates_the_live_state(wired, box):
    runner = main.build_alfen_runner(main.resolve_alfen_settings(main.current_config))
    try:
        await runner.poll_once(include_transactions=True)
    finally:
        await runner.close()

    rows = _rows(wired)
    assert len(rows) == 1
    assert rows[0]['total_kwh'] == pytest.approx(12.5)
    assert rows[0]['status'] == 'completed'
    assert main.api_state['current_energy'] == pytest.approx(1247.067), \
        "Live-Zähler für die Oberfläche muss mitlaufen"
    assert main.api_state['wallbox_state'] == 'Charging Power On'


async def test_billing_comes_only_from_the_log_never_from_the_polled_state(wired, box, monkeypatch):
    """Würde der Zustand zusätzlich in sensor_callback laufen, entstünde
    dieselbe Ladung ein zweites Mal — einmal aus dem Log, einmal aus der
    Flanke. Das darf nicht passieren."""
    called = []
    monkeypatch.setattr(main, "sensor_callback",
                        lambda *a, **kw: called.append(a) or None)
    runner = main.build_alfen_runner(main.resolve_alfen_settings(main.current_config))
    try:
        await runner.poll_once(include_transactions=True)
    finally:
        await runner.close()
    assert called == [], "sensor_callback darf im Alfen-Betrieb nicht benutzt werden"
    assert len(_rows(wired)) == 1


async def test_repeated_cycles_bill_once(wired, box):
    runner = main.build_alfen_runner(main.resolve_alfen_settings(main.current_config))
    try:
        for _ in range(3):
            await runner.poll_once(include_transactions=True)
    finally:
        await runner.close()
    assert len(_rows(wired)) == 1, "das Log wird jedes Mal komplett gelesen"


async def test_an_unreachable_wallbox_does_not_kill_the_cycle(wired, box):
    await box.close()
    runner = main.build_alfen_runner(main.resolve_alfen_settings(main.current_config))
    try:
        ok = await runner.poll_once(include_transactions=True)
    finally:
        await runner.close()
    assert ok is False
    assert _rows(wired) == [], "bei Lesefehler darf nichts erfunden werden"


async def test_main_alfen_mode_skips_home_assistant(wired, box, monkeypatch):
    calls = []

    async def fake_run(settings):
        calls.append(settings)

    monkeypatch.setattr(main, "SessionManager", lambda db_path, **kw: wired)
    monkeypatch.setattr(main, "load_config", lambda: dict(main.current_config))
    monkeypatch.setattr(main, "run_alfen_mode", fake_run)
    monkeypatch.setattr(main, "HomeAssistantWebsocket",
                        lambda *a, **kw: pytest.fail("kein HA im Alfen-Betrieb"))
    await main.main()
    assert len(calls) == 1 and calls[0].enabled
