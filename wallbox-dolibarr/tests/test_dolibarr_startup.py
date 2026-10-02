"""Dolibarr beim Start nicht erreichbar.

Praxisbefund aus dem ersten echten Container-Lauf: Das Log meldete "Dolibarr
API nicht erreichbar — wird später erneut versucht", aber der Code setzte den
API-Client danach auf None. Die Übertragung startet nur, wenn er existiert —
war Dolibarr beim Containerstart kurz weg, wurde bis zum nächsten Neustart
KEINE Ladung übertragen. Außerdem blockierte die Prüfung den Start, bis alle
Wiederholungen durch waren; so lange nahm der OCPP-Server keine Wallbox an.
"""
import asyncio
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402
from session_manager import SessionManager  # noqa: E402

CONFIG = {"session_source": "ocpp", "rfid_whitelist": [],
          "ocpp_charge_points": [{"id": "CP1"}],
          "api": {"dolibarr_url": "https://erp.invalid", "api_token": "t"}}


class _SlowDownDolibarr:
    """Dolibarr, das nicht antwortet — wie eine Firewall, die verwirft."""
    probes = 0

    def __init__(self, *a, **kw):
        pass

    def check_connection(self):
        type(self).probes += 1
        time.sleep(1.0)
        return False


@pytest.fixture()
def boot(tmp_path, monkeypatch):
    calls = {}

    async def fake_run(settings):
        calls['api_client'] = main.api_client
        calls['started_at'] = time.monotonic()

    monkeypatch.setattr(main, "SessionManager",
                        lambda db_path, **kw: SessionManager(db_path=str(tmp_path / "s.db")))
    monkeypatch.setattr(main, "load_config", lambda: dict(CONFIG))
    monkeypatch.setattr(main, "run_ocpp_mode", fake_run)
    monkeypatch.setattr(main, "WallboxApiClient", _SlowDownDolibarr)
    _SlowDownDolibarr.probes = 0
    return calls


async def test_unreachable_dolibarr_keeps_the_client_for_later_retries(boot):
    await main.main()
    assert boot['api_client'] is not None, \
        "API-Client wurde verworfen — es würde bis zum Neustart nie übertragen"


async def test_startup_is_not_blocked_by_an_unreachable_dolibarr(boot):
    t0 = time.monotonic()
    await main.main()
    waited = boot['started_at'] - t0
    assert waited < 0.5, f"OCPP-Server startete erst nach {waited:.1f}s — Dolibarr-Prüfung blockiert"


async def test_connection_is_probed_in_the_background(boot):
    await main.main()
    for _ in range(40):
        await asyncio.sleep(0.05)
        if _SlowDownDolibarr.probes:
            break
    assert _SlowDownDolibarr.probes >= 1, "Dolibarr wurde nie geprüft"
