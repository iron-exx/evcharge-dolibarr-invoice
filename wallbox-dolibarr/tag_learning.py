#!/usr/bin/env python3
"""Lernmodus für RFID-Karten.

Der Admin schaltet den Modus ein, hält eine Karte an die Wallbox und sieht sie
in der Oberfläche — dann kann er sie benennen und einordnen.

Zum Klartext: Der Admin MUSS die Karten-ID sehen, sonst kann er die Karte nicht
wiedererkennen und auch nicht in Dolibarr eintragen. Deshalb liegt sie hier —
aber ausschließlich im Arbeitsspeicher, zeitlich begrenzt, in begrenzter Zahl,
und sie verschwindet sofort beim Ausschalten des Modus. Persistiert wird nur
der Hash (siehe SessionManager.note_tag_seen). Das ist dieselbe Linie wie beim
OCPP-Pfad, der eine abgelehnte Karte ebenfalls nur flüchtig anzeigt.
"""
import logging
import time
from typing import List, Optional

from tag_release import is_none_value
from utils.hash import hash_rfid

_LOGGER = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600       # 10 Minuten reichen zum Benennen
DEFAULT_MAX_ENTRIES = 10


class LearnBuffer:
    """Flüchtige Liste zuletzt vorgehaltener Karten."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES):
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._enabled = False
        self._entries = {}          # normalisierter Tag → {tag, at, count}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        value = bool(value)
        if value != self._enabled:
            _LOGGER.info("Lernmodus %s", "eingeschaltet" if value else "ausgeschaltet")
        self._enabled = value
        if not value:
            # Ausschalten löscht den Klartext sofort — nicht nur die Anzeige.
            self._entries.clear()

    def observe(self, raw_tag, now: Optional[float] = None) -> bool:
        """Karte vorgehalten. True, wenn sie aufgenommen wurde."""
        if not self._enabled or is_none_value(raw_tag):
            return False
        tag = str(raw_tag).strip().upper()
        now = now if now is not None else time.monotonic()
        entry = self._entries.get(tag)
        if entry is None:
            self._entries[tag] = {'tag': tag, 'at': now, 'count': 1}
            _LOGGER.info("Lernmodus: Karte erkannt, %s... — in der Oberfläche benennen",
                         hash_rfid(tag)[:16])
        else:
            entry['at'] = now
            entry['count'] += 1
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        for tag, entry in list(self._entries.items()):
            if now - entry['at'] > self.ttl_seconds:
                del self._entries[tag]
        if len(self._entries) > self.max_entries:
            for tag, _ in sorted(self._entries.items(), key=lambda kv: kv[1]['at'])[
                    :len(self._entries) - self.max_entries]:
                del self._entries[tag]

    def detected(self, now: Optional[float] = None) -> List[dict]:
        """Zuletzt erkannte Karten, neueste zuerst."""
        if not self._enabled:
            return []
        now = now if now is not None else time.monotonic()
        self._prune(now)
        return [{
            'tag': e['tag'],
            'hash_prefix': hash_rfid(e['tag'])[:16],
            'count': e['count'],
            'seconds_ago': round(now - e['at'], 1),
        } for e in sorted(self._entries.values(), key=lambda e: e['at'], reverse=True)]
