"""Lesender Modbus-TCP-Client gegen einen echten (Fake-)Server."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modbus_source.client import ModbusError, ModbusTcpClient  # noqa: E402
from tests.modbus_sim import FakeModbusServer  # noqa: E402


@pytest.fixture()
async def server():
    s = FakeModbusServer(registers={100: 0x0001, 101: 0x86A0, 200: 1234}, unit_id=3)
    await s.start()
    yield s
    await s.close()


async def test_read_holding_registers(server):
    c = ModbusTcpClient('127.0.0.1', server.port, unit_id=3)
    try:
        assert await c.read(100, 2) == [0x0001, 0x86A0]
        assert await c.read(200, 1) == [1234]
    finally:
        await c.close()
    assert server.requests[0] == (3, 3, 100, 2), "muss FC3 mit der konfigurierten Unit-ID senden"


async def test_read_input_registers_uses_fc4(server):
    c = ModbusTcpClient('127.0.0.1', server.port, unit_id=3, function_code=4)
    try:
        await c.read(100, 1)
    finally:
        await c.close()
    assert server.requests[0][1] == 4


async def test_response_split_across_packets_is_reassembled():
    s = FakeModbusServer(registers={10: 0xAAAA, 11: 0xBBBB}, drip=True)
    await s.start()
    c = ModbusTcpClient('127.0.0.1', s.port)
    try:
        assert await c.read(10, 2) == [0xAAAA, 0xBBBB]
    finally:
        await c.close()
        await s.close()


async def test_modbus_exception_is_raised_not_swallowed():
    """Antwortet die Wallbox mit einer Modbus-Exception (z.B. 2 = ungültige
    Adresse), darf daraus niemals ein stiller Nullwert werden — sonst würde
    0 kWh abgerechnet."""
    s = FakeModbusServer(fail_with=2)
    await s.start()
    c = ModbusTcpClient('127.0.0.1', s.port)
    try:
        with pytest.raises(ModbusError) as exc:
            await c.read(999, 1)
        assert "2" in str(exc.value)
    finally:
        await c.close()
        await s.close()


async def test_connection_refused_raises_modbus_error():
    import socket
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    free_port = sock.getsockname()[1]; sock.close()
    c = ModbusTcpClient('127.0.0.1', free_port, timeout=1.0)
    with pytest.raises(ModbusError):
        await c.read(0, 1)


async def test_reconnects_after_the_server_drops_the_link(server):
    c = ModbusTcpClient('127.0.0.1', server.port, unit_id=3)
    try:
        assert await c.read(200, 1) == [1234]
        await c.close()                      # Verbindung weg
        assert await c.read(200, 1) == [1234], "muss selbst neu verbinden"
    finally:
        await c.close()
