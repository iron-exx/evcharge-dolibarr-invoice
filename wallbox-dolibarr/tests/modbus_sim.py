"""Minimaler Modbus-TCP-Server für Tests (nur Lesefunktionen FC3/FC4)."""
import asyncio
import struct


class FakeModbusServer:
    def __init__(self, registers=None, unit_id=1, fail_with=None, drip=False):
        self.registers = dict(registers or {})      # Adresse → 16-Bit-Wort
        self.unit_id = unit_id
        self.fail_with = fail_with                  # Modbus-Exception-Code oder None
        self.drip = drip                            # Antwort in zwei TCP-Paketen senden
        self.requests = []
        self._server = None
        self.port = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(7)
                tid, proto, length, unit = struct.unpack('>HHHB', header)
                body = await reader.readexactly(length - 1)
                fc = body[0]
                addr, count = struct.unpack('>HH', body[1:5])
                self.requests.append((unit, fc, addr, count))

                if self.fail_with is not None:
                    pdu = struct.pack('>BB', fc | 0x80, self.fail_with)
                else:
                    words = [self.registers.get(addr + i, 0) for i in range(count)]
                    data = b''.join(struct.pack('>H', w) for w in words)
                    pdu = struct.pack('>BB', fc, len(data)) + data

                frame = struct.pack('>HHHB', tid, 0, len(pdu) + 1, unit) + pdu
                if self.drip and len(frame) > 8:
                    writer.write(frame[:6]); await writer.drain()
                    await asyncio.sleep(0.01)
                    writer.write(frame[6:])
                else:
                    writer.write(frame)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
