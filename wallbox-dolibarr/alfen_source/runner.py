"""Betriebszyklus der Alfen-HTTP-Quelle.

Zwei Takte, aus einem Grund: Eigenschaften abfragen ist günstig, das
Transaktions-Log zu lesen ist teuer (die Wallbox läuft dafür ihre gesamte
Historie durch). Darum:

* `poll_interval`        — Zählerstand und Zustand für die **Anzeige**
* `transaction_interval` — das Log für die **Abrechnung**

Die Abrechnung kommt AUSSCHLIESSLICH aus dem Log. Würden die abgefragten Werte
zusätzlich in die Sensor-Logik laufen, entstünde jede Ladung zweimal: einmal
als importierte Transaktion, einmal aus der Zustandsflanke. Der Live-Zustand
wird daher nur in `api_state` geschrieben, nie in `sensor_callback`.
"""
import asyncio
import logging
import time
from typing import Callable, Optional

from alfen_source.client import AlfenError, AlfenHttpClient
from alfen_source.importer import import_transactions
from alfen_source.transactions import parse_transaction_log

_LOGGER = logging.getLogger(__name__)
_MAX_ERROR_BACKOFF = 300.0


class AlfenRunner:
    def __init__(self, settings, session_manager, api_state: dict, wallbox_id: str,
                 whitelist=None, on_tag_seen: Optional[Callable[[str], object]] = None,
                 now: Callable[[], float] = time.monotonic):
        self._s = settings
        self._sm = session_manager
        self._state = api_state if api_state is not None else {}
        self._wallbox_id = wallbox_id
        self._whitelist = list(whitelist or [])
        self._on_tag_seen = on_tag_seen
        self._now = now
        self._client = AlfenHttpClient(
            base_url=settings.base_url, username=settings.username,
            password=settings.password, display_name=settings.display_name,
            verify_ssl=settings.verify_ssl, timeout=settings.timeout,
            max_log_pages=settings.max_log_pages)
        self._last_tx = 0.0
        self._warned = False

    async def poll_once(self, include_transactions: bool = False) -> bool:
        """Ein Durchlauf. True bei Erfolg, False bei Zugriffsfehler.

        Bei einem Fehler wird NICHTS geschrieben — kein ersatzweiser Zähler,
        keine erfundene Ladung.
        """
        try:
            values = await self._client.get_properties(
                [self._s.param_energy, self._s.param_state])

            energy = values.get(self._s.param_energy)
            state = values.get(self._s.param_state)
            if energy is not None:
                try:
                    self._state['current_energy'] = float(energy)
                except (TypeError, ValueError):
                    pass
            if state is not None:
                self._state['wallbox_state'] = str(state)
            from datetime import datetime
            self._state['last_update'] = datetime.now().strftime('%H:%M:%S')

            if include_transactions:
                lines = await self._client.get_transaction_lines()
                sessions = parse_transaction_log(lines)
                result = import_transactions(
                    self._sm, sessions, wallbox_id=self._wallbox_id,
                    whitelist=self._whitelist, on_tag_seen=self._on_tag_seen)
                if result['imported']:
                    _LOGGER.info("Alfen-Log: %d neue Ladung(en) übernommen "
                                 "(%d bereits bekannt)",
                                 result['imported'], result['already_known'])
                if result['unauthorized']:
                    _LOGGER.warning("Alfen-Log: %d Ladung(en) mit nicht freigeschalteter "
                                    "Karte — im Tab Karten einordnen",
                                    result['unauthorized'])
                self._last_tx = self._now()
        except AlfenError as exc:
            if not self._warned:
                _LOGGER.error("Alfen-Zugriff fehlgeschlagen — es wird NICHTS geschrieben, "
                              "damit keine falschen Werte entstehen: %s", exc)
                self._warned = True
            return False
        except Exception as exc:
            _LOGGER.exception("Alfen-Durchlauf unerwartet fehlgeschlagen: %s", exc)
            return False

        if self._warned:
            _LOGGER.info("Alfen-Verbindung zu %s wieder da", self._s.base_url)
            self._warned = False
        return True

    async def run(self) -> None:
        _LOGGER.info("Alfen-HTTP-Quelle startet: %s (Abfrage alle %.0f s, "
                     "Transaktions-Log alle %.0f s)",
                     self._s.base_url, self._s.poll_interval, self._s.transaction_interval)
        backoff = self._s.poll_interval
        # Beim Start sofort das Log lesen, damit offene Ladungen nachkommen.
        first = True
        while True:
            due = first or (self._now() - self._last_tx) >= self._s.transaction_interval
            first = False
            try:
                ok = await self.poll_once(include_transactions=due)
            except asyncio.CancelledError:
                raise
            backoff = self._s.poll_interval if ok else min(backoff * 2, _MAX_ERROR_BACKOFF)
            await asyncio.sleep(backoff)

    async def close(self) -> None:
        await self._client.close()
