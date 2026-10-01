"""Fragt die Wallbox per Modbus TCP ab und speist die Session-Logik.

Der Poller ist absichtlich dumm: er liest Register, dekodiert sie und ruft
denselben Callback wie der Home-Assistant-Pfad (`sensor_callback`). Die ganze
Logik — Whitelist, Debounce, Autorisierungsmodus, Zustandserkennung,
Session-Start und -Ende — bleibt unverändert dort, wo sie schon getestet ist.

Wichtigste Regel: ein fehlgeschlagener Lesevorgang meldet NICHTS. Würde er
ersatzweise 0 liefern, käme eine Ladung mit 0 kWh in die Abrechnung oder eine
laufende Session würde mit falschem Zählerstand beendet.
"""
import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional

from modbus_source.client import ModbusError, ModbusTcpClient
from modbus_source.registers import decode_registers, decode_string
from modbus_source.settings import ModbusSettings, RegisterSpec
from tag_release import TagReleaser

_LOGGER = logging.getLogger(__name__)

# Nach einem Fehler nicht schneller als das Poll-Intervall wiederholen, aber
# auch nicht unbegrenzt bremsen — die Wallbox soll zügig wieder einsteigen.
_MAX_ERROR_BACKOFF = 60.0


class ModbusPoller:
    def __init__(self, settings: ModbusSettings, entity_ids: Dict[str, str],
                 callback: Callable[[str, dict], Awaitable[None]]):
        self._s = settings
        self._entities = entity_ids
        self._callback = callback
        self._client = ModbusTcpClient(settings.host, settings.port, settings.unit_id,
                                       settings.function_code, settings.timeout)
        self._warned = False          # nur einmal pro Ausfall meckern
        # Haftendes Tag-Register: viele Wallboxen behalten dauerhaft die letzte
        # Karte. Dieselbe Behandlung wie im HA-Pfad — gemeinsamer Helfer.
        self._tags = TagReleaser(hold_seconds=settings.rfid_hold_seconds)

    async def _read(self, spec: RegisterSpec):
        words = await self._client.read(spec.address, spec.count)
        if spec.type == 'string':
            return decode_string(words)
        return decode_registers(words, spec.type, spec.word_order, spec.scale)

    async def poll_once(self) -> bool:
        """Einen Durchlauf lesen. True bei Erfolg, False bei Lesefehler.

        Bei einem Fehler wird NICHTS an den Callback gemeldet — auch nicht die
        Register, die vorher noch erfolgreich gelesen wurden: ein halb
        gelesener Zustand ist nicht zuverlässig interpretierbar.
        """
        try:
            # Zuerst alles lesen, dann melden — kein Teilzustand nach außen.
            values = {}
            for key, spec in (('energy', self._s.energy), ('state', self._s.state),
                              ('rfid', self._s.rfid), ('power', self._s.power)):
                if spec is not None:
                    values[key] = (spec, await self._read(spec))
        except (ModbusError, ValueError) as exc:
            if not self._warned:
                _LOGGER.error("Modbus-Lesefehler bei %s:%s — es wird NICHTS gemeldet, "
                              "damit keine falschen kWh entstehen: %s",
                              self._s.host, self._s.port, exc)
                self._warned = True
            return False

        if self._warned:
            _LOGGER.info("Modbus-Verbindung zu %s:%s wieder da", self._s.host, self._s.port)
            self._warned = False

        # Reihenfolge: erst Zähler und Tag, dann der Zustand. Der Zustand löst
        # Start und Ende aus und soll auf aktuellen Werten entscheiden.
        for key in ('energy', 'rfid', 'power', 'state'):
            if key not in values:
                continue
            spec, value = values[key]
            entity_id = self._entities.get(key)
            if not entity_id:
                continue
            if key == 'rfid':
                reported = self._tags.observe(value)
                if reported is None:
                    continue            # haftender Wert, nichts Neues
                value = reported
            else:
                value = spec.translate(value)
            await self._callback(entity_id, {'state': value})
        return True

    async def run(self) -> None:
        """Dauerschleife. Überlebt jeden Fehler — eine nicht erreichbare
        Wallbox darf das Addon nicht beenden."""
        _LOGGER.info("Modbus-Poller startet: %s:%s (Unit %s, FC%s, alle %.0f s)",
                     self._s.host, self._s.port, self._s.unit_id,
                     self._s.function_code, self._s.poll_interval)
        backoff = self._s.poll_interval
        while True:
            try:
                ok = await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.exception("Modbus-Poller: unerwarteter Fehler: %s", exc)
                ok = False
            if ok:
                backoff = self._s.poll_interval
            else:
                backoff = min(backoff * 2, _MAX_ERROR_BACKOFF)
            await asyncio.sleep(backoff)

    async def close(self) -> None:
        await self._client.close()
