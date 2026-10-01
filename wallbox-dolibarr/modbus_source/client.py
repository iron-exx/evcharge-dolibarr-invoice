"""Lesender Modbus-TCP-Client (Funktionscodes 3 und 4).

Bewusst selbst geschrieben statt pymodbus: ExpenseCharge liest ausschließlich
Register und schreibt nie. Dafür braucht es keine Bibliothek — Modbus TCP ist
längenpräfigiert, also eindeutig zu rahmen — und das Image installiert
Abhängigkeiten über apk; `ocpp` ist dort schon die einzige pip-Ausnahme.

ponytail: nur lesend, nur FC3/FC4, kein Modbus-RTU und keine Schreibfunktionen.
Wird je geschrieben (Lastmanagement, Freigabe), ist pymodbus die bessere Basis.
"""
import asyncio
import logging
import struct
from typing import List, Optional

_LOGGER = logging.getLogger(__name__)

_MBAP_LEN = 7                    # Transaction(2) Protocol(2) Length(2) Unit(1)
_MAX_REGISTERS = 125             # Modbus-Grenze für eine Leseanfrage
VALID_FUNCTION_CODES = (3, 4)    # 3 = Holding Registers, 4 = Input Registers

# Aus der Modbus-Spezifikation, damit im Log nicht nur eine nackte Zahl steht.
_EXCEPTIONS = {
    1: 'Funktionscode nicht unterstützt',
    2: 'ungültige Registeradresse',
    3: 'ungültiger Wert',
    4: 'Gerätefehler',
    5: 'Anfrage angenommen, noch nicht fertig',
    6: 'Gerät beschäftigt',
    11: 'Gateway: Zielgerät antwortet nicht',
}


class ModbusError(Exception):
    """Lesen fehlgeschlagen — Verbindung, Timeout oder Modbus-Exception."""


class ModbusTcpClient:
    """Eine Verbindung, serialisierte Anfragen, Neuverbinden bei Abriss.

    Nicht nebenläufig benutzbar: ein Lock serialisiert die Anfragen, weil
    Modbus TCP Antworten nur über die Transaction-ID zuordnet und viele
    Wallboxen ohnehin nur eine offene Anfrage verkraften.
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = 1,
                 function_code: int = 3, timeout: float = 5.0):
        if function_code not in VALID_FUNCTION_CODES:
            raise ValueError(f"function_code muss {VALID_FUNCTION_CODES} sein, war {function_code}")
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.function_code = function_code
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._transaction = 0
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            self._reader = self._writer = None
            raise ModbusError(f"Verbindung zu {self.host}:{self.port} fehlgeschlagen: {exc}") from exc

    async def read(self, address: int, count: int = 1) -> List[int]:
        """Liest `count` Register ab `address` und gibt die rohen 16-Bit-Wörter zurück."""
        if not 1 <= count <= _MAX_REGISTERS:
            raise ValueError(f"count muss 1..{_MAX_REGISTERS} sein, war {count}")
        async with self._lock:
            try:
                return await asyncio.wait_for(self._read_once(address, count), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                await self._drop()
                raise ModbusError(
                    f"Zeitüberschreitung beim Lesen von {count} Register ab {address}") from exc
            except (OSError, asyncio.IncompleteReadError) as exc:
                await self._drop()
                raise ModbusError(f"Verbindung verloren beim Lesen ab {address}: {exc}") from exc

    async def _read_once(self, address: int, count: int) -> List[int]:
        await self._connect()
        self._transaction = (self._transaction + 1) & 0xFFFF
        tid = self._transaction
        pdu = struct.pack('>BHH', self.function_code, address, count)
        self._writer.write(struct.pack('>HHHB', tid, 0, len(pdu) + 1, self.unit_id) + pdu)
        await self._writer.drain()

        # readexactly setzt die Frames aus beliebig aufgeteilten TCP-Paketen zusammen.
        rtid, proto, length, unit = struct.unpack('>HHHB', await self._reader.readexactly(_MBAP_LEN))
        body = await self._reader.readexactly(length - 1)
        fc = body[0]

        if fc & 0x80:
            code = body[1] if len(body) > 1 else 0
            raise ModbusError(
                f"Modbus-Exception {code} ({_EXCEPTIONS.get(code, 'unbekannt')}) "
                f"beim Lesen von {count} Register ab {address}")
        if rtid != tid:
            await self._drop()
            raise ModbusError(f"Antwort passt nicht zur Anfrage (Transaction {rtid} statt {tid})")

        byte_count = body[1]
        data = body[2:2 + byte_count]
        if len(data) != count * 2:
            raise ModbusError(f"{count} Register erwartet, {len(data) // 2} erhalten")
        return [w[0] for w in struct.iter_unpack('>H', data)]

    async def _drop(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self) -> None:
        await self._drop()
