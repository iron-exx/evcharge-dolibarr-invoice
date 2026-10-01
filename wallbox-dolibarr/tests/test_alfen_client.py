"""Alfen-HTTPS-Client gegen eine nachgebaute Wallbox."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from alfen_source.client import AlfenError, AlfenHttpClient  # noqa: E402
from tests.alfen_sim import FakeAlfen  # noqa: E402

LINES = [
    "4711_1:txstart 2 Socket 1, 2026-10-01 08:12:34 1234.567kWh EFCD083E 1 y",
    "4712_1:txstop 2 Socket 1, 2026-10-01 10:45:02 1247.067kWh EFCD083E y",
    "4713_1:txstart 2 Socket 1, 2026-10-02 08:00:00 1247.067kWh EFCD083E 1 y",
    "4714_1:txstop 2 Socket 1, 2026-10-02 09:00:00 1255.067kWh EFCD083E y",
]


@pytest.fixture()
async def box():
    b = FakeAlfen(props={'2221_22': 1247.067, '2501_1': 'Charging Power On'}, lines=LINES)
    await b.start()
    yield b
    await b.close()


def _client(box, **kw):
    kw.setdefault('username', 'admin')
    kw.setdefault('password', 'geheim')
    return AlfenHttpClient(base_url=box.base_url, verify_ssl=False, timeout=5, **kw)


async def test_logs_in_and_reads_properties(box):
    c = _client(box)
    try:
        values = await c.get_properties(['2221_22', '2501_1'])
    finally:
        await c.close()
    assert values['2221_22'] == pytest.approx(1247.067)
    assert values['2501_1'] == 'Charging Power On'
    assert box.login_attempts == 1, "einmal anmelden reicht"


async def test_session_is_reused_across_calls(box):
    c = _client(box)
    try:
        await c.get_properties(['2221_22'])
        await c.get_properties(['2501_1'])
    finally:
        await c.close()
    assert box.login_attempts == 1, "nicht bei jedem Aufruf neu anmelden"


async def test_wrong_credentials_raise_instead_of_returning_nothing(box):
    c = _client(box, password='falsch')
    try:
        with pytest.raises(AlfenError, match="Anmeldung"):
            await c.get_properties(['2221_22'])
    finally:
        await c.close()


async def test_login_attempts_are_rate_limited(box):
    """Die Wallbox sperrt nach mehreren Fehlversuchen — wir dürfen sie nicht
    in die Sperre treiben."""
    c = _client(box, password='falsch', max_login_attempts=3, login_window=60)
    try:
        for _ in range(6):
            with pytest.raises(AlfenError):
                await c.get_properties(['2221_22'])
    finally:
        await c.close()
    assert box.login_attempts <= 3, f"{box.login_attempts} Anmeldeversuche trotz Begrenzung"


async def test_reads_the_transaction_log_across_pages(box):
    c = _client(box)
    try:
        lines = await c.get_transaction_lines()
    finally:
        await c.close()
    assert len(lines) == len(LINES)
    assert len(box.transaction_offsets) >= 2, "muss seitenweise lesen"


async def test_transaction_log_respects_the_page_limit(box):
    box.lines = LINES * 500
    c = _client(box, max_log_pages=2)
    try:
        lines = await c.get_transaction_lines()
    finally:
        await c.close()
    assert len(box.transaction_offsets) <= 2
    assert len(lines) <= 6


async def test_a_server_error_raises_and_does_not_fake_data(box):
    box.fail_prop_with = 500
    c = _client(box)
    try:
        with pytest.raises(AlfenError):
            await c.get_properties(['2221_22'])
    finally:
        await c.close()


async def test_unreachable_host_raises():
    import socket
    s = socket.socket(); s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]; s.close()
    c = AlfenHttpClient(base_url=f'http://127.0.0.1:{port}', username='a', password='b',
                        verify_ssl=False, timeout=1)
    try:
        with pytest.raises(AlfenError):
            await c.get_properties(['2221_22'])
    finally:
        await c.close()


async def test_credentials_never_appear_in_the_error_text(box):
    c = _client(box, password='SUPERGEHEIM')
    try:
        with pytest.raises(AlfenError) as exc:
            await c.get_properties(['2221_22'])
        assert 'SUPERGEHEIM' not in str(exc.value)
    finally:
        await c.close()
