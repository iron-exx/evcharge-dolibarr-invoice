"""Parser für das Transaktions-Log der Alfen-Wallbox.

Das Log liefert je Ladevorgang zwei Zeilen, `txstart` und `txstop`, jeweils mit
Zeitpunkt, Zählerstand und Karte. Aus einem Paar wird ein abgeschlossener
Ladevorgang.

WARNUNG ZUM FORMAT: Es ist aus dem Parser der HACS-Integration
(leeyuentuen/alfen_wallbox) rekonstruiert und NICHT an echter Hardware
verifiziert. Darum wird hier musterbasiert gelesen — Zeitpunkt, kWh und Karte
werden per Muster gesucht statt an festen Feldpositionen. Verschiebt ein
Firmware-Update die Reihenfolge oder kommt ein Feld hinzu, liefert der Parser
weiterhin das Richtige oder gar nichts; er liefert nie einen falschen Wert an
der falschen Stelle.

Lieber eine Zeile überspringen als sie falsch deuten: fehlt die Karte oder der
Zählerstand, wird die Zeile verworfen. Ein nicht abgerechneter Vorgang ist
reparierbar, eine falsche Spesenzeile nicht.
"""
import logging
import re
from typing import List, Optional

_LOGGER = logging.getLogger(__name__)

# <lineid>_<n>:txstart … bzw. :txstop …
_LINE_ID = re.compile(r'^\s*(\d+)_\d+\s*:\s*(txstart|txstop)\b', re.IGNORECASE)
# "Socket 1," — der Name endet am Komma
_SOCKET = re.compile(r'\b(Socket\s+\d+)\s*,', re.IGNORECASE)
# 2026-10-01 08:12:34
_WHEN = re.compile(r'(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})')
# 1234.567kWh oder 1234,567kWh
_KWH = re.compile(r'(\d+(?:[.,]\d+)?)\s*kWh', re.IGNORECASE)
# Karte: Hex-Kennung, 4–20 Zeichen
_TAG = re.compile(r'^[0-9A-Fa-f]{4,20}$')


def parse_transaction_line(line) -> Optional[dict]:
    """Eine Logzeile → Ereignis-Dict, oder None wenn sie nicht verwertbar ist."""
    text = str(line or '').strip()
    if not text:
        return None

    head = _LINE_ID.match(text)
    if not head:
        return None
    line_id, kind = int(head.group(1)), head.group(2).lower()

    when = _WHEN.search(text)
    kwh_match = _KWH.search(text)
    socket = _SOCKET.search(text)
    if not (when and kwh_match):
        _LOGGER.debug("Transaktionszeile ohne Zeitpunkt oder Zählerstand übersprungen")
        return None

    # Die Karte steht hinter dem Zählerstand. Von dort aus das erste Feld
    # nehmen, das wie eine Kennung aussieht — statt auf eine feste Position
    # zu setzen, die ein Firmware-Update verschieben könnte.
    tag = None
    for token in text[kwh_match.end():].split():
        candidate = token.strip(',;')
        if _TAG.match(candidate):
            tag = candidate.upper()
            break
    if not tag:
        _LOGGER.debug("Transaktionszeile ohne erkennbare Karte übersprungen")
        return None

    try:
        kwh = float(kwh_match.group(1).replace(',', '.'))
    except ValueError:
        return None

    return {
        'kind': 'start' if kind == 'txstart' else 'stop',
        'line_id': line_id,
        'socket': socket.group(1).replace('  ', ' ') if socket else 'Socket 1',
        'timestamp': f'{when.group(1)}T{when.group(2)}',
        'kwh': kwh,
        'tag': tag,
    }


def parse_transaction_log(lines) -> List[dict]:
    """Logzeilen → abgeschlossene Ladevorgänge, in der Reihenfolge ihres Endes.

    Nur Paare aus Start und Stop werden zurückgegeben. Ein laufender Vorgang
    (Start ohne Stop) fehlt absichtlich: ohne Endzählerstand darf nicht
    abgerechnet werden.
    """
    open_starts = {}        # socket → Startereignis
    sessions = []

    for raw in lines or []:
        event = parse_transaction_line(raw)
        if event is None:
            continue
        socket = event['socket']

        if event['kind'] == 'start':
            if socket in open_starts:
                _LOGGER.info("%s: neuer Start ohne vorherigen Stop — der alte Vorgang "
                             "wird verworfen", socket)
            open_starts[socket] = event
            continue

        start = open_starts.pop(socket, None)
        if start is None:
            _LOGGER.debug("%s: Stop ohne zugehörigen Start übersprungen", socket)
            continue
        if start['tag'] != event['tag']:
            _LOGGER.warning("%s: Start- und Stop-Karte unterschiedlich (%s… / %s…) — "
                            "Vorgang übersprungen", socket, start['tag'][:4], event['tag'][:4])
            continue
        total = round(event['kwh'] - start['kwh'], 3)
        if total < 0:
            _LOGGER.warning("%s: Zählerstand am Ende kleiner als am Anfang "
                            "(%.3f → %.3f) — Vorgang übersprungen",
                            socket, start['kwh'], event['kwh'])
            continue

        sessions.append({
            # Stabil über Neustarts: die Zeilen-IDs der Wallbox plus Steckdose.
            # Darauf stützt sich die Idempotenz beim Import.
            'transaction_id': f"{socket.replace(' ', '')}#{start['line_id']}-{event['line_id']}",
            'socket': socket,
            'tag': event['tag'],
            'start_time': start['timestamp'],
            'end_time': event['timestamp'],
            'start_kwh': start['kwh'],
            'end_kwh': event['kwh'],
            'total_kwh': total,
        })
    return sessions
