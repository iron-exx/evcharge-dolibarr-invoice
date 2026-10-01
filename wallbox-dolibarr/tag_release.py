#!/usr/bin/env python3
"""Rückfall auf "kein Tag" selbst erzeugen, wenn der Sensor den Wert hält.

Hintergrund (Praxisbefund): Die Alfen-HA-Integration leitet den RFID-Tag aus
dem Transaktions-Log der Wallbox ab — also aus dem letzten abgeschlossenen
Ladevorgang. Der Wert bleibt deshalb stehen, bis eine neue Transaktion
auftaucht, und nicht nur solange jemand die Karte vorhält. Dasselbe gilt für
Modbus-Register, die den letzten Tag speichern.

Für die Session-Logik ist ein dauerhaft anliegender Tag irreführend: sie
unterscheidet "Karte liegt an" von "Karte weg". Dieser Helfer macht daraus ein
Flankensignal — Tag einmal melden, danach nach `hold_seconds` genau einmal den
Rückfall auf "kein Tag".

Benutzt von beiden Datenquellen: dem Home-Assistant-Pfad und dem
Modbus-Poller.
"""
import logging
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Werte, die bereits "keine Karte" bedeuten — identisch zur Liste in main.py,
# damit beide Pfade dasselbe als Ruhezustand verstehen.
NONE_VALUES = {'', 'no tag', 'no_tag', 'none', 'unknown', 'unavailable'}

RELEASED = ''          # was als "kein Tag" nach außen gemeldet wird


def is_none_value(raw) -> bool:
    return str(raw or '').strip().lower() in NONE_VALUES


class TagReleaser:
    """Macht aus einem haltenden Tag-Wert ein Flankensignal.

    `observe()` liefert:
      - den Tag        → neu vorgehalten, Session darf starten
      - RELEASED ('')  → Karte gilt als weg (echtes "No Tag" oder selbst erzeugt)
      - None           → nichts Neues, nicht weitermelden

    hold_seconds == 0 schaltet den selbst erzeugten Reset ab; echte
    "No Tag"-Meldungen werden dann weiterhin durchgelassen.
    """

    def __init__(self, hold_seconds: float = 1.0):
        self.hold_seconds = max(0.0, float(hold_seconds))
        self._value = ''
        self._seen_at = 0.0
        self._released = True

    def observe(self, raw_tag, now: Optional[float] = None) -> Optional[str]:
        now = now if now is not None else time.monotonic()

        if is_none_value(raw_tag):
            # Echtes "keine Karte" vom Sensor: durchlassen, aber nur beim Wechsel.
            if self._value or not self._released:
                self._value, self._released = '', True
                return RELEASED
            return None

        tag = str(raw_tag).strip()

        # Nur ein WECHSEL des Werts gilt als neues Vorhalten.
        #
        # Nach dem Reset denselben Wert erneut auszulösen wäre verlockend —
        # damit wäre eine zweite Ladung derselben Karte sichtbar. Es ist aber
        # falsch: bei einem haftenden Sensor ist "alte Karte klebt noch" nicht
        # von "dieselbe Karte erneut vorgehalten" zu unterscheiden, und dann
        # würde nach JEDER Ladung eine Phantom-Session starten. Eine zweite
        # Ladung derselben Karte wird stattdessen über den Zustandssensor
        # erkannt; die Zuordnung übernimmt der in main.py bewusst erhaltene
        # _latest_rfid.
        if tag != self._value:
            self._value, self._seen_at, self._released = tag, now, False
            return tag

        if not self._released and self.hold_seconds \
                and (now - self._seen_at) >= self.hold_seconds:
            self._released = True
            _LOGGER.debug("Tag-Wert haftet seit %.1fs — Rückfall auf 'kein Tag' selbst erzeugt",
                          now - self._seen_at)
            return RELEASED
        return None
