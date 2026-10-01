"""Private Ladungen dürfen nicht als 'ausstehend' gezählt werden.

Sie werden absichtlich nie übertragen (transmitted_at bleibt NULL). Zählt man
sie als ausstehend, steht in der Oberfläche dauerhaft eine Warnung, die sich
nie auflösen lässt.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from session_manager import SessionManager  # noqa: E402
from web_server import _db_stats_month, create_app  # noqa: E402


class _Api:
    def transmit_session(self, data):
        return True, "ok"


def _charge(sm, tag, kwh, ts):
    tx = sm.start_ocpp_transaction(rfid_hex=tag, wallbox_id="garage", charge_point_id="CP1",
                                   connector_id=1, meter_start_kwh=0.0,
                                   start_time=ts, ocpp_start_timestamp=f"{ts}#{tag}")
    sm.stop_ocpp_transaction(tx, "CP1", kwh, ts.replace("T10:", "T12:"), "Local")
    return tx


@pytest.fixture()
def sm(tmp_path):
    from datetime import datetime
    now = datetime.now()
    s = SessionManager(db_path=str(tmp_path / "s.db"))
    s._now_month = f"{now.year}-{now.month:02d}"
    return s


def test_private_session_is_not_counted_as_pending(sm):
    from datetime import datetime
    now = datetime.now()
    ts = f"{now.year}-{now.month:02d}-{min(now.day, 28):02d}T10:00:00"

    sm.upsert_tag("AABBCCDD", label="Privat Meier", mode="private")
    sm.upsert_tag("EFCD083E", label="Firmenwagen", mode="business")
    _charge(sm, "AABBCCDD", 7.0, ts)
    _charge(sm, "EFCD083E", 5.0, ts)
    sm.transmit_completed_sessions(_Api())        # geschäftliche geht raus, private wird 'private'

    stats = _db_stats_month(sm.db_path)
    assert stats["pending"] == 0, \
        f"private Ladung als ausstehend gezählt (pending={stats['pending']})"


async def test_history_page_does_not_report_private_as_pending(sm, tmp_path):
    from datetime import datetime
    now = datetime.now()
    ts = f"{now.year}-{now.month:02d}-{min(now.day, 28):02d}T10:00:00"
    sm.upsert_tag("AABBCCDD", label="Privat Meier", mode="private")
    _charge(sm, "AABBCCDD", 7.0, ts)
    sm.transmit_completed_sessions(_Api())

    api_state = {"client": None, "current_energy": None, "wallbox_state": None,
                 "last_update": None}
    async with TestClient(TestServer(create_app(sm, {}, api_state))) as c:
        body = await (await c.get(f"/history?year={now.year}&month={now.month}")).text()
    assert "1 ausst." not in body, "private Ladung in der Verlaufs-Zeile als ausstehend"
    assert "0 ausst." in body
