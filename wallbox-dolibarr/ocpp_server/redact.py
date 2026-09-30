"""Entfernt RFID-Klartext aus Protokoll-Logs (D15).

Die Bibliotheken `ocpp` und `websockets` loggen ganze OCPP-Nachrichten samt
`idTag`. Ein festes Log-Level allein schützt davor NICHT:

  - `ChargePoint._handle_call` loggt bei jedem Handler-Fehler und bei jedem
    Schema-Verstoß die komplette Nachricht auf ERROR — und ERROR liegt über
    jedem Level, das wir setzen könnten. Genau dieser Pfad ist im Betrieb
    normal (nicht spezifikationstreue Firmware, SQLite-Fehler).
  - Dreht jemand die Logger ausdrücklich hoch (z.B. beim späteren Verdrahten
    der Option `log_level`), käme der Klartext ebenfalls zurück.

Deshalb hängt der Schutz hier am INHALT, nicht am Level.
"""
import logging
import re

# "idTag":"ABC123"  ·  "idTag": "ABC123"  ·  'idTag': 'ABC123'
# Deckt sowohl das JSON der Wallbox als auch das repr() der ocpp-Datenklassen ab.
_ID_TAG = re.compile(r'''(["']idTag["']\s*:\s*)(["'])(.*?)\2''', re.IGNORECASE)
_REDACTED = 'REDACTED'


def redact_id_tags(text: str) -> str:
    """Ersetzt jeden idTag-Wert durch 'REDACTED', lässt den Rest unberührt."""
    return _ID_TAG.sub(lambda m: f'{m.group(1)}{m.group(2)}{_REDACTED}{m.group(2)}', text)


class IdTagRedactingFilter(logging.Filter):
    """Bereinigt die fertig formatierte Meldung, bevor sie einen Handler erreicht."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # Eine kaputte Formatierung darf die Meldung nicht verschlucken —
            # aber dann steht auch kein formatierter idTag darin.
            return True
        if 'idtag' in message.lower():
            record.msg = redact_id_tags(message)
            record.args = ()
        return True


def install(*loggers: logging.Logger) -> None:
    """Hängt den Filter an, ohne ihn doppelt zu installieren."""
    for logger in loggers:
        if not any(isinstance(f, IdTagRedactingFilter) for f in logger.filters):
            logger.addFilter(IdTagRedactingFilter())
